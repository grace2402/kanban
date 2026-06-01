"""
Google Calendar Integration Service
Handles all Google Calendar API operations for Kanban task scheduling.
Token refresh and error handling centralized here.
"""

import os
import re
import requests
from datetime import datetime, timedelta
from functools import wraps

from timezone_utils import TZ_NAME as CALENDAR_TZ, to_gcal_datetime, effective_end


# ── Configuration ──
TZ = CALENDAR_TZ  # Keep for backward compat with templates
APP_URL = os.environ.get('KANBAN_APP_URL', os.environ.get('APP_URL', 'http://localhost:5001'))
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')


# ── Retry decorator for external API calls ──
def retry(max_retries=3, base_delay=1.0, backoff=2.0, exceptions=(requests.RequestException,)):
    """Retry a callable with exponential backoff.

    Google Calendar API is susceptible to transient 5xx / rate-limits.
    This decorator retries on those errors and returns None on final failure.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        import time
                        time.sleep(delay)
                        delay *= backoff
            # All retries exhausted — log and propagate
            print(f"[calendar] {fn.__name__} failed after {max_retries + 1} attempts: {last_exc}")
            return None
        return wrapper
    return decorator


# ── Token Refresh ──
def _refresh_google_token(refresh_token):
    """Refresh Google OAuth access token using stored refresh token."""
    if not refresh_token or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return None
    
    try:
        r = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id': GOOGLE_CLIENT_ID,
                'client_secret': GOOGLE_CLIENT_SECRET,
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token',
            },
            timeout=10
        )
        if r.ok:
            return r.json().get('access_token')
    except Exception as e:
        pass
    return None


# ── Calendar API Client ──
class _CalendarAPI:
    """Thin wrapper around Google Calendar REST API."""
    
    def __init__(self, token):
        self.token = token
        self.headers = {'Authorization': f'Bearer {token}'}
    
    def verify_token(self):
        """Check if the access token is still valid."""
        try:
            r = requests.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers=self.headers, timeout=5
            )
            return r.ok
        except Exception:
            return False
    
    def create_event(self, summary, description='', start_time=None, end_time=None, attendee_emails=None):
        """Create a Google Calendar event. Returns (event_id|None)."""
        
        # Use centralized timezone helpers from timezone_utils.py
        start_iso = to_gcal_datetime(start_time) if start_time else None
        effective_end_val = effective_end(start_time, end_time) if end_time else start_iso
        
        body = {
            'summary': summary or 'Task',
            'description': description or '',
            'start': {'dateTime': start_iso, 'timeZone': TZ},
            'end':   {'dateTime': effective_end_val, 'timeZone': TZ},
        }
        
        if attendee_emails:
            # Handle both string (comma-separated) and list inputs
            if isinstance(attendee_emails, str):
                email_list = [e.strip() for e in attendee_emails.split(',') if e.strip()]
            else:
                email_list = [str(e).strip() for e in attendee_emails if e]
            body['attendees'] = [{'email': e} for e in email_list]
        
        @retry(max_retries=3, base_delay=1.0)
        def _post():
            r = requests.post(
                'https://www.googleapis.com/calendar/v3/calendars/primary/events?sendUpdates=all',
                headers=self.headers, json=body, timeout=15
            )
            return r
        
        try:
            r = _post()
            if r and r.ok:
                return r.json().get('id')
        except Exception as e:
            print(f"[calendar] create_event failed for '{summary}': {e}")
        return False
    
    def delete_event(self, event_id):
        """Delete an existing Google Calendar event. Returns True on success or 410 (already deleted)."""
        
        @retry(max_retries=3, base_delay=1.0)
        def _delete():
            r = requests.delete(
                f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}?sendNotifications=true',
                headers=self.headers, timeout=15
            )
            return r
        
        try:
            r = _delete()
            if r is None:
                return False
            # Google returns HTTP 410 when deleting an already-deleted event.
            # Treat 410 as idempotent success — the event is gone either way.
            return r.ok or r.status_code == 410
        except Exception as e:
            print(f"[calendar] delete_event failed for {event_id}: {e}")
            return False
    
    def update_event(self, event_id, summary=None, start_time=None, end_time=None):
        """Update an existing Google Calendar event."""
        body = {}
        if summary:   body['summary'] = summary
        # Use centralized to_gcal_datetime (same logic as create_event)
        if start_time: body['start'] = {'dateTime': to_gcal_datetime(start_time), 'timeZone': TZ}
        if end_time:   body['end']   = {'dateTime': to_gcal_datetime(end_time), 'timeZone': TZ}
        
        @retry(max_retries=3, base_delay=1.0)
        def _put():
            r = requests.put(
                f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}',
                headers=self.headers, json=body, timeout=15
            )
            return r
        
        try:
            r = _put()
            if r is None:
                return False
            return r.ok
        except Exception as e:
            print(f"[calendar] update_event failed for {event_id}: {e}")
            return False


# ── Public API ──

def get_calendar_service(access_token, google_id=None):
    """Create a CalendarAPI client from an access token.
    
    Automatically tries to refresh the token if verify_token fails.
    If google_id is provided, looks up refresh_token from kanban_users table.
    Also checks session['oauth_refresh_token'] as fallback.
    """
    if not access_token:
        return None
    
    cal = _CalendarAPI(access_token)
    # Verify token validity; try refresh if needed  
    if cal.verify_token():
        return cal
    
    # Token expired — attempt auto-refresh
    import os as _os
    from sqlalchemy import create_engine as _ce
    refresh_token = None
    
    # 1. Try session refresh token first (set by _get_user_token_from_db)
    try:
        from flask import session as _flask_session
        refresh_token = _flask_session.get('oauth_refresh_token', '') or None
    except RuntimeError:
        pass  # Outside request context
    
    # 2. Try kanban_users DB table if google_id provided
    if not refresh_token and google_id:
        try:
            db_conn = _ce(os.environ.get('DATABASE_URL', '')).raw_connection()
            db_cur = db_conn.cursor()
            db_cur.execute(
                "SELECT oauth_refresh_token FROM kanban_users WHERE google_id=%s LIMIT 1", (google_id,)
            )
            row = db_cur.fetchone()
            if row and row[0]:
                refresh_token = row[0]
            db_cur.close()
            db_conn.close()
        except Exception:
            pass
    
    # 3. Last resort: try any user's refresh token (for _cleanup_calendar_on_delete)
    if not refresh_token:
        try:
            db_conn = _ce(_os.environ.get('DATABASE_URL', '')).raw_connection()
            db_cur = db_conn.cursor()
            db_cur.execute(
                "SELECT oauth_refresh_token FROM kanban_users WHERE oauth_refresh_token IS NOT NULL LIMIT 1"
            )
            row = db_cur.fetchone()
            if row and row[0]:
                refresh_token = row[0]
            db_cur.close()
            db_conn.close()
        except Exception:
            pass
    
    if not refresh_token:
        return None
    
    # Refresh the token
    new_access_token = _refresh_google_token(refresh_token)
    if not new_access_token:
        return None
    
    # Verify the refreshed token works
    cal2 = _CalendarAPI(new_access_token)
    if cal2.verify_token():
        # Update session with fresh token if in request context
        try:
            from flask import session as _flask_session
            _flask_session['oauth_access_token'] = new_access_token
        except RuntimeError:
            pass
        return cal2
    
    return None  # All refresh attempts failed


def create_event_with_all_attendees(title, description, start_time, end_time, assignee_emails):
    """Stub: actual event creation handled in app.py adapter _notify_and_calendar_sync."""
    return None


def _find_and_delete_calendar_events(task_id):
    """Find and delete calendar events stored in this task's description."""
    try:
        from flask import session as flask_session
        token = flask_session.get('oauth_access_token')
        if not token:
            return 0
        
        cal = get_calendar_service(token)
        if not cal:
            return 0
    except ImportError:
        return 0
    
    deleted_count = 0
    try:
        from sqlalchemy import create_engine as _ce
        from flask import current_app as ca
        db_uri = ca.config['SQLALCHEMY_DATABASE_URI'] if hasattr(ca, 'config') else os.environ.get('DATABASE_URL', '')
        conn = _ce(db_uri).raw_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT description FROM kanban_tasks WHERE id=%s", (task_id,))
            row = cur.fetchone()
            if not row or not row[0]:
                return 0
            
            import re as _re
            event_ids = _re.findall(r'\[CALENDAR:([a-zA-Z0-9_-]+)\]', str(row[0]))
            
            for eid in event_ids:
                if cal.delete_event(eid):
                    deleted_count += 1
            
            # Clean markers from description
            if event_ids:
                cleaned = _re.sub(r'\[CALENDAR:[a-zA-Z0-9_-]+\]', '', str(row[0])).strip()
                cur.execute("UPDATE kanban_tasks SET description=%s WHERE id=%s", (cleaned, task_id))
                conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        print(f"[calendar] Cleanup DB error for task {task_id}: {e}")
    
    return deleted_count

    if not access_token:
        return None
    
    cal = _CalendarAPI(access_token)
    # Verify token validity; try refresh if needed
    if not cal.verify_token():
        return None  # Caller should handle refresh via DB-stored tokens
    return cal


def create_task_calendar_event(task_id, title, description, assignee_emails, start_time, end_time):
    """Create a single Google Calendar event with ALL assignees as attendees.
    
    Returns (event_id|None). Stores event ID in task description for future updates.
    """
    if not assignee_emails or not start_time:
        return None
    
    # Build attendee list from comma-separated string or list
    if isinstance(assignee_emails, str):
        attendees = [e.strip() for e in str(assignee_emails).split(',') if e.strip()]
    else:
        attendees = assignee_emails
    
    event_id = create_event_with_tokens(attendees, title, description, start_time, end_time)
    
    # Store event ID in description (for future updates/deletes)
    if event_id and isinstance(assignee_emails, str):
        meta_marker = f'[CALENDAR:{event_id}]'
        # This is handled by the caller who has DB access; return marker to prepend
    
    return event_id


def create_event_with_tokens(attendees, title, description, start_time, end_time):
    """Create calendar event using token from session (legacy) or try refresh."""
    # Import here to avoid circular imports when app not fully initialized
    import sys
    if 'app' in sys.modules:
        from flask import current_app as app
    
    # Token management is now handled by the caller passing tokens explicitly.
    # For backward compatibility during migration, this function accepts no token parameter
    # and relies on the caller to have set up session tokens or DB tokens.
    
    return None  # See create_event_with_db_tokens below


def _get_effective_token(session):
    """Get effective access token from Flask session with refresh fallback."""
    try:
        from flask import session as flask_session
    except ImportError:
        return None
    
    token = flask_session.get('oauth_access_token')
    if not token:
        return None
    
    # Try to verify, then refresh if needed
    cal = _CalendarAPI(token)
    if cal.verify_token():
        return token
    
    refresh_token = flask_session.get('oauth_refresh_token', '')
    new_token = _refresh_google_token(refresh_token)
    if new_token:
        try:
            from flask import session as flask_session
            flask_session['oauth_access_token'] = new_token
        except RuntimeError:
            pass  # Outside request context
        return new_token
    
    return None


def create_event_with_db_tokens(db_uri, google_id, task_id, title, description, assignee_emails, start_time, end_time):
    """Create calendar event using token stored in kanban_users DB table.
    
    Args:
        db_uri: Database connection string (replaces db_session for consistency)
        google_id: User's Google ID (for DB lookup)
        task_id: Kanban task ID
        title: Task title for event summary
        description: Task description
        assignee_emails: Comma-separated string of attendee emails
        start_time, end_time: ISO format timestamps
    
    Returns:
        New event ID string or None on failure.
    """
    if not google_id:
        return None
    
    try:
        from sqlalchemy import create_engine as _ce
        # Fetch user's stored token from DB using raw connection (matches app.py pattern)
        conn = _ce(db_uri).raw_connection()
        cur = conn.cursor()
        try:
            row = cur.execute(
                "SELECT oauth_access_token, oauth_refresh_token FROM kanban_users WHERE google_id=:gid LIMIT 1",
                {'gid': google_id}
            ).fetchone()
            
            if not row or not row[0]:
                return None
            
            access_token = row[0]
            refresh_token = row[1] or ''
        finally:
            cur.close()
        
        # Verify & refresh token
        cal = _CalendarAPI(access_token)
        if not cal.verify_token():
            new_token = _refresh_google_token(refresh_token)
            if new_token:
                access_token = new_token
                try:
                    conn2 = _ce(db_uri).raw_connection()
                    cur2 = conn2.cursor()
                    cur2.execute(
                        "UPDATE kanban_users SET oauth_access_token=:t WHERE google_id=:gid",
                        {'t': new_token, 'gid': google_id}
                    )
                    conn2.commit()
                except Exception:
                    pass
        
        cal = _CalendarAPI(access_token)
        event_id = cal.create_event(
            summary=f"📋 {title or f'Task #{task_id}'}",
            description=f"{description}\nKanban URL: {APP_URL}/#/detail/{task_id}",
            start_time=start_time,
            end_time=end_time,
            attendee_emails=assignee_emails if isinstance(assignee_emails, list) else assignee_emails
        )
        
        return event_id
        
    except Exception as e:
        app.logger.error("create_event_with_db_tokens failed for task %s: %s", task_id, str(e))
        return None


def delete_calendar_events_for_task(db_session, task_id):
    """Find and delete all Google Calendar events associated with a task.
    
    Searches for [CALENDAR:xxx] markers in the task description.
    Returns count of deleted events.
    """
    try:
        row = db_session.execute(
            __import__('sqlalchemy').text('SELECT title, description FROM kanban_tasks WHERE id=:tid LIMIT 1'),
            {'tid': task_id}
        ).fetchone()
        
        if not row or not row[1]:
            return 0
        
        event_ids = re.findall(r'\[CALENDAR:([a-zA-Z0-9_-]+)\]', str(row[1]))
    except Exception as e:
        print(f"Calendar cleanup DB error for task {task_id}: {e}")
        return 0
    
    if not event_ids:
        return 0
    
    # Need to get a token to delete events — use first user who has one
    try:
        user_row = db_session.execute(
            __import__('sqlalchemy').text('SELECT oauth_access_token FROM kanban_users WHERE oauth_access_token IS NOT NULL LIMIT 1')
        ).fetchone()
        
        if not user_row or not user_row[0]:
            return 0
        
        cal = _CalendarAPI(user_row[0])
    except Exception:
        return 0
    
    deleted_count = 0
    for eid in event_ids:
        if cal.delete_event(eid):
            deleted_count += 1
    
    # Clean the markers from task description
    try:
        db_session.execute(
            __import__('sqlalchemy').text(
                "UPDATE kanban_tasks SET description = regexp_replace(description, '\\[CALENDAR:[a-zA-Z0-9_-]+\\]', '', 'g') WHERE id=:tid"
            ),
            {'tid': task_id}
        )
        db_session.commit()
    except Exception:
        pass
    
    return deleted_count


def resync_task_schedule(google_id, task_id):
    """When task schedule changes: delete old events and create new ones.
    
    Also updates kanban_calendar_events table to keep it in sync with Google Calendar.
    
    Args:
        google_id: Current user's Google ID for token lookup
        task_id: Task being updated
    
    Returns True if resync succeeded, False otherwise.
    """
    try:
        from sqlalchemy import create_engine as _ce
        db_uri = os.environ.get('DATABASE_URL', '') or os.environ.get('SQLALCHEMY_DATABASE_URI', '')
        
        # Get current task data via raw connection (matches app.py pattern)
        conn = _ce(db_uri).raw_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT title, description, start_time, end_time, assignee_email FROM kanban_tasks WHERE id=:tid LIMIT 1",
                {'tid': task_id}
            )
            row = cur.fetchone()
            
            if not row or not row[2]:  # No start_time
                return False
            
            title = row[0]
            desc = str(row[1]) if row[1] else ''
            st, et = row[2], row[3]
            assignee_emails_str = str(row[4]) if row[4] else None
            
            if not assignee_emails_str:
                return False
            
        finally:
            cur.close()
        
        # Step 1: Clean up old records from kanban_calendar_events table FIRST
        conn2 = _ce(db_uri).raw_connection()
        cur2 = conn2.cursor()
        try:
            cur2.execute("DELETE FROM kanban_calendar_events WHERE task_id=:tid", {'tid': task_id})
            conn2.commit()
            
            # Step 2: Delete old [CALENDAR:] markers from description  
            cur2.execute(
                "UPDATE kanban_tasks SET description = regexp_replace(description, '\\[CALENDAR:[a-zA-Z0-9_-]+\\]', '', 'g') WHERE id=:tid",
                {'tid': task_id}
            )
            conn2.commit()
        finally:
            cur2.close()
        
        # Step 3: Create new event with all attendees  
        new_event_id = create_event_with_db_tokens(
            db_uri=db_uri,
            google_id=google_id,
            task_id=task_id,
            title=title,
            description=desc,
            assignee_emails=assignee_emails_str,
            start_time=st.isoformat() if hasattr(st, 'isoformat') else str(st),
            end_time=et.isoformat() if hasattr(et, 'isoformat') and et else (st.isoformat() if hasattr(st, 'isoformat') else str(st))
        )
        
        # Step 4: Store new event ID in kanban_calendar_events table
        if new_event_id:
            conn3 = _ce(db_uri).raw_connection()
            cur3 = conn3.cursor()
            try:
                cur3.execute(
                    "INSERT INTO kanban_calendar_events (task_id, calendar_event_id, summary) VALUES (:tid,:eid,:summary)",
                    {'tid': task_id, 'eid': new_event_id, 'summary': title or ''}
                )
                conn3.commit()
            finally:
                cur3.close()
                conn3.close()
        
        return True
        
    except Exception as e:
        print(f"[calendar] Schedule resync failed for task {task_id}: {e}")
        return False


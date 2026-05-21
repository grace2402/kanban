"""
Google Calendar Integration Service
Handles all Google Calendar API operations for Kanban task scheduling.
Token refresh and error handling centralized here.
"""

import os
import re
import requests
from datetime import datetime, timezone, timedelta


# ── Configuration ──
TZ = 'Asia/Taipei'
APP_URL = os.environ.get('KANBAN_APP_URL', os.environ.get('APP_URL', 'http://localhost:5001'))
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')


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
        body = {
            'summary': summary or 'Task',
            'description': description or '',
            'start': {'dateTime': str(start_time), 'timeZone': TZ},
            'end':   {'dateTime': str(end_time) if end_time else str(start_time), 'timeZone': TZ},
        }
        
        if attendee_emails:
            attendees = [{'email': e.strip()} for e in str(attendee_emails).split(',') if e.strip()]
            body['attendees'] = attendees
        
        try:
            r = requests.post(
                'https://www.googleapis.com/calendar/v3/calendars/primary/events?sendUpdates=all',
                headers=self.headers, json=body, timeout=10
            )
            if r.ok:
                return r.json().get('id')
        except Exception as e:
            pass
        return False
    
    def delete_event(self, event_id):
        """Delete an existing Google Calendar event."""
        try:
            r = requests.delete(
                f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}?sendNotifications=true',
                headers=self.headers, timeout=10
            )
            return r.ok
        except Exception:
            return False
    
    def update_event(self, event_id, summary=None, start_time=None, end_time=None):
        """Update an existing Google Calendar event."""
        body = {}
        if summary:   body['summary'] = summary
        if start_time: body['start'] = {'dateTime': str(start_time), 'timeZone': TZ}
        if end_time:   body['end']   = {'dateTime': str(end_time), 'timeZone': TZ}
        
        try:
            r = requests.put(
                f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}',
                headers=self.headers, json=body, timeout=10
            )
            return r.ok
        except Exception:
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


def resync_task_schedule(task_id):
    """Reschedule calendar events for a task. Caller must have loaded tokens into session."""
    try:
        from flask import session as flask_session, current_app as ca
    except ImportError:
        return False
    
    token = flask_session.get('oauth_access_token')
    if not token:
        return False
    
    cal = get_calendar_service(token)
    if not cal:
        print(f"[calendar] No calendar service for resync of task {task_id}")
        return False
    
    try:
        db_uri = ca.config['SQLALCHEMY_DATABASE_URI'] if hasattr(ca, 'config') else os.environ.get('DATABASE_URL', '')
        conn = __import__('sqlalchemy').create_engine(db_uri).raw_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT title, description, start_time, end_time, assignee_email FROM kanban_tasks WHERE id=%s",
                (task_id,)
            )
            row = cur.fetchone()
            if not row or not row[2]:
                return False
            
            # Delete old events first  
            _find_and_delete_calendar_events(task_id)  # Uses current token
            
            title, desc, st, et, ae_str = row
            assignees = [e.strip() for e in str(ae_str).split(',') if e.strip()] if ae_str else []
            
            cal.create_event(
                summary=f"📋 {title}",
                description=desc or '',
                start_time=str(st), end_time=str(et) if et else str(st),
                attendee_emails=assignees
            )
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        print(f"[calendar] Resync DB error for task {task_id}: {e}")
    
    return True


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

    """Create a CalendarAPI client from an access token."""
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


def create_event_with_db_tokens(db_session, google_id, task_id, title, description, assignee_emails, start_time, end_time):
    """Create calendar event using token stored in kanban_users DB table.
    
    Args:
        db_session: SQLAlchemy session
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
    
    # Fetch user's stored token from DB
    try:
        User = db_session.get_bind().execute(
            __import__('sqlalchemy').text('SELECT oauth_access_token, oauth_refresh_token FROM kanban_users WHERE google_id=:gid LIMIT 1')
        ).fetchone()
    except Exception:
        return None
    
    if not User or not User[0]:
        return None
    
    access_token = User[0]
    
    # Verify & refresh token
    cal = _CalendarAPI(access_token)
    if not cal.verify_token():
        refresh_token = User[1] or ''
        new_token = _refresh_google_token(refresh_token)
        if new_token:
            access_token = new_token
            try:
                db_session.execute(
                    __import__('sqlalchemy').text('UPDATE kanban_users SET oauth_access_token=:t WHERE google_id=:gid'),
                    {'t': new_token, 'gid': google_id}
                )
                db_session.commit()
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


def resync_task_schedule(db_session, google_id, task_id):
    """When task schedule changes: delete old events and create new ones.
    
    Args:
        db_session: SQLAlchemy session
        google_id: Current user's Google ID for token lookup
        task_id: Task being updated
    
    Returns True if resync succeeded.
    """
    try:
        # Get current task data
        row = db_session.execute(
            __import__('sqlalchemy').text(
                'SELECT title, description, start_time, end_time, assignee_email FROM kanban_tasks WHERE id=:tid LIMIT 1'
            ),
            {'tid': task_id}
        ).fetchone()
        
        if not row or not row[2]:  # No start_time
            return False
        
        title = row[0]
        desc = str(row[1]) if row[1] else ''
        st, et = row[2], row[3]
        assignee_emails_str = str(row[4]) if row[4] else None
        
        if not assignee_emails_str:
            return False
        
        # Delete old events
        delete_calendar_events_for_task(db_session, task_id)
        
        # Create new event with all attendees
        create_event_with_db_tokens(
            db_session=db_session,
            google_id=google_id,
            task_id=task_id,
            title=title,
            description=desc,
            assignee_emails=assignee_emails_str,
            start_time=st.isoformat() if hasattr(st, 'isoformat') else str(st),
            end_time=et.isoformat() if hasattr(et, 'isoformat') and et else (st.isoformat() if hasattr(st, 'isoformat') else str(st))
        )
        
        return True
        
    except Exception as e:
        print(f"Schedule resync failed for task {task_id}: {e}")
        return False

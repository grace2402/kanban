"""
SMTP Email Service Module — Unified Entry Point
Handles all Kanban notification emails: task creation, schedule changes, etc.

Replaces duplicated code that was previously embedded in app.py (~300 lines).
Features:
  - Rich HTML email templates with Google Calendar links
  - Multi-recipient support (everyone sees everyone like a meeting invite)
  - CC support for notification emails
"""

import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone


# ── SMTP Configuration ──
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SENDER_EMAIL = os.environ.get('MAIL_USER', '')
SENDER_PASSWORD = os.environ.get('MAIL_PASSWORD', '')

APP_NAME = os.environ.get('APP_NAME', 'Kanban Board')
APP_URL = os.environ.get('KANBAN_APP_URL', os.environ.get('APP_URL', 'http://localhost:5001'))


def _is_configured():
    """Check if SMTP is properly configured."""
    return bool(SENDER_EMAIL and SENDER_PASSWORD)


# ── Time Formatting Helpers ──

def fmt_time(ts):
    """Format ISO timestamp for display in email."""
    if not ts:
        return '—'
    try:
        dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        return dt.strftime('%Y/%m/%d %H:%M') + ' (UTC+8)'
    except Exception:
        return str(ts)


def fmt_time_display(ts_str, tz_label='UTC+8'):
    """Format ISO timestamp for display in email."""
    if not ts_str:
        return '(未設定)'
    try:
        dt = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
        return dt.strftime('%Y/%m/%d %H:%M') + f' ({tz_label})'
    except (ValueError, AttributeError):
        return str(ts_str)


# ── HTML Template Builder ──

def build_task_notification_html(task_id, title, description, priority, start_time, end_time, assignee_emails, schedule_change=False):
    """Build rich HTML email for task notification with Google Calendar link.
    
    Args:
        task_id: Kanban task ID
        title: Task title
        description: Task description
        priority: 'high', 'medium', or 'low'
        start_time, end_time: ISO timestamps (can be None)
        assignee_emails: Comma-separated string of assignee emails
        schedule_change: If True, add "schedule changed" banner at top
    
    Returns HTML string.
    """
    priority_map = {'high': '🔴 高', 'medium': '🟡 中', 'low': '🟢 低'}
    priority_text = priority_map.get(priority, priority) if priority else '未設定'
    
    start_display = fmt_time(start_time)
    end_display = fmt_time(end_time)
    time_info = f"{start_display} ~ {end_display}" if start_time or end_time else "未設定排程"

    # Google Calendar link (TEMPLATE action lets user review before adding)
    cal_url = ""
    
    def _parse_naive_dt(ts):
        """Parse ISO timestamp and return a UTC naive datetime for Google Calendar URL.
        
        CRITICAL: Google Calendar template URLs (dates parameter) use 'Z' suffix = UTC.
        If we convert to Taipei local time and append Z, Google reads it as UTC → 8h off!
        
        So we must convert BACK to UTC before stripping tzinfo for the URL format.
        
        Example: DB has "2026-06-01T09:00+08:00" (Taipei 9am)
          psycopg2 returns: datetime(2026,5,31,17,0,tzinfo=UTC) — same instant in UTC
          .astimezone(timezone.utc).replace(tzinfo=None) → datetime(2026,5,31,17,0) naive
          Formatted as "20260531T170000Z" → Google reads: UTC 17:00 = Taipei June 1 9am ✓
        """
        if ts is None:
            return None
        s = str(ts)
        # Replace 'Z' with '+00:00' for fromisoformat compatibility
        s = s.replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        
        if dt.tzinfo is not None:
            # Has timezone → convert to UTC first, THEN strip offset.
            utc_dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            # Already naive — assume it's already in the right local time.
            # For Google URL with Z suffix, this is treated as UTC by definition.
            utc_dt = dt
        
        return utc_dt
    
    if start_time:
        try:
            dt_start = _parse_naive_dt(start_time)
            
            # Determine local (Taipei) date from original timestamp string
            def _local_date(ts):
                """Extract the LOCAL (Taipei) date from an ISO timestamp.
                
                CRITICAL: Timestamps may come as UTC-datetime.isoformat() or 
                +08:00 offset strings. We must convert to Taipei timezone FIRST,
                then extract the date — NOT just call .date() on a UTC datetime.
                """
                s = str(ts).replace('Z', '+00:00')
                dt = datetime.fromisoformat(s)
                # Convert to Taipei timezone first, THEN extract date
                if dt.tzinfo is not None:
                    return dt.astimezone(timezone(timedelta(hours=8))).date()
                else:
                    # Already naive — assume it's in local (Taipei) time
                    return dt.date()
            
            if end_time and end_time != start_time:
                local_end_date = _local_date(end_time)
                local_start_date = _local_date(start_time)
                dt_end = _parse_naive_dt(end_time)
                
                if local_end_date > local_start_date:
                    # Multi-day task: preserve actual start/end times from input
                    # Use _parse_naive_dt which converts to UTC-naive for Google URL
                    cal_dates = f"{dt_start.strftime('%Y%m%dT%H%M%SZ')}/{(dt_end).strftime('%Y%m%dT%H%M%S')}Z"
                else:
                    # Same-day task → end_time is a specific time on the same day. 
                    # Do NOT add 1 day here — that would push it to the next calendar day!
                    cal_dates = f"{dt_start.strftime('%Y%m%dT%H%M%SZ')}/{(dt_end).strftime('%Y%m%dT%H%M%S')}Z"
            else:
                # Single-point time → treat as full day (add 1 day for end)
                cal_dates = f"{dt_start.strftime('%Y%m%dT%H%M%SZ')}/{(dt_start + timedelta(days=1)).strftime('%Y%m%dT000000Z')}"
        except Exception:
            pass
        
        if 'cal_dates' in dir():  # Only set if no exception above
            from urllib.parse import quote
            task_title_for_cal = title or f'Task #{task_id}'
            kanban_url = f"{APP_URL}/#/detail/{task_id}"
            
            params = [
                ('action', 'TEMPLATE'),
                ('text', task_title_for_cal),
                ('dates', cal_dates),
                ('details', description or ''),
                ('location', kanban_url),
            ]
            
            for att in [e.strip() for e in str(assignee_emails).split(',') if e.strip()]:
                params.append(('add', att))
            
            query = '&'.join(f'{quote(k)}={quote(v)}' for k, v in params)
            cal_url = f"https://calendar.google.com/calendar/render?{query}"

    assignees_str = '<br>'.join(e.strip() for e in str(assignee_emails).split(',') if e.strip())
    
    schedule_banner = ''
    if schedule_change:
        schedule_banner = '<div style="background: #fff3cd; padding: 8px 16px; border-radius: 4px; margin-bottom: -1px; text-align: center;"><b>⚠️ 時程已異動</b></div>'
    
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: auto;">
        {schedule_banner}
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #4A90D9, #357ABD); padding: 20px; border-radius: 8px 8px 0 0; color: white;">
            <h2 style="margin: 0; font-size: 20px;">📋 {title or '新任務指派'}</h2>
        </div>
        
        <!-- Task Details -->
        <div style="border: 1px solid #e0e0e0; border-top: none; padding: 16px;">
            <table style="width: 100%; border-collapse: collapse;">
                <tr><td style="padding: 8px; color: #666; width: 90px;"><b>任務 ID</b></td>
                    <td style="padding: 8px;">{task_id}</td></tr>
                <tr><td style="padding: 8px; color: #666;"><b>優先級</b></td>
                    <td style="padding: 8px;">{priority_text}</td></tr>
                <tr><td style="padding: 8px; color: #666;"><b>排程時間</b></td>
                    <td style="padding: 8px;">{time_info}</td></tr>
                {'<tr><td style="padding: 8px; color: #666;"><b>指派給</b></td>'
                 f'<td style="padding: 8px;">{assignees_str}</td></tr>' if assignees_str else ''}
            </table>
            
            {'<div style="margin-top: 12px; padding: 12px; background: #f9f9f9; border-radius: 6px;">'
             f'<b style="color: #666;">描述</b>'
             f'<p style="margin: 4px 0 0; white-space: pre-wrap;">{description or "無"}</p></div>' if description else ''}
        </div>
        
        <!-- Action Buttons -->
        <div style="padding: 16px; text-align: center;">
            {'<a href="' + cal_url + '" target="_blank" '
             f'style="display: inline-block; padding: 10px 24px; background: #4CAF50; color: white; '
             'text-decoration: none; border-radius: 6px; margin-right: 8px;">'
             '📅 加入 Google Calendar</a><br>' if cal_url else ''}
            <a href="{APP_URL}/#/detail/{task_id}" target="_blank" 
               style="display: inline-block; padding: 10px 24px; background: #4A90D9; color: white;
                      text-decoration: none; border-radius: 6px;">
              🔗 查看任務詳情</a>
        </div>
        
        <!-- Footer -->
        <div style="padding: 12px 16px; background: #f4f4f4; border-top: 1px solid #e0e0e0;
                    border-radius: 0 0 8px 8px; text-align: center; color: #999; font-size: 12px;">
            此郵件由 {APP_NAME} 系統自動發送
        </div>
    </div>"""
    
    return html


# ── Email Sending Functions ──

def send_kanban_email(to_email, subject, body_html, cc_list=None):
    """Send HTML email via SMTP with error handling. Supports CC for batch notifications."""
    if not _is_configured():
        return False
    
    try:
        msg = MIMEText(body_html, 'html', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = SENDER_EMAIL
        
        # Primary recipient(s) — support list or comma-separated string
        if isinstance(to_email, list):
            recipients = [e.strip() for e in to_email if e.strip()]
            primary_recipient = recipients[0] if recipients else ''
            msg['To'] = ', '.join(recipients)
        elif ',' in str(to_email):
            recipients = [e.strip() for e in str(to_email).split(',') if e.strip()]
            primary_recipient = recipients[0]
            msg['To'] = ', '.join(recipients)
        else:
            recipients = [to_email]
            primary_recipient = to_email
            msg['To'] = to_email
        
        # CC all other assignees
        if cc_list:
            cc_emails = [e.strip() for e in cc_list if e.strip()]
            if cc_emails:
                msg['Cc'] = ', '.join(cc_emails)
        
        # Build final list of all recipients for sendmail
        all_recipients = list(set(recipients + (cc_list or [])))
        all_recipients = [e.strip() for e in all_recipients if e.strip()]
        
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, all_recipients, msg.as_string())
        
        return True
        
    except Exception as e:
        print(f"SMTP 發送失敗: {e}")
        return False


# ── Notification Entry Points ──

def notify_task_created(task_id, creator_email, assignee_email, title='', description=None, priority=None, start_time=None, end_time=None):
    """Notify when a task is created and assigned.
    
    Sends ONE email to ALL assignees (not CC), so everyone sees each other as attendees — like a meeting invitation.
    """
    if not _is_configured():
        return False
    
    subject = f"📋 [Kanban Board] {title or '新任務指派'}"
    
    # Collect all unique assignee emails
    assignees_raw = [e.strip() for e in str(assignee_email).split(',') if e.strip()]
    assignees = list(set(assignees_raw))
    
    body_html = build_task_notification_html(
        task_id, title, description, priority, 
        start_time, end_time, ','.join(assignees)
    )
    
    # Send ONE email with ALL assignees as recipients (everyone sees everyone like a meeting invite)
    return send_kanban_email(list(assignees), subject, body_html)


def notify_task_updated(task_id, creator_email, assignee_email, title='', description=None, priority=None, start_time=None, end_time=None):
    """Notify when task schedule changes. Sends to creator with all assignees in CC."""
    if not _is_configured():
        return False
    
    subject = f"📅 [Kanban Board] 任務時程異動: {title or 'Task #' + str(task_id)}"
    
    # Collect all unique assignee emails
    assignees_raw = [e.strip() for e in str(assignee_email).split(',') if e.strip()]
    assignees = list(set(assignees_raw))
    assignees.sort()
    
    body_html = build_task_notification_html(
        task_id, title, description, priority,
        start_time, end_time, ','.join(assignees) or creator_email,
        schedule_change=True
    )
    
    return send_kanban_email(creator_email, subject, body_html, cc_list=assignees if assignees else None)


# ── Legacy Compatibility ──

# Keep these for backward compatibility with old app.py imports
send_notification = notify_task_created  # Old name → new function
build_task_notification_html = build_task_notification_html
notify_task_creator = notify_task_updated  # Old name → new function


def notify_task_deleted(task_id, creator_email, assignee_email='', title='', calendar_event_id=''):
    """Notify assignees that a task was deleted.
    
    If the task had Google Calendar events attached (calendar_event_id), includes a button to delete them.
    """
    if not _is_configured() or not assignee_email:
        return False
    
    print(f"[DELETE EMAIL] task={task_id} | calendar_event_id={repr(calendar_event_id)} | len={len(str(calendar_event_id))}")
    
    try:
        recipients = [e.strip() for e in str(assignee_email).split(',') if e.strip()]
        
        # Build calendar delete button URL (requires login to kanban board)
        cal_delete_url = ''
        if calendar_event_id and len(str(calendar_event_id)) > 0:
            cal_delete_url = f'{APP_URL}/api/calendar/delete-event?task_id={task_id}&calendar_event_id={calendar_event_id}'
        
        cal_action_html = ''
        if cal_delete_url:
            cal_action_html = ('<div style="margin-top:16px;text-align:center;">'
                '<a href="' + cal_delete_url + '" target="_blank" '
                'style="display:inline-block;padding:8px 20px;background:#ef4444;color:white;'
                'text-decoration:none;border-radius:6px;font-size:13px;font-weight:500;">'
                '📅 刪除 Google Calendar 事件</a>'
                '<p style="font-size:11px;color:#991b1b;margin-top:6px;">點擊後將移除此任務關聯的行事曆活動</p></div>')
        
        html_body = ('<div style="font-family:sans-serif;max-width:600px;margin:auto;">'
            '<table cellpadding="0" cellspacing="0" width="100%" style="border-radius:8px;'
            'overflow:hidden;border-collapse:separate;background:#ffffff;border:1px solid #e2e8f0;">'
            '<tr><td style="padding:24px 28px 16px 28px;">'
            '<div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">'
            '<span style="font-size:32px">🗑️</span>'
            f'<h2 style="margin:0;color:#dc2626;font-size:18px;">任務已刪除</h2></div>'
            f'<table cellpadding="0" cellspacing="0" width="100%" style="background:#fef2f2;border-radius:6px;'
            'padding:14px 18px;margin-bottom:16px;border-collapse:separate;">'
            '<tr><td style="font-size:13px;color:#7c2d12;font-weight:bold;width:90px;vertical-align:top;padding-right:8px;">任務編號</td>'
            f'<td style="color:#450a0a;font-family:monospace;font-size:13px;">{task_id}</td></tr>'
            '<tr><td style="font-size:13px;color:#7c2d12;font-weight:bold;padding-top:8px;padding-right:8px;vertical-align:top;">任務標題</td>'
            f'<td style="color:#450a0a;font-size:14px;font-weight:600;padding-top:8px;">{title}</td></tr>'
            '<tr><td style="font-size:13px;color:#7c2d12;font-weight:bold;padding-top:8px;padding-right:8px;vertical-align:top;">刪除者</td>'
            f'<td style="color:#450a0a;font-size:13px;padding-top:8px;">{creator_email}</td></tr>'
            '<tr><td colspan="2" style="font-size:13px;color:#92400e;padding-top:14px;border-top:1px solid #fed7aa;"><strong>⚠️ 此任務已從看板中移除</strong></td></tr>'
            '</table>'
            + cal_action_html +
            '</td></tr>'
            '<tr><td style="padding:0 28px 24px 28px;text-align:center;border-top:1px solid #f1f5f9;">'
            f'<p style="color:#64748b;font-size:13px;margin-bottom:4px;">看板系統 — {APP_URL}</p></td></tr>'
            '</table></div>')

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'🗑️ 任務已刪除：{title} (#{task_id})'
        msg['From'] = SENDER_EMAIL
        msg['To'] = ', '.join(recipients)
        html_part = MIMEText(html_body, 'html', 'utf-8')
        msg.attach(html_part)
        
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipients, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Delete notification failed for task {task_id}: {e}")
        return False

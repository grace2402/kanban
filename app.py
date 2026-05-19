from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session
import os
import re
from datetime import datetime
from sqlalchemy import create_engine, text

import json as json_lib
import requests

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'kanban-secret-key-change-me')
# App metadata for emails
APP_NAME = os.environ.get('APP_NAME', 'Kanban Board')
APP_URL = os.environ.get('KANBAN_APP_URL', 'http://localhost:5001')
# Fix DB password — the env var also has *** but Postgres accepts it for teaasia user
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    "postgresql://teaasia:teaasia@teaasia-db:5432/teaasia"
)

@app.context_processor
def inject_js_email():
    """Make currentUserEmail available in all templates for JS."""
    email = session.get('user_email', '')
    return {'currentUserEmail': email}

# Google OAuth 設定
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID', '')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET', '')
app.config['GOOGLE_DISCOVERY_URL'] = "https://accounts.google.com/.well-known/openid-configuration"
# app.config['ALLOWED_DOMAINS'] = ['nextdrive.io']  # Development: allow all emails
app.config['ALLOWED_DOMAINS'] = []  # Empty = no domain restriction (for localhost testing)

# Authlib OAuth initialization (after app created)
from authlib.integrations.flask_client import OAuth
oauth_obj = OAuth(app) if app.config.get('GOOGLE_CLIENT_ID') else None

if oauth_obj and app.config['GOOGLE_CLIENT_ID']:
    oauth_obj.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url=app.config['GOOGLE_DISCOVERY_URL'],
        client_kwargs={'scope': 'openid email profile https://www.googleapis.com/auth/calendar'}
    )

DEFAULT_TASKS = [
    {'id': 't1',  'title': '歷史數據資料庫化',                'description': '將 MQTT 歷史資料存入 SQLite/PostgreSQL，取代 localStorage',     'column': 'backlog', 'priority': 'high'},
    {'id': 't2',  'title': '告警通知系統',                    'description': 'SOC/SOH/溫度異常時觸發 Telegram Line 通知',                  'column': 'backlog', 'priority': 'high'},
    {'id': 't3',  'title': '數據匯出功能',                    'description': 'CSV/PDF 匯出監控報告，支援時間範圍篩選',                       'column': 'backlog', 'priority': 'medium'},
    {'id': 't4',  'title': '案場地圖視覺化',                  'description': 'Leaflet/Mapbox 整合，在地圖上顯示案場狀態',                    'column': 'backlog', 'priority': 'low'},
    {'id': 't5',  'title': '權限管理',                        'description': '不同角色查看不同案場資料的 RBAC 系統',                         'column': 'backlog', 'priority': 'medium'},
    {'id': 't6',  'title': 'Forecast UI 優化',                'description': 'monitoring-forecast-ui.js 視覺化改進，加入趨勢圖表',           'column': 'todo',    'priority': 'high'},
    {'id': 't7',  'title': '深色/亮色主題切換',               'description': '使用 teaasia-css-white-theme skill 實作主題切換',              'column': 'todo',    'priority': 'medium'},
    {'id': 't8',  'title': 'Kanban 看板系統',                 'description': '建立專案管理看板，整合到 TeaAsia 系統',                        'column': 'in_progress', 'priority': 'high'},
    {'id': 't9',  'title': 'MQTT 斷線重連機制',               'description': 'monitoring-mqtt.js 自動重連 + 指數退避策略',                   'column': 'review',  'priority': 'high'},
    {'id': 't10', 'title': 'MQTT WebSocket 連線',             'description': 'monitoring-mqtt.js mqtt.js CDN ws broker',                     'column': 'done',    'priority': 'high'},
    {'id': 't11', 'title': 'Topic 訊息解析',                  'description': 'monitoring-parser.js data 按 deviceUuid 分組',                 'column': 'done',    'priority': 'high'},
    {'id': 't12', 'title': '案場卡片動態建立',                'description': 'monitoring-card.js SITES array to card HTML',                  'column': 'done',    'priority': 'high'},
    {'id': 't13', 'title': '多案場監控頁面',                  'description': 'multi_site_monitoring plus monitoring_system 路由',             'column': 'done',    'priority': 'high'},
    {'id': 't14', 'title': 'Forecast 預測引擎',               'description': 'monitoring-forecast.js localStorage 30天留存 15min interval',  'column': 'done',    'priority': 'medium'},
]



@app.route('/login/callback')
def google_callback():
    """Handle Google OAuth callback with domain restriction"""
    if not oauth_obj:
        flash('Google OAuth 未設定，請聯繫管理員。', 'error')
        return redirect(url_for('start_login'))
    
    try:
        token = oauth_obj.google.authorize_access_token()
    except Exception as e:
        app.logger.error(f"Google OAuth callback 失敗: {e}")
        flash('Google 登入失敗，請重試。', 'error')
        return redirect(url_for('start_login'))
    
    if not token or 'userinfo' not in token:
        flash('Google 登入失敗：未收到使用者資訊。', 'error')
        return redirect(url_for('start_login'))
    
    userinfo = token['userinfo']
    email = userinfo.get('email', '')
    google_id = userinfo.get('sub', '')
    
    if not email:
        flash('Google 登入失敗：無法取得 Email。', 'error')
        return redirect(url_for('start_login'))
    
    allowed_domains = app.config.get('ALLOWED_DOMAINS', [])
    if allowed_domains:
        domain_ok = any(email.endswith(f'@{d}') for d in allowed_domains)
        
        if not domain_ok:
            flash(f'僅允許 {", ".join(allowed_domains)} 網域的帳號登入。', 'error')
            return redirect(url_for('start_login'))
    
    # Auto-register user in kanban_users table
    conn_reg = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur_reg = conn_reg.cursor()
    try:
        cur_reg.execute(
            "INSERT INTO kanban_users (google_id, email, name) VALUES (%s,%s,%s) "
            "ON CONFLICT (email) DO UPDATE SET name=EXCLUDED.name, google_id=EXCLUDED.google_id",
            (google_id, email, userinfo.get('name', ''))
        )
        conn_reg.commit()
    except Exception as e:
        app.logger.warning("User registration failed: %s", e)
    finally:
        cur_reg.close()
        conn_reg.close()

    # Create/update session with user info
    session['user_email'] = email
    session['google_id'] = google_id
    
    # Store OAuth access token for Calendar API & Email (MVP: stored in session)
    if 'access_token' in token:
        session['oauth_access_token'] = token.get('access_token')
        session['oauth_refresh_token'] = token.get('refresh_token', '')
    
    flash(f'歡迎回來，{email.split("@")[0]}！', 'success')
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    """Logout current user — redirect to kanban index (login gate) instead of OAuth flow."""
    session.clear()
    flash('已登出。', 'info')
    # Delete session cookie immediately to invalidate any cached pages
    resp = redirect(url_for('index'))  # ← 改回看板首頁（會顯示登入遮罩）
    resp.set_cookie('session', '', expires=0, max_age=0, path='/')
    return resp


@app.route('/')
def index():
    """Check auth before showing kanban board"""
    user_email = session.get('user_email')
    if not user_email:
        # Not logged in — show login gate (don't redirect to OAuth)
        flash('請先登入。', 'warning')
        return render_template('index.html', user_email=None, currentUserEmail='')
    return render_template('index.html', user_email=user_email, currentUserEmail=user_email)


@app.route('/login/dev', methods=['POST'])
def dev_login():
    """Development-only: skip OAuth, set session directly. Use only for local testing."""
    email = request.form.get('email', 'vip@test.com')
    session['user_email'] = email
    session['google_id'] = f'dev_{email}'
    flash(f'開發模式登入為 {email}', 'success')
    return redirect(url_for('index'))

@app.route('/login/dev-csrf', methods=['GET'])
def dev_login_form():
    """Development-only: simple login form (no CSRF for testing)"""
    from flask import render_template_string
    html = '''<!DOCTYPE html>
<html><body style="font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh">
<h2>Dev Login</h2>
<form method="post" action="/login/dev" style="margin-top:16px">
  <input name="email" value="vip@test.com" />
  <button type="submit">Login</button>
</form>
<p><a href="/">← Back to Kanban</a></p>
</body></html>'''
    return render_template_string(html)

@app.route('/login')
def start_login():
    """Redirect to Google OAuth"""
    if not app.config.get('GOOGLE_CLIENT_ID'):
        flash('Google OAuth 未設定，請聯繫管理員。', 'error')
        return redirect(url_for('index'))
    
    if oauth_obj:
        # Dynamic callback URL based on actual request host
        redirect_uri = f'{request.scheme}://{request.host}/login/callback'
        return oauth_obj.google.authorize_redirect(redirect_uri=redirect_uri)
    
    flash('OAuth 未初始化', 'error')
    return redirect(url_for('index'))


# ── Auth Guard: require login for API ──
@app.before_request
def require_login():
    """Require authentication for API endpoints."""
    if request.path.startswith('/api/') and 'user_email' not in session:
        # Allow /api/users without login (public user profiles for assignee search)
        if request.path != '/api/users':
            return jsonify({'error': 'unauthorized', 'message': '需要登入'}), 401


# ── Calendar View: GET /calendar ──
@app.route('/calendar')
def calendar():
    """Render the calendar view page."""
    user_email = session.get('user_email')
    if not user_email:
        flash('請先登入。', 'warning')
        return render_template('index.html', user_email=None)
    return render_template('calendar.html', user_email=user_email)


# ── API: GET /api/calendar (monthly tasks for calendar view) ──
@app.route('/api/calendar', methods=['GET'])
def get_calendar_tasks():
    """Return tasks that fall within the given month, filtered by assignee if ?user= is provided."""
    month = request.args.get('month', '')  # YYYY-MM format
    user_email = request.args.get('user', '').strip()

    if not month:
        return jsonify({'error': 'missing month parameter'}), 400

    try:
        year, mon = month.split('-')
        year_int = int(year)
        mon_int = int(mon)
    except (ValueError, IndexError):
        return jsonify({'error': 'invalid month format, use YYYY-MM'}), 400

    # Compute date range for the requested month
    if mon_int == 12:
        next_month = year_int + 1
        next_mon = 1
    else:
        next_month = year_int
        next_mon = mon_int + 1

    start_date = f'{year_int}-{mon_int:02d}-01'
    end_date = f'{next_month}-{next_mon:02d}-01'

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()

    if user_email:
        # Only show tasks assigned to this user — use LIKE for multi-email support
        like_pattern = '%' + user_email + '%'
        query = """
            SELECT id, title, description, priority, assignee_email, start_time, end_time
            FROM kanban_tasks
            WHERE (%s <= COALESCE(end_time::date, date '9999-12-31'))
              AND (COALESCE(start_time::date, date '1970-01-01') < %s)
              AND assignee_email LIKE %s
            ORDER BY start_time ASC
        """
        cur.execute(query, (start_date, end_date, like_pattern))
    else:
        # Show all tasks that overlap with the month
        query = """
            SELECT id, title, description, priority, assignee_email, start_time, end_time
            FROM kanban_tasks
            WHERE (%s <= COALESCE(end_time::date, date '9999-12-31'))
              AND (COALESCE(start_time::date, date '1970-01-01') < %s)
            ORDER BY start_time ASC
        """
        cur.execute(query, (start_date, end_date))

    rows = cur.fetchall()
    tasks = []
    for r in rows:
        st = r[5].isoformat() if r[5] else None
        et = r[6].isoformat() if r[6] else None
        tasks.append({
            'id': r[0],
            'title': r[1],
            'description': r[2] or '',
            'priority': r[3],
            'assignee_email': r[4],
            'start_time': st,
            'end_time': et,
        })

    cur.close()
    conn.close()
    return jsonify(tasks)


# ── API: GET /api/calendar/assignees (distinct assignees for current month) ──
@app.route('/api/calendar/assignees', methods=['GET'])
def get_calendar_assignees():
    """Return distinct assignee emails from tasks in the given month."""
    month = request.args.get('month', '')  # YYYY-MM format

    if not month:
        return jsonify({'error': 'missing month parameter'}), 400

    try:
        year, mon = month.split('-')
        year_int = int(year)
        mon_int = int(mon)
    except (ValueError, IndexError):
        return jsonify({'error': 'invalid month format, use YYYY-MM'}), 400

    if mon_int == 12:
        next_month = year_int + 1
        next_mon = 1
    else:
        next_month = year_int
        next_mon = mon_int + 1

    start_date = f'{year_int}-{mon_int:02d}-01'
    end_date = f'{next_month}-{next_mon:02d}-01'

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()

    # Get distinct assignee emails for tasks in this month
    cur.execute("""
        SELECT DISTINCT assignee_email
        FROM kanban_tasks
        WHERE (%s <= COALESCE(end_time::date, date '9999-12-31'))
          AND (COALESCE(start_time::date, date '1970-01-01') < %s)
          AND assignee_email IS NOT NULL
          AND assignee_email != ''
        ORDER BY assignee_email ASC
    """, (start_date, end_date))

    rows = cur.fetchall()
    
    # Collect all distinct emails (handle comma-separated multi-assignees)
    seen_emails = set()
    for r in rows:
        raw_email = r[0] or ''
        if not raw_email:
            continue
        # Split comma-separated emails and add each one separately
        for email in raw_email.split(','):
            email = email.strip()
            if email and email not in seen_emails:
                seen_emails.add(email)
    
    # Now fetch display names from kanban_users table
    assignees = []
    conn2 = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur2 = conn2.cursor()
    for email in sorted(seen_emails):
        try:
            cur2.execute("SELECT name FROM kanban_users WHERE email=%s LIMIT 1", (email,))
            user_row = cur2.fetchone()
            display_name = user_row[0] if user_row and user_row[0] else ''
        except Exception:
            display_name = ''
        
        if not display_name:
            # Fallback: extract from email
            display_name = email.split('@')[0].replace('.', ' ').title()
        
        assignees.append({'email': email, 'name': display_name})
    
    cur2.close()
    conn2.close()

    return jsonify(assignees)


# ── Prevent browser bfcache on auth pages ──
@app.after_request
def prevent_auth_caching(response):
    """Set cache-control headers to prevent bfcache for protected routes."""
    if request.endpoint in ('index', 'login', 'google_callback', 'logout'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


# ── API: GET /api/users (search users for assignee dropdown) ──
@app.route('/api/users', methods=['GET'])
def search_users():
    """Search kanban_users by name/email with pagination."""
    q = request.args.get('q', '').strip()
    limit = int(request.args.get('limit', 10))
    page = int(request.args.get('page', 1))

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()

    if q:
        # Search on name or email substring match (case-insensitive) with pagination
        # Substring matching gives better UX for user search — "an" matches "Anne", "Andy", "Wang", etc.
        offset = (page - 1) * limit
        pattern_lower = '%' + q.lower() + '%'  # e.g. '%an%' for substring match
        cur.execute(
            "SELECT google_id, email, name FROM kanban_users "
            "WHERE LOWER(name) LIKE %s OR LOWER(email) LIKE %s "
            "ORDER BY name ASC LIMIT %s OFFSET %s",
            (pattern_lower, pattern_lower, min(limit, 50), offset)
        )
    else:
        # Return all users when no query (for initial load)
        cur.execute(
            "SELECT google_id, email, name FROM kanban_users ORDER BY name ASC LIMIT %s OFFSET %s",
            (min(limit, 50), 0)
        )

    rows = cur.fetchall()
    users = []
    for r in rows:
        users.append({'id': r[0], 'email': r[1], 'name': r[2] or ''})
    cur.close()
    conn.close()
    return jsonify(users)


# ── API: GET /api/tasks ──
@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    """Return all kanban tasks from shared DB."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, description, column_name, priority, creator_email, assignee_email, start_time, end_time FROM kanban_tasks ORDER BY column_name, id;")
    rows = cur.fetchall()
    tasks = []
    for r in rows:
        # Convert timestamp objects to ISO format strings
        st = r[7].isoformat() if r[7] else None
        et = r[8].isoformat() if r[8] else None
        tasks.append({
            'id': r[0], 'title': r[1], 'description': r[2] or '',
            'column': r[3], 'priority': r[4],
            'creator_email': r[5], 'assignee_email': r[6],
            'start_time': st, 'end_time': et,
        })
    cur.close()
    conn.close()
    return jsonify(tasks)


# ── API: POST /api/tasks (create task from form/JS) ──
@app.route('/api/task', methods=['POST'])
def create_task():
    """Create a new kanban task."""
    data = request.json or {}
    if not data.get('title'):
        return jsonify({'error': 'no title'}), 400
    
    # Generate ID if missing
    tid = data.get('id', f't{len(DEFAULT_TASKS) + 1}')
    
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO kanban_tasks (id, title, description, column_name, priority, creator_email, assignee_email, start_time, end_time) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tid, data.get('title', ''), data.get('description', ''),
             data.get('column', 'backlog'), data.get('priority', 'medium'),
             session.get('user_email') if session else None,
             data.get('assignee_email'),
             data.get('start_time'),
             data.get('end_time'))
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()
    
    oauth_token = session.get('oauth_access_token')
    if data.get('assignee_email') and oauth_token and data.get('start_time'):
        try:
            assignees = [e.strip() for e in str(data['assignee_email']).split(',') if e.strip()]
            _create_meeting_with_all_assignees(
                tid, 
                title=data.get('title', ''), 
                description=data.get('description', ''),
                assignee_emails=','.join(assignees),
                start_time=data.get('start_time'),
                end_time=data.get('end_time')
            )
        except Exception as e:
            app.logger.warning("Calendar sync failed for task %s — will still try email notification: %s", tid, str(e))
    elif data.get('assignee_email'):
        app.logger.info("OAuth token not available for calendar sync of task %s (dev login or expired token)", tid)

    # Notify assignee that task was created for them
    notify_task_created(
        tid, session.get('user_email'), data.get('assignee_email'),
        title=data.get('title', ''), description=data.get('description', ''),
        priority=data.get('priority', None), start_time=data.get('start_time'),
        end_time=data.get('end_time')
    )

    return jsonify({'status': 'ok'})


# ── API: PUT /api/task/<id> (update task) ──
@app.route('/api/task/<tid>', methods=['PUT'])
def update_task(tid):
    """Update an existing kanban task."""
    data = request.json or {}
    
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Read OLD values BEFORE update (for schedule change detection)
        old_task_sql = "SELECT start_time, end_time, creator_email FROM kanban_tasks WHERE id=%s"
        cur0 = conn.cursor()
        cur0.execute(old_task_sql, (tid,))
        old_row = cur0.fetchone()
        old_start = old_row[0] if old_row else None
        old_end = old_row[1] if old_row else None
        old_creator = old_row[2] if old_row else None
        cur0.close()

        # Now perform the update
        cur.execute(
            "UPDATE kanban_tasks SET title=%s, description=%s, column_name=%s, priority=%s, assignee_email=%s, start_time=%s, end_time=%s WHERE id=%s",
            (data.get('title', ''), data.get('description', ''),
             data.get('column'), data.get('priority'),
             data.get('assignee_email'),
             data.get('start_time'),
             data.get('end_time'),
             tid)
        )
        conn.commit()
        
        # Check if schedule changed and notify creator via email + calendar sync
        time_changed = False
        if old_start is not None and data.get('start_time'):
            if str(old_start.date()) != str(data['start_time']):
                time_changed = True
        if old_end is not None and data.get('end_time'):
            if str(old_end.date()) != str(data['end_time']):
                time_changed = True
            
        if time_changed:
                notify_task_creator(
                    tid, old_creator,
                    assignee_email=data.get('assignee_email', ''),
                    title=data.get('title', ''), description=data.get('description', ''),
                    priority=data.get('priority'), start_time=data.get('start_time'),
                    end_time=data.get('end_time')
                )
                
                # Auto-resync Google Calendar events (delete old + create new for each assignee)
                oauth_token = session.get('oauth_access_token')
                if oauth_token and data.get('start_time'):
                    app.logger.info("Schedule changed for task %s — resyncing calendar...", tid)
                    try:
                        resync_task_schedule(tid, oauth_token)
                    except Exception as e:
                        app.logger.error("Calendar resync failed: %s", e)
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
    
    return jsonify({'status': 'ok'})


# ── API: DELETE /api/task/<id> (delete task) ──
@app.route('/api/task/<tid>', methods=['DELETE'])
def delete_task(tid):
    """Delete a kanban task."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM kanban_tasks WHERE id=%s", (tid,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()
    
    # Clean up Google Calendar events for deleted task
    oauth_token = session.get('oauth_access_token')
    if oauth_token:
        try:
            _find_and_delete_calendar_events(tid, oauth_token)
        except Exception as e:
            app.logger.error("Calendar cleanup on delete failed: %s", e)
    
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)


# ── SMTP Email Notification Service (Phase 2) ──
import smtplib
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
MAIL_USER = os.environ.get('MAIL_USER', '')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')


def send_kanban_email(to_email, subject, body_html, cc_list=None):
    """Send HTML email via SMTP with error handling. Supports CC for batch notifications."""
    if not MAIL_USER or not MAIL_PASSWORD:
        app.logger.warning("SMTP 未設定，跳過發送郵件")
        return False
    
    try:
        msg = MIMEText(body_html, 'html', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = MAIL_USER
        
        # Primary recipient(s)
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
        
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(MAIL_USER, MAIL_PASSWORD)
            server.sendmail(MAIL_USER, all_recipients, msg.as_string())
        
        app.logger.info("Email 已發送至 %s (CC: %s): %s", primary_recipient, ', '.join(cc_list or []), subject)
        return True
    except Exception as e:
        app.logger.error("SMTP 發送失敗: %s", str(e))
        return False


def build_task_notification_html(task_id, title, description, priority, start_time, end_time, assignee_emails):
    """Build rich HTML email for task notification with Google Calendar link."""
    priority_map = {'high': '🔴 高', 'medium': '🟡 中', 'low': '🟢 低'}
    priority_text = priority_map.get(priority, priority) if priority else '未設定'
    
    # Time display helper
    def fmt_time(ts):
        if not ts: return '—'
        try:
            dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
            return dt.strftime('%Y/%m/%d %H:%M') + ' (UTC+8)'
        except Exception:
            return str(ts)
    
    start_display = fmt_time(start_time)
    end_display = fmt_time(end_time)
    time_info = f"{start_display} ~ {end_display}" if start_time or end_time else "未設定排程"
    
    # Google Calendar link (TEMPLATE action lets user review before adding)
    cal_dates = ''
    if start_time:
        try:
            dt_start = datetime.fromisoformat(str(start_time).replace('Z', '+00:00'))
            if end_time and end_time != start_time:
                dt_end = datetime.fromisoformat(str(end_time).replace('Z', '+00:00'))
                cal_dates = f"{dt_start.strftime('%Y%m%dT%H%M%SZ')}/{dt_end.strftime('%Y%m%dT%H%M%SZ')}"
            else:
                cal_dates = f"{dt_start.strftime('%Y%m%dT%H%M%SZ')}/"
        except Exception:
            pass
    
    cal_url = ""
    if cal_dates:
        from urllib.parse import quote
        
        task_title_for_cal = title or f"Task #{task_id}"
        kanban_url = f"{APP_URL}/#/detail/{task_id}"
        
        # Build query parameters with attendees (被指派者)
        params = [
            ('action', 'TEMPLATE'),
            ('text', task_title_for_cal),
            ('dates', cal_dates),
            ('details', description or ''),
            ('location', kanban_url),
        ]
        
        # Add all assignees as attendees (Google Calendar URL uses 'add' param, NOT 'attendee')
        for att in [e.strip() for e in str(assignee_emails).split(',') if e.strip()]:
            params.append(('add', att))
            
        query = '&'.join(f'{quote(k)}={quote(v)}' for k, v in params)
        cal_url = f"https://calendar.google.com/calendar/render?{query}"

    assignees_str = '<br>'.join(e.strip() for e in str(assignee_emails).split(',') if e.strip())
    
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: auto;">
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


def notify_task_created(task_id, creator_email, assignee_email, title='', description=None, priority=None, start_time=None, end_time=None):
    """Notify when a task is created and assigned.
    
    Sends ONE email to ALL assignees (not CC), so everyone sees each other as attendees - like a meeting invitation.
    """
    subject = f"📋 [Kanban Board] {title or '新任務指派'}"
    
    # Collect all unique assignee emails (no self-notification skip)
    assignees_raw = [e.strip() for e in str(assignee_email).split(',') if e.strip()]
    app.logger.info("NOTIFY_TASK_CREATED: assignee_emails=%r, parsed_count=%d", assignee_email, len(assignees_raw))
    assignees = list(set(assignees_raw))
    
    html_body = build_task_notification_html(task_id, title, description, priority, start_time, end_time, ','.join(assignees))
    
    # Send ONE email with ALL assignees as recipients (everyone sees everyone like a meeting invite)
    send_kanban_email(list(assignees), subject, html_body)
    
    # Also create Google Calendar event with ALL attendees if OAuth token available
    oauth_token = session.get('oauth_access_token')
    assignees_str = ','.join(assignees)  # Convert list back to comma-separated string for calendar API
    if oauth_token and start_time:
        try:
            _create_meeting_with_all_assignees(task_id, title, description, assignees_str, start_time, end_time)
        except Exception as e:
            app.logger.error("Failed to create Google Calendar event for task %s: %s", task_id, str(e))


def _create_meeting_with_all_assignees(task_id, title, description, assignee_emails, start_time, end_time):
    """Create a single Google Calendar event with ALL assignees as attendees."""
    oauth_token = session.get('oauth_access_token')
    if not oauth_token:
        return False
    
    cal = get_google_calendar_service(oauth_token)
    if not cal:
        # Try refreshing token
        refresh_token = session.get('oauth_refresh_token', '')
        new_token = _refresh_google_token(refresh_token)
        if new_token:
            cal = get_google_calendar_service(new_token)
    
    if not cal:
        return False
    
    # Build attendees list from assignee_emails (comma-separated string)
    attendees = [{'email': e.strip()} for e in str(assignee_emails).split(',') if e.strip()]
    
    event_id = cal.create_event(
        summary=title or f'Task #{task_id}',
        description=f"{description}\nTask URL: {APP_URL}/#/detail/{task_id}",
        start_time=start_time,
        end_time=end_time,
        attendee_emails=attendees  # Pass ALL attendees at once
    )
    
    return event_id is not None


# ── Google Calendar Integration ──
def get_google_calendar_service(oauth_access_token):
    """Create a minimal Google Calendar API client using requests + OAuth token."""
    if not oauth_access_token:
        app.logger.warning("No OAuth access token for Calendar API")
        return None
    headers = {'Authorization': f'Bearer {oauth_access_token}'}
    try:
        # Verify token is valid by calling userinfo endpoint
        r = requests.get('https://www.googleapis.com/oauth2/v3/userinfo', headers=headers, timeout=5)
        if not r.ok:
            app.logger.warning("OAuth token invalid for Calendar API")
            return None
    except Exception as e:
        app.logger.warning("Token verification failed: %s", e)
        return None
    # Return a wrapper that can call calendar API
    class CalendarAPI:
        def __init__(self, token):
            self.token = token
            self.headers = {'Authorization': f'Bearer {token}'}
        def create_event(self, summary, description='', start_time=None, end_time=None, attendee_email=None, attendee_emails=None):
            """Create a Google Calendar event for the task. Returns (event_id|None)."""
            body = {
                'summary': summary,
                'description': description or '',
                'start': {'dateTime': start_time, 'timeZone': 'Asia/Taipei'},
                'end':   {'dateTime': end_time,   'timeZone': 'Asia/Taipei'},
            }
            
            # Support both single email (old API) and list of attendees (new API)
            if attendee_emails:
                body['attendees'] = attendee_emails  # List of {'email': 'x@y.com'}
            elif attendee_email:
                body['attendees'] = [{'email': attendee_email}]
            try:
                r = requests.post(
                    'https://www.googleapis.com/calendar/v3/calendars/primary/events?sendUpdates=all',
                    headers=self.headers,
                    json=body,
                    timeout=10
                )
                if r.ok:
                    eid = r.json().get('id')
                    app.logger.info("Calendar event created: %s", eid)
                    return eid  # Return event ID for future reference
                else:
                    app.logger.error("Calendar API error: %d %s", r.status_code, r.text[:200])
                    return False
            except Exception as e:
                app.logger.error("Calendar create failed: %s", e)
                return False
        def delete_event(self, event_id):
            """Delete an existing Google Calendar event (sends cancellation to attendees)."""
            try:
                r = requests.delete(
                    f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}?sendNotifications=true',
                    headers=self.headers,
                    timeout=10
                )
                if r.ok:
                    app.logger.info("Calendar event deleted: %s", event_id)
                    return True
                else:
                    app.logger.error("Calendar delete error: %d %s", r.status_code, r.text[:200])
                    return False
            except Exception as e:
                app.logger.error("Calendar delete failed: %s", e)
                return False
        def update_event(self, event_id, summary=None, start_time=None, end_time=None):
            """Update an existing Google Calendar event."""
            body = {}
            if summary:   body['summary'] = summary
            if start_time: body['start'] = {'dateTime': start_time, 'timeZone': 'Asia/Taipei'}
            if end_time:   body['end']   = {'dateTime': end_time,   'timeZone': 'Asia/Taipei'}
            try:
                r = requests.put(
                    f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}',
                    headers=self.headers,
                    json=body,
                    timeout=10
                )
                if r.ok:
                    app.logger.info("Calendar event updated: %s", event_id)
                    return True
                else:
                    app.logger.error("Calendar update error: %d %s", r.status_code, r.text[:200])
                    return False
            except Exception as e:
                app.logger.error("Calendar update failed: %s", e)
                return False
    return CalendarAPI(oauth_access_token)


def _refresh_google_token(refresh_token):
    """Refresh Google OAuth access token using stored refresh token. Returns new access_token or None."""
    if not refresh_token:
        return None
    try:
        r = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id': app.config['GOOGLE_CLIENT_ID'],
                'client_secret': app.config['GOOGLE_CLIENT_SECRET'],
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token',
            },
            timeout=10
        )
        if r.ok:
            data = r.json()
            return data.get('access_token')
        else:
            app.logger.warning("Token refresh failed: %d %s", r.status_code, r.text[:200])
    except Exception as e:
        app.logger.warning("Token refresh error: %s", e)
    return None


def sync_task_to_calendar(task_id, assignee_email, oauth_token):
    """Sync a kanban task to the assignee's Google Calendar. Stores event ID in description for future updates."""
    if not oauth_token or not assignee_email:
        return False
    
    # Try refreshing token if current one might be expired
    refresh_token = session.get('oauth_refresh_token', '')
    effective_token = oauth_token
    cal = get_google_calendar_service(oauth_token)
    
    if not cal and refresh_token:
        app.logger.info("Token invalid/expired, attempting refresh for task %s", task_id)
        new_token = _refresh_google_token(refresh_token)
        if new_token:
            effective_token = new_token
            session['oauth_access_token'] = new_token  # Update stored token
            cal = get_google_calendar_service(new_token)
    
    if not cal:
        return False

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT title, description, start_time, end_time FROM kanban_tasks WHERE id=%s",
            (task_id,)
        )
        row = cur.fetchone()
        if not row or not row[2]:  # No start_time => skip calendar event
            return False

        title, desc, st, et = row
        existing_desc = desc or ''
        
        # Build attendees list from assignee_emails (comma-separated string)
        attendees = [{'email': e.strip()} for e in str(assignee_email).split(',') if e.strip()]
        
        new_event_id = cal.create_event(
            summary=f"📋 {title}",
            description=existing_desc.replace('[CALENDAR:', '').replace(']', ''),  # Clean old event IDs from description
            start_time=st.isoformat(),
            end_time=(et or st).isoformat(),
            attendee_emails=attendees,  # Pass ALL assignees at once
        )
        if new_event_id:
            # Store the Google Calendar event ID in task description for future reference
            meta_marker = f'[CALENDAR:{new_event_id}]'
            updated_desc = (existing_desc + ' ' + meta_marker).strip()
            cur.execute(
                "UPDATE kanban_tasks SET description=%s WHERE id=%s",
                (updated_desc, task_id)
            )
            conn.commit()
        return new_event_id is not None
    except Exception as e:
        app.logger.error("Calendar sync failed for task %s: %s", task_id, e)
        return False
    finally:
        cur.close()
        conn.close()


def _find_and_delete_calendar_events(task_id, oauth_token):
    """Search Google Calendar for events with this task's ID in description and delete them."""
    if not oauth_token:
        return 0
    cal = get_google_calendar_service(oauth_token)
    if not cal:
        return 0

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Get task title and existing event IDs from description
        cur.execute(
            "SELECT title, description FROM kanban_tasks WHERE id=%s",
            (task_id,)
        )
        row = cur.fetchone()
        if not row:
            return 0

        title, desc = row
        # Extract stored calendar event IDs from description
        event_ids = re.findall(r'\[CALENDAR:([a-zA-Z0-9_-]+)\]', desc or '')

        deleted_count = 0
        for eid in event_ids:
            if cal.delete_event(eid):
                deleted_count += 1
        return deleted_count
    except Exception as e:
        app.logger.error("Calendar cleanup failed for task %s: %s", task_id, e)
        return 0
    finally:
        cur.close()
        conn.close()


def resync_task_schedule(task_id, oauth_token):
    """When task schedule changes: delete old calendar events and create new ones for each assignee."""
    if not oauth_token:
        return False
    
    # Try refreshing token first
    refresh_token = session.get('oauth_refresh_token', '')
    effective_token = oauth_token
    if not get_google_calendar_service(oauth_token) and refresh_token:
        app.logger.info("Token invalid/expired, attempting refresh for schedule resync %s", task_id)
        new_token = _refresh_google_token(refresh_token)
        if new_token:
            effective_token = new_token
            session['oauth_access_token'] = new_token

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Step 1: Delete all existing calendar events for this task (across all assignees)
        _find_and_delete_calendar_events(task_id, effective_token)

        # Step 2: Get updated task info
        cur.execute(
            "SELECT title, description, start_time, end_time, assignee_email FROM kanban_tasks WHERE id=%s",
            (task_id,)
        )
        row = cur.fetchone()
        if not row or not row[2]:  # No start_time => skip
            return False

        title, desc, st, et, assignee_emails_str = row
        if not assignee_emails_str:
            return False

        # Step 3: Create SINGLE calendar event with ALL attendees (not one per person)
        assignees = [e.strip() for e in str(assignee_emails_str).split(',') if e.strip()]
        try:
            _create_meeting_with_all_assignees(
                task_id, 
                title=title, 
                description=desc,
                assignee_emails=assignee_emails_str,
                start_time=st,
                end_time=et
            )
            created_any = True
        except Exception as e:
            app.logger.error("Failed to create calendar event for resync task %s: %s", task_id, str(e))

        return created_any
    except Exception as e:
        app.logger.error("Schedule resync failed for task %s: %s", task_id, e)
        return False
    finally:
        cur.close()
        conn.close()


def notify_task_creator(task_id, creator_email, assignee_email='', title='', description=None, priority=None, start_time=None, end_time=None):
    """Notify when schedule changes - sends ONE email with all assignees in CC."""
    subject = f"📅 [Kanban Board] 任務時程異動: {title or 'Task #' + str(task_id)}"
    
    # Collect all unique assignee emails
    assignees = list(set([e.strip() for e in str(assignee_email).split(',') if e.strip()]))
    assignees.sort()
    
    body_html = build_task_notification_html(
        task_id, title, description, priority, start_time, end_time, ','.join(assignees) or creator_email
    )
    # Add schedule change notice at top  
    body_html = body_html.replace('<h2', '<div style="background: #fff3cd; padding: 8px 16px; border-radius: 4px; margin-bottom: -1px; text-align: center;"><b>⚠️ 時程已異動</b></div><h2')
    
    send_kanban_email(creator_email, subject, body_html, cc_list=assignees if assignees else None)


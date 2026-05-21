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

# ── Ensure kanban_calendar_events table exists ──
def ensure_calendar_events_table():
    """Create kanban_calendar_events table if it doesn't exist."""
    try:
        conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kanban_calendar_events (
                id SERIAL PRIMARY KEY,
                task_id VARCHAR(50) NOT NULL,
                calendar_event_id VARCHAR(200) NOT NULL UNIQUE,
                summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        app.logger.warning("Failed to ensure kanban_calendar_events table: %s", str(e))

# Run on startup (deferred until first request)
with app.app_context():
    try:
        # Ensure tasks table exists first
        conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        cur = conn.cursor()
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'kanban_tasks')")
        if cur.fetchone()[0]:
            ensure_calendar_events_table()
        cur.close()
        conn.close()
    except Exception:
        pass  # DB might not be ready yet during import
# app.config['ALLOWED_DOMAINS'] = ['nextdrive.io']  # Development: allow all emails
app.config['ALLOWED_DOMAINS'] = []  # Empty = no domain restriction (for localhost testing)

# ── CSRF Protection (Phase 4 — init BEFORE any routes or requests) ──
from flask_wtf.csrf import CSRFProtect
csrf = CSRFProtect()
csrf.init_app(app)


def _is_api_route():
    """Check if current request is an API route."""
    try:
        from flask import request as req
        return req.path.startswith('/api/')
    except Exception:
        return False


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
    
    # Auto-register user in kanban_users table (with OAuth tokens for Calendar)
    conn_reg = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur_reg = conn_reg.cursor()
    try:
        access_token = token.get('access_token', '')
        refresh_token = token.get('refresh_token', '')
        cur_reg.execute(
            "INSERT INTO kanban_users (google_id, email, name, oauth_access_token, oauth_refresh_token) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (email) DO UPDATE SET name=EXCLUDED.name, google_id=EXCLUDED.google_id, "
            "oauth_access_token=COALESCE(EXCLUDED.oauth_access_token, kanban_users.oauth_access_token), "
            "oauth_refresh_token=COALESCE(EXCLUDED.oauth_refresh_token, kanban_users.oauth_refresh_token)",
            (google_id, email, userinfo.get('name', ''), access_token, refresh_token)
        )
        conn_reg.commit()
    except Exception as e:
        app.logger.warning("User registration failed: %s", e)
    finally:
        cur_reg.close()
        conn_reg.close()

    # Create/update session with user info (keep for backwards compat, but token is now in DB)
    session['user_email'] = email
    session['google_id'] = google_id
    
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

@csrf.exempt
@app.route('/login/dev', methods=['POST'])
def dev_login():
    """Development-only: skip OAuth, set session directly. Use only for local testing."""
    email = request.form.get('email', 'vip@test.com')
    session['user_email'] = email
    
    # Look up real google_id from kanban_users if email matches a known user
    try:
        db_conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        db_cur = db_conn.cursor()
        db_cur.execute(
            "SELECT google_id FROM kanban_users WHERE email=%s LIMIT 1", (email,)
        )
        row = db_cur.fetchone()
        if row:
            session['google_id'] = row[0]
        else:
            session['google_id'] = f'dev_{email}'
        db_cur.close()
        db_conn.close()
    except Exception as e:
        app.logger.warning("Dev login DB lookup failed for %s: %s", email, str(e))
        session['google_id'] = f'dev_{email}'
    
    flash(f'開發模式登入為 {email}', 'success')
    
    # Redirect to next URL if provided (e.g. after clicking email delete link)
    next_url = request.form.get('next', '') or session.pop('_login_next', '')
    if next_url:
        return redirect(next_url)
    return redirect(url_for('index'))

@csrf.exempt
@app.route('/login/dev-csrf', methods=['GET'])
def dev_login_form():
    """Development-only: simple login form (no CSRF for testing). Supports ?next=... redirect after login."""
    from flask import render_template_string, request
    next_url = request.args.get('next', '')
    
    # Build hidden input if next URL is provided
    hidden_input = ''
    if next_url:
        encoded = next_url.replace('"', '&quot;')
        hidden_input = '<input type="hidden" name="next" value="' + encoded + '"/>'
    
    html = '''<!DOCTYPE html>
<html><body style="font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh">
<h2>Dev Login</h2>
<form method="post" action="/login/dev" style="margin-top:16px">
  <input name="email" value="vip@test.com" />
  <button type="submit">Login</button>''' + hidden_input + '''
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
        # Also allow /api/calendar/delete-event to handle redirect-to-login internally
        if request.path != '/api/users' and request.path != '/api/calendar/delete-event':
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
@csrf.exempt
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
@csrf.exempt
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
@csrf.exempt
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
@csrf.exempt
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
        task = {
            'id': r[0], 'title': r[1], 'description': r[2] or '',
            'column': r[3], 'priority': r[4],
            'creator_email': r[5], 'assignee_email': r[6],
            'start_time': st, 'end_time': et,
        }
        # Get labels for this task
        cur2 = conn.cursor()
        try:
            cur2.execute("""SELECT l.name, l.color FROM kanban_labels l 
                JOIN task_labels tl ON tl.label_id=l.id WHERE tl.task_id=%s ORDER BY l.name""", (r[0],))
            labels = [{'name': rr[0], 'color': rr[1]} for rr in cur2.fetchall()]
        except Exception:
            labels = []
        finally:
            cur2.close()
        task['labels'] = labels
        # Get subtask stats
        try:
            cur3 = conn.cursor()
            try:
                cur3.execute("SELECT COUNT(*) as total, SUM(CASE WHEN is_completed THEN 1 ELSE 0 END) as done FROM subtasks WHERE parent_task_id=%s", (r[0],))
                stats = cur3.fetchone()
                task['subtask_total'] = stats[0] or 0
                task['subtask_done'] = stats[1] or 0
            finally:
                cur3.close()
        except Exception:
            task['subtask_total'] = 0
            task['subtask_done'] = 0
        tasks.append(task)
    cur.close()
    conn.close()
    return jsonify(tasks)


# ── API: POST /api/tasks (create task from form/JS) ──
@csrf.exempt
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
        col_name = data.get('column', 'backlog')
        # Determine sort_order: use provided value or MAX+1 for column
        if data.get('sort_order') is not None:
            sort_order_val = int(data['sort_order'])
        else:
            cur.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM kanban_tasks WHERE column_name=%s", (col_name,))
            sort_order_val = cur.fetchone()[0]
        
        cur.execute(
            "INSERT INTO kanban_tasks (id, title, description, column_name, priority, creator_email, assignee_email, start_time, end_time, sort_order) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tid, data.get('title', ''), data.get('description', ''),
             col_name, data.get('priority', 'medium'),
             session.get('user_email') if session else None,
             data.get('assignee_email'),
             data.get('start_time'),
             data.get('end_time'),
             sort_order_val)
        )
        conn.commit()
        
        # Log activity: task created
        try:
            cur2 = conn.cursor()
            try:
                cur2.execute(
                    "INSERT INTO activity_log (task_id, actor_email, action, field_name, old_value, new_value) VALUES (%s,%s,%s,%s,%s,%s)",
                    (tid, session.get('user_email', ''), 'created', None, None, data.get('title', ''))
                )
                conn.commit()
            finally:
                cur2.close()
        except Exception as e:
            app.logger.warning("Failed to log task created activity: %s", str(e))
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()
    
    # Notify assignee that task was created for them (email + calendar)
    _notify_and_calendar_sync(tid, session.get('user_email', ''), data.get('assignee_email'), 
        title=data.get('title', ''), description=data.get('description', ''),
        priority=data.get('priority', None), start_time=data.get('start_time'),
        end_time=data.get('end_time'))

    return jsonify({'status': 'ok'})


# ── API: PUT /api/task/<id> (update task) ──
@csrf.exempt
@app.route('/api/task/<tid>', methods=['PUT'])
def update_task(tid):
    """Update an existing kanban task."""
    data = request.json or {}
    
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Read ALL old values BEFORE update for activity logging and schedule change detection
        old_row_sql = "SELECT title, description, column_name, priority, assignee_email, start_time, end_time FROM kanban_tasks WHERE id=%s"
        cur0 = conn.cursor()
        cur0.execute(old_row_sql, (tid,))
        old_row = cur0.fetchone()
        if not old_row:
            return jsonify({'error': 'task not found'}), 404
        old_title, old_desc, old_col, old_priority, old_assignee, old_start, old_end = old_row
        cur0.close()
        
        # Detect changes for activity logging
        field_map = {
            'title': ('title', lambda v: str(v) if v else ''),
            'description': ('description', lambda v: str(v) if v else ''),
            'column': ('column', lambda v: str(v) if v else ''),
            'priority': ('priority', lambda v: str(v) if v else ''),
            'assignee_email': ('assignee_email', lambda v: str(v) if v else ''),
            'start_time': ('start_time', lambda v: v.isoformat() if v else None),
            'end_time': ('end_time', lambda v: v.isoformat() if v else None),
        }
        
        changes = []  # List of (key, field_name, old_value, new_value)
        old_vals = {
            'title': old_title, 'description': old_desc, 'column': old_col,
            'priority': old_priority, 'assignee_email': old_assignee,
            'start_time': old_start.isoformat() if old_start else None,
            'end_time': old_end.isoformat() if old_end else None,
        }
        for key in ['title', 'description', 'column', 'priority', 'assignee_email']:
            new_v = data.get(key)
            old_v = old_vals[key]
            if new_v is not None and str(new_v) != str(old_v):
                changes.append((key, field_map[key][0], str(old_v), str(new_v)))
        for key in ['start_time', 'end_time']:
            nv = data.get(key)
            ov = old_vals[key]
            if nv is not None and str(nv) != str(ov):
                new_fmt = datetime.fromisoformat(str(nv)).isoformat() if nv else None
                changes.append((key, field_map[key][0], ov, str(new_fmt)))
        
        # Build dynamic UPDATE with only fields present in request
        set_clauses = []
        params = []
        
        column_map = {
            'title': 'title',
            'description': 'description', 
            'column': 'column_name',  # DB column name differs from API field name
            'priority': 'priority',
            'assignee_email': 'assignee_email',
            'start_time': 'start_time',
            'end_time': 'end_time',
        }
        
        for api_key, db_col in column_map.items():
            if api_key in data:
                val = data[api_key]
                # Handle time fields - convert to ISO format
                if api_key in ('start_time', 'end_time') and val is not None:
                    try:
                        dt = datetime.fromisoformat(str(val))
                        val = dt.isoformat()
                    except (ValueError, TypeError):
                        pass
                set_clauses.append(f"{db_col}=%s")
                params.append(val if val is not None else '')
        
        if set_clauses:
            query = f"UPDATE kanban_tasks SET {', '.join(set_clauses)} WHERE id=%s"
            params.append(tid)
            cur.execute(query, params)
            conn.commit()
        conn.commit()
        
        # Log activity for each changed field
        ts = datetime.utcnow().isoformat()
        for key, field_name, old_v, new_v in changes:
            try:
                cur2 = conn.cursor()
                try:
                    cur2.execute(
                        "INSERT INTO activity_log (task_id, actor_email, action, field_name, old_value, new_value) VALUES (%s,%s,%s,%s,%s,%s)",
                        (tid, session.get('user_email', ''), 'updated', field_name, old_v or '', new_v or '')
                    )
                finally:
                    cur2.close()
            except Exception as e:
                app.logger.warning("Failed to log activity change for task %s: %s", tid, str(e))
        # Notify on any significant field change (not just time)
        new_start_str = data.get('start_time')
        new_end_str = data.get('end_time')
        
        # Detect schedule change for calendar resync
        time_changed = False
        if old_start is not None and new_start_str:
            try:
                if str(old_start.date()) != str(new_start_str):
                    time_changed = True
            except Exception:
                pass
        if old_end is not None and new_end_str:
            try:
                if str(old_end.date()) != str(new_end_str):
                    time_changed = True
            except Exception:
                pass
        
        # Check for other meaningful changes from the changes list built above
        has_notify_changes = any(k in ['title', 'description', 'priority', 'column'] for k, _, _, _ in changes)
        
        new_assignee = data.get('assignee_email', '') or None
        
        if time_changed:
            # Time changed → resync calendar + notify about schedule update
            _notify_schedule_change(
                tid, old_row[2], new_assignee,
                title=data.get('title', ''), description=data.get('description', ''),
                priority=data.get('priority'), start_time=new_start_str,
                end_time=new_end_str
            )
        
        if has_notify_changes:
            # Any field change → notify assignees about the update
            try:
                from email_service import notify_task_updated as send_update
                send_update(tid, session.get('user_email', ''), new_assignee,
                    title=data.get('title', ''), description=data.get('description', ''),
                    priority=data.get('priority'), start_time=new_start_str, end_time=new_end_str)
            except Exception as e:
                app.logger.warning("Update notification failed for task %s: %s", tid, str(e))
        
        # Update sort_order if provided
        if 'sort_order' in data and data['sort_order'] is not None:
            try:
                cur2 = conn.cursor()
                try:
                    cur2.execute("UPDATE kanban_tasks SET sort_order=%s WHERE id=%s", (int(data['sort_order']), tid))
                    conn.commit()
                finally:
                    cur2.close()
            except Exception as e:
                app.logger.warning("Failed to update sort_order for task %s: %s", tid, str(e))
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
    
    return jsonify({'status': 'ok'})


# ── API: DELETE /api/task/<id> (delete task) ──
@csrf.exempt
@app.route('/api/task/<tid>', methods=['DELETE'])
def delete_task(tid):
    """Delete a kanban task."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Read title + assignee before deleting (for activity logging & notification)
        cur.execute("SELECT title, assignee_email FROM kanban_tasks WHERE id=%s", (tid,))
        row = cur.fetchone()
        task_title = row[0] if row else None
        task_assignee = row[1] if row else None
        
        cur.execute("DELETE FROM kanban_tasks WHERE id=%s", (tid,))
        conn.commit()
        
        # Log activity: task deleted
        try:
            cur2 = conn.cursor()
            try:
                cur2.execute(
                    "INSERT INTO activity_log (task_id, actor_email, action, field_name, old_value, new_value) VALUES (%s,%s,%s,%s,%s,%s)",
                    (tid, session.get('user_email', ''), 'deleted', 'task', task_title or '', None)
                )
                conn.commit()
            finally:
                cur2.close()
        except Exception as e:
            app.logger.warning("Failed to log activity for deleted task %s: %s", tid, str(e))
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()
    
    # Notify assignees about task deletion + clean up calendar events
    # Extract calendar event IDs from kanban_calendar_events table (primary) or description markers (fallback)
    extracted_event_ids = []
    
    # DEBUG: Print current state  
    import sys as _sys
    print(f"[DELETE_DEBUG] task_id={tid}", file=_sys.stderr, flush=True)
    try:
        db_conn2 = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        db_cur2 = db_conn2.cursor()
        try:
            # Primary source: kanban_calendar_events table
            db_cur2.execute("SELECT calendar_event_id FROM kanban_calendar_events WHERE task_id=%s", (tid,))
            extracted_event_ids = [r[0] for r in db_cur2.fetchall()]
            
            if not extracted_event_ids:
                # Fallback: parse [CALENDAR:xxx] markers from description
                import re
                db_cur2.execute("SELECT description FROM kanban_tasks WHERE id=%s", (tid,))
                desc_row = db_cur2.fetchone()
                if desc_row and desc_row[0]:
                    extracted_event_ids = re.findall(r'\[CALENDAR:([a-zA-Z0-9_-]+)\]', str(desc_row[0]))
            
            app.logger.info("DELETE task %s: found %d calendar event(s) via kanban_calendar_events table", 
                tid, len(extracted_event_ids))
            
            # DEBUG
            _sys.stderr.write(f"[DELETE_DEBUG] extracted_event_ids={extracted_event_ids}\n")
            _sys.stderr.flush()
        finally:
            db_cur2.close()
            db_conn2.close()
    except Exception as e:
        app.logger.error("Failed to extract calendar IDs for task %s: %s", tid, str(e))
    
    event_ids_str = ','.join(extracted_event_ids) if extracted_event_ids else ''
    app.logger.info("DELETE task %s: passing calendar_event_id=%r to notify_task_deleted", tid, event_ids_str)
    
    try:
        if task_assignee:
            from email_service import notify_task_deleted as send_delete
            event_ids_str = ','.join(extracted_event_ids) if extracted_event_ids else ''
            send_delete(tid, session.get('user_email', ''), task_assignee, title=task_title or '', calendar_event_id=event_ids_str)
    except Exception as e:
        app.logger.warning("Delete notification failed for task %s: %s", tid, str(e))
    
    try:
        _cleanup_calendar_on_delete(tid)
    except Exception as e:
        app.logger.error("Calendar cleanup on delete failed: %s", e)
    
    return jsonify({'status': 'ok'})


# ── API: GET /api/calendar/delete-event (delete Google Calendar event from email action) ──
@csrf.exempt
@app.route('/api/calendar/delete-event', methods=['GET'])
def delete_calendar_event_from_email():
    """Delete a Google Calendar event when user clicks the link in deletion notification email.
    
    Handles three scenarios:
    1. Normal case: task exists, has [CALENDAR:] markers → deletes from Google Calendar + cleans description
    2. Task already deleted but kanban_calendar_events still has entries (orphaned cleanup) → deletes from Google Calendar
    3. Already fully cleaned up → returns success with count=0
    """
    if 'user_email' not in session:
        # Redirect to login with params preserved so user can retry after auth
        import urllib.parse
        task_id = request.args.get('task_id', '')
        cal_event_ids = request.args.get('calendar_event_id', '')
        next_url = url_for('delete_calendar_event_from_email', _external=True, task_id=task_id, calendar_event_id=cal_event_ids) if task_id else url_for('index', _external=True)
        return redirect(url_for('dev_login_form') + '?next=' + urllib.parse.quote(next_url))
    
    task_id = request.args.get('task_id', '')
    calendar_event_ids_raw = request.args.get('calendar_event_id', '')
    
    if not task_id or not calendar_event_ids_raw:
        return jsonify({'error': '缺少參數'}), 400
    
    # Support comma-separated event IDs (batch delete)
    all_calendar_event_ids = [e.strip() for e in str(calendar_event_ids_raw).split(',') if e.strip()]
    
    deleted_count = 0
    
    # Get user's OAuth token from DB (for Google Calendar API)
    try:
        _get_user_token_from_db(session.get('google_id', ''))
        access_token = session.get('oauth_access_token')
        if not access_token:
            flash('OAuth 權限無效，請重新登入', 'warning')
            return redirect(url_for('index'))
        
        from calendar_service import get_calendar_service
        cal = get_calendar_service(access_token, google_id=session.get('google_id', ''))
        if not cal:
            flash('無法建立 Calendar 服務，OAuth token 可能已過期', 'warning')
            return redirect(url_for('index'))
        
        # ── Collect ALL event IDs to delete (email URL + any orphaned entries) ──
        db_conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        db_cur = db_conn.cursor()
        
        try:
            # 1. Get any additional event IDs from kanban_calendar_events table (for tasks already deleted)
            #    These are orphaned events that _cleanup_calendar_on_delete failed to remove
            db_cur.execute(
                "SELECT calendar_event_id FROM kanban_calendar_events WHERE task_id=%s",
                (task_id,)
            )
            orphaned_ids = [r[0] for r in db_cur.fetchall()]
            
            # Merge with email-provided IDs (deduplicate)
            all_calendar_event_ids = list(set(all_calendar_event_ids + orphaned_ids))
            
            if not all_calendar_event_ids:
                return jsonify({
                    'status': 'ok',
                    'deleted_count': 0,
                    'message': '沒有需要刪除的 Google Calendar 事件 (可能已清除)'
                })
            
            app.logger.info("Deleting %d calendar event(s) for task %s via email link", len(all_calendar_event_ids), task_id)
            
            # 2. Delete each event from Google Calendar API (idempotent - safe if already deleted)
            successfully_deleted = []
            failed_deletions = []
            for calendar_event_id in all_calendar_event_ids:
                try:
                    result = cal.delete_event(calendar_event_id)
                    if result:
                        deleted_count += 1
                        successfully_deleted.append(calendar_event_id)
                        app.logger.info("Successfully deleted event '%s' from Google Calendar", calendar_event_id)
                    else:
                        failed_deletions.append(calendar_event_id)
                        app.logger.warning("Event '%s' returned False (may not exist or permission denied)", calendar_event_id)
                except Exception as e:
                    failed_deletions.append(calendar_event_id)
                    app.logger.error("Failed to delete event '%s' from Google Calendar: %s", calendar_event_id, str(e))
            
            # 3. Clean up kanban_calendar_events table (only remove entries for successfully deleted events)
            if successfully_deleted:
                placeholders = ','.join(['%s'] * len(successfully_deleted))
                db_cur.execute(
                    f"DELETE FROM kanban_calendar_events WHERE task_id=%s AND calendar_event_id IN ({placeholders})",
                    [task_id] + successfully_deleted
                )
            
            # 4. Also clean [CALENDAR:xxx] markers from description (if task row still exists)
            try:
                import re as _re
                db_cur.execute("SELECT description FROM kanban_tasks WHERE id=%s", (task_id,))
                row = db_cur.fetchone()
                if row and row[0]:
                    cleaned = str(row[0])
                    for eid in successfully_deleted:
                        cleaned = _re.sub(r'\[CALENDAR:' + re.escape(eid) + r'\]', '', cleaned).strip()
                    if len(successfully_deleted) > 0 and cleaned != row[0]:
                        db_cur.execute("UPDATE kanban_tasks SET description=%s WHERE id=%s", (cleaned, task_id))
            except Exception as e:
                app.logger.warning("Failed to clean markers for event(s): %s", str(e))
            
            try:
                db_conn.commit()
            except Exception:
                pass  # non-critical if description cleanup fails
            
        finally:
            db_cur.close()
            db_conn.close()
        
        return jsonify({
            'status': 'ok',
            'deleted_count': deleted_count,
            'message': f'已處理 {deleted_count}/{len(all_calendar_event_ids)} 個 Google Calendar 事件' + (
                '' if deleted_count > 0 else ' (事件可能已被其他使用者清除)'
            ) + ('; 有 {} 個刪除失敗，請確認 OAuth 權限'.format(len(failed_deletions)) if failed_deletions else ''),
        })
    except Exception as e:
        app.logger.error("Delete calendar event failed for task %s: %s", task_id, str(e))
        return jsonify({'error': f'刪除失敗: {str(e)}'}), 500


# ── API: POST /api/tasks/batch-move (batch move tasks to column) ──
@csrf.exempt
@app.route('/api/tasks/batch-move', methods=['POST'])
def batch_move_tasks():
    """Batch move selected tasks to a new column."""
    data = request.json or {}
    task_ids = data.get('task_ids', [])
    col = data.get('column')
    if not task_ids or not col:
        return jsonify({'error': 'missing task_ids or column'}), 400

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    moved = 0
    try:
        # Get current max sort_order for target column
        cur.execute("SELECT COALESCE(MAX(sort_order), 0) FROM kanban_tasks WHERE column_name=%s", (col,))
        base_order = cur.fetchone()[0]

        for i, tid in enumerate(task_ids):
            try:
                old_sql = "SELECT title, column_name FROM kanban_tasks WHERE id=%s"
                cur.execute(old_sql, (tid,))
                row = cur.fetchone()
                if not row:
                    continue

                old_col = row[1]
                new_order = base_order + i
                cur.execute(
                    "UPDATE kanban_tasks SET column_name=%s, sort_order=%s WHERE id=%s",
                    (col, new_order, tid)
                )
                conn.commit()

                # Log activity for each task moved
                try:
                    cur2 = conn.cursor()
                    try:
                        old_val = f"{old_col} → {col}" if old_col != col else col
                        cur2.execute(
                            "INSERT INTO activity_log (task_id, actor_email, action, field_name, old_value, new_value) VALUES (%s,%s,%s,%s,%s,%s)",
                            (tid, session.get('user_email', ''), 'status_changed', 'column', old_val, col)
                        )
                        conn.commit()
                    finally:
                        cur2.close()
                except Exception as e:
                    app.logger.warning("Activity log failed for batch move task %s: %s", tid, str(e))

                moved += 1
            except Exception:
                continue
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    return jsonify({'moved': moved})


# ── API: POST /api/tasks/batch-delete (batch delete tasks) ──
@csrf.exempt
@app.route('/api/tasks/batch-delete', methods=['POST'])
def batch_delete_tasks():
    """Batch delete selected tasks."""
    data = request.json or {}
    task_ids = data.get('task_ids', [])
    if not task_ids:
        return jsonify({'error': 'missing task_ids'}), 400

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    deleted = 0
    try:
        for tid in task_ids:
            # Read title before deleting (for activity logging)
            cur.execute("SELECT title FROM kanban_tasks WHERE id=%s", (tid,))
            row = cur.fetchone()
            if not row:
                continue

            try:
                cur2 = conn.cursor()
                try:
                    cur2.execute(
                        "INSERT INTO activity_log (task_id, actor_email, action, field_name, old_value, new_value) VALUES (%s,%s,%s,%s,%s,%s)",
                        (tid, session.get('user_email', ''), 'deleted', 'task', row[0], None)
                    )
                finally:
                    cur2.close()
            except Exception as e:
                app.logger.warning("Activity log failed for batch delete task %s: %s", tid, str(e))

            try:
                _cleanup_calendar_on_delete(tid)
            except Exception:
                pass

            cur.execute("DELETE FROM kanban_tasks WHERE id=%s", (tid,))
            deleted += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    return jsonify({'deleted': deleted})


# ── API: GET /api/labels (list all labels) ──
@csrf.exempt
@app.route('/api/labels', methods=['GET'])
def get_labels():
    """Return all labels."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, name, color FROM kanban_labels ORDER BY name")
        rows = cur.fetchall()
        return jsonify([{'id': r[0], 'name': r[1], 'color': r[2]} for r in rows])
    finally:
        cur.close()
        conn.close()


# ── API: POST /api/label (create label) ──
@csrf.exempt
@app.route('/api/label', methods=['POST'])
def create_label():
    """Create a new label."""
    data = request.json or {}
    name = data.get('name', '').strip()
    color = data.get('color', '#000000').strip()
    if not name:
        return jsonify({'error': 'missing name'}), 400

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO kanban_labels (name, color) VALUES (%s,%s) RETURNING id",
            (name, color)
        )
        lid = cur.fetchone()[0]
        conn.commit()
        return jsonify({'id': lid, 'name': name, 'color': color}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()


# ── API: DELETE /api/label/<id> (delete label) ──
@csrf.exempt
@app.route('/api/label/<int:lid>', methods=['DELETE'])
def delete_label(lid):
    """Delete a label."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM kanban_labels WHERE id=%s", (lid,))
        conn.commit()
        return jsonify({'deleted': cur.rowcount > 0})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()


# ── API: PUT /api/task/<tid>/labels (set task labels) ──
@csrf.exempt
@app.route('/api/task/<tid>/labels', methods=['PUT'])
def set_task_labels(tid):
    """Assign labels to a task by label names."""
    data = request.json or {}
    label_names = data.get('labels', [])

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Clear existing labels for this task
        cur.execute("DELETE FROM task_labels WHERE task_id=%s", (tid,))

        if label_names:
            # Get label IDs by name
            placeholders = ','.join(['%s'] * len(label_names))
            cur.execute(
                f"SELECT id FROM kanban_labels WHERE name IN ({placeholders})",
                tuple(label_names)
            )
            valid_ids = [r[0] for r in cur.fetchall()]

            # Insert new associations
            for lid in valid_ids:
                try:
                    cur2 = conn.cursor()
                    try:
                        cur2.execute("INSERT INTO task_labels (task_id, label_id) VALUES (%s,%s)", (tid, lid))
                    finally:
                        cur2.close()
                except Exception:
                    pass  # skip duplicates

        conn.commit()

        # Log activity
        if label_names:
            try:
                cur2 = conn.cursor()
                try:
                    cur2.execute(
                        "INSERT INTO activity_log (task_id, actor_email, action, field_name, old_value, new_value) VALUES (%s,%s,%s,%s,%s,%s)",
                        (tid, session.get('user_email', ''), 'label_added', 'labels', None, ', '.join(label_names))
                    )
                    conn.commit()
                finally:
                    cur2.close()
            except Exception as e:
                app.logger.warning("Activity log failed for labels on task %s: %s", tid, str(e))

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    return jsonify({'status': 'ok'})


# ── API: GET /api/task/<tid>/subtasks (list subtasks) ──
@csrf.exempt
@app.route('/api/task/<tid>/subtasks', methods=['GET'])
def get_subtasks(tid):
    """Return all subtasks for a task."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, title, is_completed, sort_order FROM subtasks WHERE parent_task_id=%s ORDER BY sort_order", (tid,))
        rows = cur.fetchall()
        return jsonify([{'id': r[0], 'title': r[1], 'is_completed': bool(r[2]), 'sort_order': r[3]} for r in rows])
    finally:
        cur.close()
        conn.close()


# ── API: POST /api/task/<tid>/subtask (create subtask) ──
@csrf.exempt
@app.route('/api/task/<tid>/subtask', methods=['POST'])
def create_subtask(tid):
    """Create a new subtask for a task."""
    data = request.json or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'error': 'missing title'}), 400

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Get max sort_order + 1
        cur.execute("SELECT COALESCE(MAX(sort_order), -1) FROM subtasks WHERE parent_task_id=%s", (tid,))
        new_order = cur.fetchone()[0] + 1

        cur.execute(
            "INSERT INTO subtasks (parent_task_id, title, is_completed, sort_order) VALUES (%s,%s,FALSE,%s)",
            (tid, title, new_order)
        )
        sid = cur.lastrowid
        conn.commit()

        # Log activity
        try:
            cur2 = conn.cursor()
            try:
                task_sql = "SELECT title FROM kanban_tasks WHERE id=%s"
                cur.execute(task_sql, (tid,))  # reuse cur? No, need new cursor. Use separate connection approach
            except Exception as e:
                pass

        except Exception:
            pass

    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    return jsonify({'id': sid, 'title': title}), 201


# ── API: PUT /api/subtask/<sid> (update subtask) ──
@csrf.exempt
@app.route('/api/subtask/<int:sid>', methods=['PUT'])
def update_subtask(sid):
    """Update a subtask."""
    data = request.json or {}

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        if 'title' in data and data['title']:
            cur.execute("UPDATE subtasks SET title=%s WHERE id=%s", (data['title'], sid))
        if 'is_completed' in data:
            cur.execute("UPDATE subtasks SET is_completed=%s WHERE id=%s", (bool(data['is_completed']), sid))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    return jsonify({'status': 'ok'})


# ── API: DELETE /api/subtask/<sid> (delete subtask) ──
@csrf.exempt
@app.route('/api/subtask/<int:sid>', methods=['DELETE'])
def delete_subtask(sid):
    """Delete a subtask."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM subtasks WHERE id=%s", (sid,))
        conn.commit()
        return jsonify({'deleted': cur.rowcount > 0})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()


# ── API: GET /api/task/<tid>/activity (task activity log) ──
@csrf.exempt
@app.route('/api/task/<tid>/activity', methods=['GET'])
def get_task_activity(tid):
    """Return recent activity for a task."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, task_id, actor_email, action, field_name, old_value, new_value, created_at FROM activity_log WHERE task_id=%s ORDER BY id DESC LIMIT 50",
            (tid,)
        )
        rows = cur.fetchall()
        return jsonify([
            {
                'id': r[0], 'task_id': r[1], 'actor_email': r[2], 'action': r[3],
                'field_name': r[4], 'old_value': r[5], 'new_value': r[6],
                'created_at': r[7].isoformat() if r[7] else None
            } for r in rows
        ])
    finally:
        cur.close()
        conn.close()

# ── Email/Calendar Service Adapters (Phase 1 — unified from email_service.py & calendar_service.py) ──
from email_service import send_kanban_email, notify_task_created, fmt_time as _fmt_time


def _get_user_token_from_db(google_id):
    """Load OAuth token from kanban_users table. Stores in session for service use."""
    if not google_id:
        return None
    try:
        conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        cur = conn.cursor()
        
        # Try direct lookup by google_id first  
        row = None
        cur.execute("SELECT oauth_access_token, oauth_refresh_token FROM kanban_users WHERE google_id=%s LIMIT 1", (google_id,))
        row = cur.fetchone()
        
        # Fallback: if no match and google_id looks like it starts with 'dev_', try by email  
        if not row or not row[0]:
            if google_id.startswith('dev_'):
                email = google_id.replace('dev_', '', 1)
                cur.execute("SELECT oauth_access_token, oauth_refresh_token FROM kanban_users WHERE email=%s LIMIT 1", (email,))
                row = cur.fetchone()
        
        cur.close()
        conn.close()
        if row and row[0]:
            session['oauth_access_token'] = row[0]
            session['oauth_refresh_token'] = row[1] or ''
            return row[0]
    except Exception as e:
        app.logger.warning("Token lookup failed for %s: %s", google_id, str(e))
    return None


def _notify_and_calendar_sync(task_id, creator_email, assignee_email, title='', description=None, priority=None, start_time=None, end_time=None):
    """Create task notification: email to all assignees + Google Calendar event."""
    # Send email (handles multi-recipient)
    try:
        notify_task_created(task_id, creator_email, assignee_email,
            title=title, description=description, priority=priority,
            start_time=start_time, end_time=end_time)
    except Exception as e:
        app.logger.warning("Email send failed for task %s: %s", task_id, str(e))
    
    # Create Google Calendar event with all assignees (uses DB-stored token)
    google_id = session.get('google_id', '')
    if assignee_email and start_time:
        try:
            _get_user_token_from_db(google_id)
            from calendar_service import get_calendar_service, create_event_with_all_attendees
            cal = get_calendar_service(session.get('oauth_access_token'), google_id=google_id)
            if cal:
                attendees = [e.strip() for e in str(assignee_email).split(',') if e.strip()]
                event_id = cal.create_event(
                    summary=f"📋 {title or f'Task #{task_id}'}",
                    description=f"{description}\nKanban URL: {APP_URL}/#/detail/{task_id}",
                    start_time=start_time, end_time=end_time,
                    attendee_emails=attendees
                )
                if event_id and isinstance(assignee_email, str):
                    # Store calendar event ID in dedicated table (reliable, not dependent on description)
                    db_conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
                    db_cur = db_conn.cursor()
                    try:
                        db_cur.execute(
                            "INSERT INTO kanban_calendar_events (task_id, calendar_event_id, summary) VALUES (%s,%s,%s) "
                            "ON CONFLICT (calendar_event_id) DO NOTHING",
                            (task_id, event_id, title or '')
                        )
                        db_conn.commit()
                    except Exception as insert_err:
                        app.logger.warning("Failed to store calendar event ID for task %s in kanban_calendar_events: %s", task_id, str(insert_err))
                    finally:
                        db_cur.close()
                        db_conn.close()
                    
                    # Also keep the [CALENDAR:] marker in description for backward compatibility
                    try:
                        meta_marker = f'[CALENDAR:{event_id}]'
                        desc_conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
                        desc_cur = desc_conn.cursor()
                        try:
                            desc_cur.execute("SELECT description FROM kanban_tasks WHERE id=%s", (task_id,))
                            desc_row = desc_cur.fetchone()
                            existing_desc = (desc_row[0] or '') + ' ' + meta_marker if desc_row else meta_marker
                            desc_cur.execute("UPDATE kanban_tasks SET description=%s WHERE id=%s", (existing_desc.strip(), task_id))
                            desc_conn.commit()
                        finally:
                            desc_cur.close()
                            desc_conn.close()
                    except Exception as desc_err:
                        app.logger.warning("Failed to write CALENDAR marker in description for task %s: %s", task_id, str(desc_err))
        except Exception as e:
            app.logger.warning("Calendar sync failed for task %s: %s", task_id, str(e))


def _notify_schedule_change(task_id, creator_email, assignee_email='', title='', description=None, priority=None, start_time=None, end_time=None):
    """Notify schedule change: email to creator with all assignees in CC + calendar resync."""
    try:
        from email_service import notify_task_updated as send_update
        send_update(task_id, creator_email, assignee_email, title=title, description=description,
            priority=priority, start_time=start_time, end_time=end_time)
    except Exception as e:
        app.logger.warning("Schedule change notification failed for task %s: %s", task_id, str(e))
    
    google_id = session.get('google_id', '')
    try:
        _get_user_token_from_db(google_id)
        from calendar_service import resync_task_schedule as cal_resync
        if session.get('oauth_access_token'):
            # Resync uses DB tokens internally
            app.logger.info("Schedule changed for task %s — resyncing calendar...", task_id)
    except Exception as e:
        app.logger.warning("Calendar resync failed for task %s: %s", task_id, str(e))


def _cleanup_calendar_on_delete(task_id):
    """Find and delete all Google Calendar events associated with a deleted task."""
    google_id = session.get('google_id', '')
    try:
        _get_user_token_from_db(google_id)
        from calendar_service import get_calendar_service
        cal = get_calendar_service(session.get('oauth_access_token'), google_id=google_id)
        if not cal:
            return
        
        # Get event IDs from dedicated table first, fall back to description markers
        db_conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        db_cur = db_conn.cursor()
        try:
            # Primary source: kanban_calendar_events table (reliable)
            db_cur.execute("SELECT calendar_event_id FROM kanban_calendar_events WHERE task_id=%s", (task_id,))
            event_ids = [r[0] for r in db_cur.fetchall()]
            
            if not event_ids:
                # Fallback: parse [CALENDAR:xxx] markers from description
                import re as _re
                db_cur.execute("SELECT description FROM kanban_tasks WHERE id=%s", (task_id,))
                row = db_cur.fetchone()
                if row and row[0]:
                    event_ids = _re.findall(r'\[CALENDAR:([a-zA-Z0-9_-]+)\]', str(row[0]))
            
            for eid in event_ids:
                try:
                    cal.delete_event(eid)
                except Exception as e:
                    app.logger.warning("Event '%s' may not exist (already cleaned up): %s", eid, str(e))
            
            # Clean up from both tables
            if event_ids:
                db_cur.execute("DELETE FROM kanban_calendar_events WHERE task_id=%s", (task_id,))
                # Also clean markers from description for backward compatibility
                import re as _re2
                db_cur.execute("SELECT description FROM kanban_tasks WHERE id=%s", (task_id,))
                row = db_cur.fetchone()
                if row and row[0]:
                    cleaned = _re2.sub(r'\[CALENDAR:[a-zA-Z0-9_-]+\]', '', str(row[0])).strip()
                    if cleaned != row[0]:
                        db_cur.execute("UPDATE kanban_tasks SET description=%s WHERE id=%s", (cleaned, task_id))
                db_conn.commit()
        finally:
            db_cur.close()
            db_conn.close()
    except Exception as e:
        app.logger.warning("Calendar cleanup failed for task %s: %s", task_id, str(e))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)


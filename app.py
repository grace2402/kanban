from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session
import os
import re
from datetime import datetime
from sqlalchemy import create_engine, text

import json as json_lib
import requests

# ── Centralized timezone helpers (single source of truth) ──
from timezone_utils import TAIPEI_TZ, ensure_taipei_offset

def _ensure_taipei_offset(ts_str):
    """Convert naive timestamp from frontend to explicit +08:00 (Taipei).
    
    Frontend sends naive timestamps like "2026-06-01T09:00" (no timezone).
    PostgreSQL with session TZ=UTC would interpret these as UTC, causing 8-hour offset.
    This function adds explicit +08:00 so DB stores the correct local time.
    
    Already-offset timestamps pass through unchanged.
    """
    if not ts_str:
        return ts_str
    
    s = str(ts_str)
    # Already has timezone info - pass through
    if 'T' in s and (s.endswith('Z') or '+' in s.split('T')[1] or '-' in s.split('T')[1].split('+')[0][-5:]):
        return ts_str
    
    # Naive timestamp - append +08:00 for Taipei
    try:
        if 'T' not in s:
            s = s.replace(' ', 'T')
        dt = datetime.fromisoformat(s)
        # Replace naive with Taipei-aware version  
        taipei_dt = datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, tzinfo=TAIPEI_TZ)
        return taipei_dt.isoformat()
    except (ValueError, TypeError):
        return ts_str


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY') or os.environ.get('SECRET_KEY', 'kanban-secret-key-change-me')
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

# ── Ensure database indexes for performance ──
def ensure_indexes():
    """Create performance indexes on frequently queried columns."""
    
    idx_map = [
        'CREATE INDEX IF NOT EXISTS idx_kanban_tasks_creator ON kanban_tasks(creator_email)',
        'CREATE INDEX IF NOT EXISTS idx_kanban_tasks_column ON kanban_tasks(column_name, creator_email)',
        'CREATE INDEX IF NOT EXISTS idx_calendar_events_task ON kanban_calendar_events(task_id)',
        'CREATE INDEX IF NOT EXISTS idx_subtasks_parent ON subtasks(parent_task_id)',
        'CREATE INDEX IF NOT EXISTS idx_kanban_users_email ON kanban_users(email)',
        'CREATE INDEX IF NOT EXISTS idx_kanban_users_google_id ON kanban_users(google_id) WHERE google_id IS NOT NULL',
    ]

    for sql in idx_map:
        try:
            conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
            cur = conn.cursor()
            cur.execute(sql)
            # IF NOT EXISTS silently skips if index exists; otherwise creates it
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            app.logger.info("Index status (%s): %s", sql[:50], 'exists' if 'duplicate' in str(e).lower() else f'skipped ({str(e)[:60]})')


# Run on startup (deferred until first request)
with app.app_context():
    try:
        # Ensure tasks table exists first, then calendar events + indexes
        conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        cur = conn.cursor()
        cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'kanban_tasks')")
        if cur.fetchone()[0]:
            ensure_calendar_events_table()
            ensure_indexes()
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

@app.route('/login/dev', methods=['POST'])
@csrf.exempt
def dev_login():
    """Development-only: skip OAuth, set session directly. Use only for local testing."""
    # Security: block in production unless explicitly enabled via DEV_MODE env var
    if not os.environ.get('DEV_MODE'):
        flash('Dev login 在正式環境不可用', 'error')
        return redirect(url_for('index'))

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
    # Security: block in production unless explicitly enabled via DEV_MODE env var
    if not os.environ.get('DEV_MODE'):
        from flask import render_template_string, request
        return render_template_string(
            '''<div style="font-family:sans-serif;text-align:center;padding:40px">
                <h2>Dev Login 已停用</h2>
                <p>此環境未設定 DEV_MODE 環境變數。</p>
                <a href="/">← Back to Kanban</a>
            </div>'''
        ), 403

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
        # Also allow /api/columns (schema endpoint, no auth needed)
        if request.path not in ('/api/users', '/api/calendar/delete-event', '/api/columns'):
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


# ── API: GET /api/tasks (list all tasks with labels) ──
@csrf.exempt
@app.route('/api/tasks', methods=['GET'])
def get_all_tasks():
    """Return all kanban tasks with their labels. Uses single JOIN query to avoid N+1."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Single query: LEFT JOIN + GROUP_CONCAT to fetch labels in one go (avoids N+1)
        cur.execute("""
            SELECT t.id, t.title, t.description, t.column_name, t.priority, t.assignee_email,
                   (t.start_time AT TIME ZONE 'UTC')::date as st_date,
                   (t.end_time AT TIME ZONE 'UTC')::date as et_date, t.sort_order,
                   COALESCE(ARRAY_TO_STRING(ARRAY_AGG(DISTINCT l.name || '|' || l.color), ','), '') as labels_csv
            FROM kanban_tasks t
            LEFT JOIN task_labels tl ON tl.task_id = t.id
            LEFT JOIN kanban_labels l ON tl.label_id = l.id
            GROUP BY t.id, t.title, t.description, t.column_name, t.priority, t.assignee_email,
                     st_date, et_date, t.sort_order
            ORDER BY t.column_name, t.sort_order
        """)
        rows = cur.fetchall()
        
        tasks = []
        for r in rows:
            tid, title, desc, col, priority, assignee, start_t, end_t, sort_o, labels_csv = r
            
            # Parse comma-separated "name|color" pairs into label list
            labels = []
            if labels_csv:
                for pair in labels_csv.split(','):
                    parts = pair.split('|', 1)
                    if len(parts) == 2 and parts[0]:
                        labels.append({'name': parts[0], 'color': parts[1]})
            
            st_str = str(start_t) if start_t else None
            et_str = str(end_t) if end_t else None
            
            tasks.append({
                'id': str(tid),
                'title': title or '',
                'description': desc or '',
                'column': col,
                'priority': priority or 'medium',
                'assignee_email': assignee or '',
                'start_time': st_str,
                'end_time': et_str,
                'sort_order': sort_o or 0,
                'labels': labels,
            })
        
        return jsonify(tasks)
    finally:
        cur.close()
        conn.close()


# ── API: GET /api/tasks (list all tasks with labels) — alias for frontend compat ──

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

    # Use AT TIME ZONE 'UTC' for timezone-safe date extraction.
    # This ensures dates are ALWAYS interpreted as UTC regardless of PostgreSQL session timezone,
    # preventing the bug where end_time='2026-06-02T00:00+08:00' (Taipei) would show as June 3
    # if the DB session TZ was set to Asia/Taipei.
    # Also fixed: changed filter from "end >= next_month" to "end > month_start" so single-month tasks appear.
    if user_email:
        like_pattern = '%' + user_email + '%'
        query = """
            SELECT id, title, description, priority, assignee_email, 
                   (start_time AT TIME ZONE 'UTC')::date as st_date, 
                   (end_time AT TIME ZONE 'UTC')::date as et_date
            FROM kanban_tasks
            WHERE (%s < COALESCE((end_time AT TIME ZONE 'UTC')::date, date '9999-12-31'))
              AND (COALESCE((start_time AT TIME ZONE 'UTC')::date, date '1970-01-01') < %s)
              AND assignee_email LIKE %s
            ORDER BY start_time ASC
        """
        cur.execute(query, (start_date, end_date, like_pattern))
    else:
        query = """
            SELECT id, title, description, priority, assignee_email, 
                   (start_time AT TIME ZONE 'UTC')::date as st_date, 
                   (end_time AT TIME ZONE 'UTC')::date as et_date
            FROM kanban_tasks
            WHERE (%s < COALESCE((end_time AT TIME ZONE 'UTC')::date, date '9999-12-31'))
              AND (COALESCE((start_time AT TIME ZONE 'UTC')::date, date '1970-01-01') < %s)
            ORDER BY start_time ASC
        """
        cur.execute(query, (start_date, end_date))

    rows = cur.fetchall()
    tasks = []
    for r in rows:
        st = str(r[5])  # Already a date object from ::date cast — no tz issue
        et = str(r[6])
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



# ── API: GET /api/stats (dashboard statistics) ──
@csrf.exempt
@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Return dashboard statistics."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Count by column
        cur.execute("SELECT column_name, COUNT(*) FROM kanban_tasks GROUP BY column_name")
        col_counts = {r[0]: r[1] for r in cur.fetchall()}
        
        # Total tasks
        total = sum(col_counts.values()) or 0
        
        # Overdue count (end_time passed)
        try:
            cur.execute("SELECT COUNT(*) FROM kanban_tasks WHERE end_time < NOW() AND column_name != 'done'")
            overdue_count = cur.fetchone()[0]
        except Exception:
            overdue_count = 0
        
        # Priority breakdown
        try:
            cur.execute("SELECT priority, COUNT(*) FROM kanban_tasks GROUP BY priority")
            priority_counts = {r[0]: r[1] for r in cur.fetchall()}
        except Exception:
            priority_counts = {}
        
        # Recent activity (last 10)
        try:
            sql_activity = """SELECT al.task_id, t.title, al.action, al.created_at, 
                                  al.actor_email FROM activity_log al
                           JOIN kanban_tasks t ON al.task_id=t.id
                           ORDER BY al.created_at DESC LIMIT 10"""
            cur.execute(sql_activity)
            recent = []
            for r in cur.fetchall():
                created_str = r[3].isoformat() if hasattr(r[3], 'isoformat') else str(r[3])
                recent.append({
                    'task_id': r[0], 
                    'title': r[1] or '(deleted)',
                    'action': r[2],
                    'time': created_str,
                    'actor': r[4] or ''
                })
        except Exception:
            recent = []
        
        return jsonify({
            'total': total,
            'by_column': col_counts,
            'overdue_count': overdue_count,
            'priority_breakdown': priority_counts,
            'recent_activity': recent
        })
    finally:
        cur.close()
        conn.close()


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

    # Get distinct assignee emails for tasks in this month (use AT TIME ZONE UTC for timezone safety)
    cur.execute("""
        SELECT DISTINCT assignee_email
        FROM kanban_tasks
        WHERE (%s < COALESCE((end_time AT TIME ZONE 'UTC')::date, date '9999-12-31'))
          AND (COALESCE((start_time AT TIME ZONE 'UTC')::date, date '1970-01-01') < %s)
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
             _ensure_taipei_offset(data.get('start_time')),
             _ensure_taipei_offset(data.get('end_time')),
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
    
    # Notify assignee that task was created for them (email + calendar) — use converted timestamps
    _notify_and_calendar_sync(tid, session.get('user_email', ''), data.get('assignee_email'), 
        title=data.get('title', ''), description=data.get('description', ''),
        priority=data.get('priority', None), start_time=_ensure_taipei_offset(data.get('start_time')),
        end_time=_ensure_taipei_offset(data.get('end_time')))

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
                # Handle time fields - convert to ISO format with Taipei offset
                if api_key in ('start_time', 'end_time') and val is not None:
                    val = _ensure_taipei_offset(val)
                set_clauses.append(f"{db_col}=%s")
                params.append(val if val is not None else '')
        
        if set_clauses:
            query = f"UPDATE kanban_tasks SET {', '.join(set_clauses)} WHERE id=%s"
            params.append(tid)
            cur.execute(query, params)
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
        new_start_str = _ensure_taipei_offset(data.get('start_time')) if data.get('start_time') else None
        new_end_str = _ensure_taipei_offset(data.get('end_time')) if data.get('end_time') else None
        
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
            
            app.logger.debug("DELETE task %s: found %d calendar event(s) via kanban_calendar_events table", 
                tid, len(extracted_event_ids))
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
    
    # ── Collect ALL event IDs to delete (email URL + any orphaned entries) ──
    db_conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    db_cur = db_conn.cursor()
    
    try:
        # Get any additional event IDs from kanban_calendar_events table (for tasks already deleted)
        db_cur.execute(
            "SELECT calendar_event_id FROM kanban_calendar_events WHERE task_id=%s",
            (task_id,)
        )
        orphaned_ids = [r[0] for r in db_cur.fetchall()]
        
        # Merge with email-provided IDs (deduplicate)
        all_calendar_event_ids = list(set(all_calendar_event_ids + orphaned_ids))
    finally:
        db_cur.close()
        db_conn.close()
    
    if not all_calendar_event_ids:
        return jsonify({
            'status': 'ok',
            'deleted_count': 0,
            'message': '沒有需要刪除的 Google Calendar 事件 (可能已清除)'
        })
    
    app.logger.info("Deleting %d calendar event(s) for task %s via email link", len(all_calendar_event_ids), task_id)
    
    # ── Try ALL users' OAuth tokens until one succeeds at deletion from Google Calendar API ──
    # (handles case where current session user != creator of the calendar events)
    successfully_deleted = []
    failed_deletions = []
    last_error = None
    
    try:
        db_conn2 = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        db_cur2 = db_conn2.cursor()
        
        # Get all users with valid OAuth tokens (access_token + refresh_token), including google_id for token matching
        db_cur2.execute("""
            SELECT google_id, oauth_access_token, oauth_refresh_token 
            FROM kanban_users 
            WHERE oauth_access_token IS NOT NULL 
              AND oauth_refresh_token IS NOT NULL
            ORDER BY google_id DESC
        """)
        user_tokens = db_cur2.fetchall()  # each row: (google_id, access_token, refresh_token)
        db_cur2.close()
        
        if not user_tokens:
            flash('找不到有效的 OAuth 權限，無法刪除 Google Calendar 事件', 'warning')
            return redirect(url_for('index'))
        
        # Try each user's token until deletion succeeds
        all_deleted = False
        
        for google_id, access_token, refresh_token in user_tokens:
            try:
                from calendar_service import get_calendar_service
                
                cal = get_calendar_service(access_token, google_id=google_id)  # pass google_id for correct token refresh matching
                if not cal:
                    app.logger.debug("Token failed verify/refresh for delete-event link")
                    continue
                
                # Try to delete all events with this user's token
                deleted_this_user = 0
                errors_for_this_user = []
                
                for calendar_event_id in all_calendar_event_ids:
                    try:
                        result = cal.delete_event(calendar_event_id)
                        if result:
                            deleted_count += 1
                            deleted_this_user += 1
                            successfully_deleted.append(calendar_event_id)
                            app.logger.info("Successfully deleted event '%s' from Google Calendar", calendar_event_id)
                        else:
                            errors_for_this_user.append(calendar_event_id)
                            app.logger.warning("Event '%s' returned False (may not exist or permission denied)", calendar_event_id)
                    except Exception as e:
                        errors_for_this_user.append(calendar_event_id)
                        last_error = str(e)
                
                if deleted_this_user > 0:
                    all_deleted = True
                    failed_deletions.extend(errors_for_this_user)
                    app.logger.info("Delete-event link: successfully deleted %d/%d events", 
                                   deleted_this_user, len(all_calendar_event_ids))
                    break  # Success! No need to try other users
            
            except Exception as e:
                last_error = str(e)
                continue
        
        if not all_deleted and last_error:
            failed_deletions.extend([eid for eid in all_calendar_event_ids if eid not in successfully_deleted])
    
    finally:
        # ── Clean up kanban_calendar_events table (only remove entries for successfully deleted events) ──
        try:
            db_conn3 = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
            db_cur3 = db_conn3.cursor()
            
            if successfully_deleted:
                placeholders = ','.join(['%s'] * len(successfully_deleted))
                db_cur3.execute(
                    f"DELETE FROM kanban_calendar_events WHERE task_id=%s AND calendar_event_id IN ({placeholders})",
                    [task_id] + successfully_deleted
                )
            
            # Also clean [CALENDAR:xxx] markers from description (if task row still exists)
            try:
                import re as _re2
                db_cur3.execute("SELECT description FROM kanban_tasks WHERE id=%s", (task_id,))
                row = db_cur3.fetchone()
                if row and row[0]:
                    cleaned = str(row[0])
                    for eid in successfully_deleted:
                        cleaned = _re2.sub(r'\[CALENDAR:' + re.escape(eid) + r'\]', '', cleaned).strip()
                    if len(successfully_deleted) > 0 and cleaned != row[0]:
                        db_cur3.execute("UPDATE kanban_tasks SET description=%s WHERE id=%s", (cleaned, task_id))
            except Exception as e:
                app.logger.warning("Failed to clean markers for event(s): %s", str(e))
            
            try:
                db_conn3.commit()
            except Exception:
                pass  # non-critical if description cleanup fails
            
            finally:
                db_cur3.close()
                db_conn3.close()
        except Exception as e:
            app.logger.error("Failed to clean DB for delete-event link: %s", str(e))
    
    return jsonify({
        'status': 'ok',
        'deleted_count': deleted_count,
        'message': f'已處理 {deleted_count}/{len(all_calendar_event_ids)} 個 Google Calendar 事件' + (
            '' if deleted_count > 0 else ' (事件可能已被其他使用者清除)'
        ) + ('; 有 {} 個刪除失敗，請確認 OAuth 權限'.format(len(failed_deletions)) if failed_deletions else ''),
    })


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


# ── API: GET /api/tasks/export/csv (export tasks as CSV) ──
@csrf.exempt
@app.route('/api/tasks/export/csv', methods=['GET'])
def export_tasks_csv():
    """Export all visible tasks as a CSV file."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Get all tasks with their details
        cur.execute("""SELECT t.id, t.title, t.description, t.column_name, t.priority, 
                              t.assignee_email, t.start_time, t.end_time
                       FROM kanban_tasks t ORDER BY t.sort_order""")
        rows = cur.fetchall()
        
        # Get labels for all tasks
        task_labels = {}
        cur.execute("""SELECT tl.task_id, l.name FROM task_labels tl 
                      JOIN kanban_labels l ON tl.label_id=l.id""")
        for r in cur.fetchall():
            tid = str(r[0])
            if tid not in task_labels:
                task_labels[tid] = []
            task_labels[tid].append(r[1])
        
        # Get subtask info for all tasks
        task_subtasks = {}
        cur.execute("SELECT parent_task_id, title, is_completed FROM subtasks")
        for r in cur.fetchall():
            tid = str(r[0])
            if tid not in task_subtasks:
                task_subtasks[tid] = []
            done_str = "✓" if r[2] else "✗"
            task_subtasks[tid].append(f"{done_str} {r[1]}")
        
        # Build CSV content
        import io as _io
        output = _io.StringIO()
        writer = __import__('csv').Writer(output)
        writer.writerow(['ID', '標題', '描述', '欄位', '優先級', '負責人', '開始時間', '結束時間', '標籤', '子任務'])
        
        for r in rows:
            tid, title, desc, col, priority, assignee, start_t, end_t = r
            labels_str = ', '.join(task_labels.get(str(tid), []))
            subs_str = '\n'.join(task_subtasks.get(str(tid), []))
            writer.writerow([
                str(tid), 
                (title or '').replace('\n', ' '),
                (desc or '').replace('\n', ' '),
                col or '', priority or '', assignee or '',
                start_t.strftime('%Y-%m-%d %H:%M') if start_t else '',
                end_t.strftime('%Y-%m-%d %H:%M') if end_t else '',
                labels_str, subs_str
            ])
        
        csv_bytes = output.getvalue().encode('utf-8-sig')  # UTF-8 BOM for Excel compatibility
        response = __import__('flask').make_response(csv_bytes)
        response.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
        response.headers['Content-Disposition'] = f'attachment; filename=kanban-tasks-{__import__("datetime").datetime.now().strftime("%Y-%m-%d")}.csv'
        return response
    except Exception as e:
        app.logger.error("CSV export failed: %s", str(e))
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


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
    app.logger.info("[CALENDAR_SYNC] task=%s assignee=%s start_time=%s google_id=%s", task_id, assignee_email, start_time, google_id)
    if assignee_email and start_time:
        try:
            _get_user_token_from_db(google_id)
            from calendar_service import get_calendar_service, create_event_with_all_attendees
            token = session.get('oauth_access_token')
            app.logger.info("[CALENDAR_SYNC] oauth_token=%s cal_before=%s", 'yes' if token else 'no', google_id)
            cal = get_calendar_service(token, google_id=google_id)
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
            app.logger.info("Schedule changed for task %s — resyncing calendar...", task_id)
            # Actually call the resync function (was previously only checking token availability)
            result = cal_resync(google_id=google_id, task_id=task_id)
            app.logger.info("Schedule resync for task %s: %s", task_id, "success" if result else "failed")
        else:
            app.logger.debug("No OAuth token available for schedule change on task %s — skipping calendar resync", task_id)
    except Exception as e:
        app.logger.warning("Calendar resync failed for task %s: %s", task_id, str(e))


def _cleanup_calendar_on_delete(task_id):
    """Find and delete all Google Calendar events associated with a deleted task.
    
    Tries ALL users' OAuth tokens from kanban_users table until one succeeds
    at deleting from Google Calendar API (handles case where deleter != creator).
    """
    # Get event IDs first (before any deletion attempts)
    event_ids = []
    try:
        db_conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        db_cur = db_conn.cursor()
        
        # Primary source: kanban_calendar_events table (reliable)
        db_cur.execute("SELECT calendar_event_id FROM kanban_calendar_events WHERE task_id=%s", (task_id,))
        event_ids = [r[0] for r in db_cur.fetchall()]
        
        if not event_ids:
            # Fallback: parse [CALENDAR:xxx] markers from description
            import re as _re2
            db_cur.execute("SELECT description FROM kanban_tasks WHERE id=%s", (task_id,))
            row = db_cur.fetchone()
            if row and row[0]:
                event_ids = _re2.findall(r'\[CALENDAR:([a-zA-Z0-9_-]+)\]', str(row[0]))
        
        app.logger.info("Cleanup task %s: found %d calendar event(s)", task_id, len(event_ids))
    except Exception as e:
        app.logger.error("Failed to get calendar IDs for task %s: %s", task_id, str(e))
    
    if not event_ids:
        # Nothing to delete from Google Calendar
        return
    
    # ── Try ALL users' OAuth tokens until one succeeds at deletion ──
    try:
        from sqlalchemy import text as _text
        db_conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        db_cur = db_conn.cursor()
        
        # Get all users with valid OAuth tokens (access_token + refresh_token), including google_id for token matching
        db_cur.execute("""
            SELECT google_id, oauth_access_token, oauth_refresh_token 
            FROM kanban_users 
            WHERE oauth_access_token IS NOT NULL 
              AND oauth_refresh_token IS NOT NULL
            ORDER BY google_id DESC
        """)
        user_tokens = db_cur.fetchall()  # each row: (google_id, access_token, refresh_token)
        db_cur.close()
        
        if not user_tokens:
            app.logger.warning("No users with valid OAuth tokens found for calendar cleanup task %s", task_id)
            return
        
        # Try each user's token until deletion succeeds
        all_deleted = False
        last_error = None
        
        for google_id, access_token, refresh_token in user_tokens:
            try:
                from calendar_service import get_calendar_service
                
                cal = get_calendar_service(access_token, google_id=google_id)  # pass google_id for correct token refresh matching
                if not cal:
                    app.logger.debug("Token failed verify/refresh for cleanup task %s", task_id)
                    continue
                
                # Try to delete all events with this user's token
                deleted_this_user = 0
                for eid in event_ids:
                    try:
                        result = cal.delete_event(eid)
                        if result:
                            deleted_this_user += 1
                            app.logger.info("Deleted Google Calendar event '%s' (user token)", eid)
                    except Exception as e:
                        last_error = str(e)
                
                if deleted_this_user > 0:
                    all_deleted = True
                    app.logger.info("Cleanup task %s: successfully deleted %d/%d events", 
                                   task_id, deleted_this_user, len(event_ids))
                    break  # Success! No need to try other users
            
            except Exception as e:
                last_error = str(e)
                continue
        
        if not all_deleted and last_error:
            app.logger.warning("Calendar cleanup for task %s failed after trying %d tokens. Last error: %s", 
                             task_id, len(user_tokens), last_error)
        
    except Exception as e:
        app.logger.error("Failed to iterate OAuth tokens for calendar cleanup task %s: %s", task_id, str(e))
    
    # ── Clean up kanban_calendar_events table and description markers ──
    try:
        db_conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        db_cur = db_conn.cursor()
        
        if event_ids:
            db_cur.execute("DELETE FROM kanban_calendar_events WHERE task_id=%s", (task_id,))
            
            # Clean [CALENDAR:xxx] markers from description for backward compatibility
            import re as _re3
            db_cur.execute("SELECT description FROM kanban_tasks WHERE id=%s", (task_id,))
            row = db_cur.fetchone()
            if row and row[0]:
                cleaned = _re3.sub(r'\[CALENDAR:[a-zA-Z0-9_-]+\]', '', str(row[0])).strip()
                if cleaned != row[0]:
                    db_cur.execute("UPDATE kanban_tasks SET description=%s WHERE id=%s", (cleaned, task_id))
            
            db_conn.commit()
    except Exception as e:
        app.logger.error("Failed to clean DB records for calendar cleanup task %s: %s", task_id, str(e))


# ── API: POST /api/task/<tid>/clone (clone task with subtasks and labels) ──
@csrf.exempt
@app.route('/api/task/<tid>/clone', methods=['POST'])
def clone_task(tid):
    """Clone a task including its subtasks, labels, and schedule."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Get original task data
        cur.execute("""SELECT id, title, description, column_name, priority, assignee_email, 
                              start_time, end_time FROM kanban_tasks WHERE id=%s""", (tid,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'task not found'}), 404
        
        orig_id, title, desc, col, priority, assignee, start_t, end_t = row
        
        # Generate new ID and clone task
        new_title = f"{title} (副本)"
        cur.execute("""INSERT INTO kanban_tasks (id, title, description, column_name, priority, 
                             assignee_email, start_time, end_time)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (f"clone_{tid}_{int(datetime.now().timestamp())}", new_title, desc, col,
                     priority, assignee, start_t, end_t))
        new_task_id = cur.fetchone()[0]

        # Clone subtasks
        cur.execute("""SELECT title, is_completed FROM subtasks WHERE parent_task_id=%s ORDER BY sort_order""", (tid,))
        subtasks = cur.fetchall()
        for i, (st_title, st_done) in enumerate(subtasks):
            try:
                from uuid import uuid4
                sub_id = str(uuid4())[:8]
                cur.execute("""INSERT INTO subtasks (id, parent_task_id, title, is_completed, sort_order) 
                               VALUES (%s,%s,%s,%s,%s)""",
                            (sub_id, new_task_id, st_title, st_done, i))
            except Exception:
                pass

        # Clone labels
        cur.execute("""SELECT l.name, l.color FROM kanban_labels l
                       JOIN task_labels tl ON tl.label_id=l.id WHERE tl.task_id=%s""", (tid,))
        labels = cur.fetchall()
        for lbl_name, lbl_color in labels:
            try:
                # Get or create label
                cur.execute("SELECT id FROM kanban_labels WHERE name=%s", (lbl_name,))
                lr = cur.fetchone()
                if lr:
                    label_id = lr[0]
                else:
                    cur.execute("""INSERT INTO kanban_labels (name, color) VALUES (%s,%s) RETURNING id""",
                                (lbl_name, lbl_color))
                    label_id = cur.fetchone()[0]
                
                # Assign to new task
                try:
                    from uuid import uuid4
                    tl_id = str(uuid4())[:8]
                    cur.execute("""INSERT INTO task_labels (id, task_id, label_id) VALUES (%s,%s,%s)""",
                                (tl_id, new_task_id, label_id))
                except Exception:
                    pass  # already exists
            except Exception:
                pass

        conn.commit()
        
        # Log activity for cloned task
        try:
            cur2 = conn.cursor()
            try:
                cur2.execute("""INSERT INTO activity_log (task_id, actor_email, action, field_name, old_value, new_value) 
                               VALUES (%s,%s,%s,%s,%s,%s)""",
                             (new_task_id, session.get('user_email', ''), 'cloned', 'clone', f"Task {orig_id}", "Cloned"))
                conn.commit()
            finally:
                cur2.close()
        except Exception as e:
            app.logger.warning("Activity log failed for clone task %s: %s", new_task_id, str(e))

        return jsonify({'id': new_task_id, 'title': new_title})
    except Exception as e:
        conn.rollback()
        app.logger.error("Clone task failed: %s", str(e))
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()



# ── Phase 5: Ensure kanban_columns table exists (dynamic columns) ──
def ensure_kanban_columns_table():
    """Create kanban_columns table for dynamic column management."""
    try:
        conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        cur = conn.cursor()
        
        # CREATE TABLE if it doesn't exist
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kanban_columns (
                id SERIAL PRIMARY KEY,
                name VARCHAR(50) UNIQUE NOT NULL,
                display_name VARCHAR(100),
                sort_order INTEGER NOT NULL DEFAULT 0
            )
        """)
        
        # Insert default columns if empty
        cur.execute("SELECT COUNT(*) FROM kanban_columns")
        count = cur.fetchone()[0]
        if count == 0:
            default_cols = ['backlog', 'todo', 'in_progress', 'review', 'done']
            for i, col_name in enumerate(default_cols):
                cur.execute(
                    """INSERT INTO kanban_columns (name, display_name, sort_order)
                       VALUES (%s,%s,%s)""",
                    (col_name, _get_display_name(col_name), i + 1)
                )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        app.logger.warning("Failed to ensure kanban_columns table: %s", str(e))


# ── Phase 5: Ensure kanban_comments table exists (task comments) ──
def ensure_kanban_comments_table():
    """Create kanban_comments table for task-level commenting."""
    try:
        conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kanban_comments (
                id SERIAL PRIMARY KEY,
                task_id VARCHAR(50) NOT NULL,
                actor_email TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        app.logger.warning("Failed to ensure kanban_comments table: %s", str(e))


# ── Helper: column display name mapping ──
def _get_display_name(col_name):
    """Get human-readable display name for a column slug."""
    names = {
        'backlog': 'Backlog',
        'todo': 'To Do',
        'in_progress': 'In Progress',
        'review': 'Review',
        'done': 'Done',
    }
    return names.get(col_name, col_name.replace('_', ' ').title())


# ── Phase 5 API: GET /api/columns (list all kanban columns) ──
@csrf.exempt
@app.route('/api/columns', methods=['GET'])
def get_columns():
    """Return all kanban column definitions."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT name, display_name, sort_order FROM kanban_columns ORDER BY sort_order")
        rows = cur.fetchall()
        return jsonify([{'name': r[0], 'display_name': r[1] or r[0].replace('_', ' ').title(), 'sort_order': r[2]} for r in rows])
    finally:
        cur.close()
        conn.close()


# ── Phase 5 API: POST /api/column (create column) ──
@csrf.exempt
@app.route('/api/column', methods=['POST'])
def create_column():
    """Create a new kanban column."""
    data = request.json or {}
    name = data.get('name', '').strip().lower().replace(' ', '_')
    display_name = data.get('display_name', name.replace('_', ' ').title())
    if not name:
        return jsonify({'error': 'missing name'}), 400

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Check for duplicate slug
        cur.execute("SELECT COUNT(*) FROM kanban_columns WHERE name=%s", (name,))
        if cur.fetchone()[0] > 0:
            return jsonify({'error': 'column already exists'}), 409

        # Get max sort_order + 1
        cur.execute("SELECT COALESCE(MAX(sort_order), 0) FROM kanban_columns")
        new_order = cur.fetchone()[0] + 1

        cur.execute(
            """INSERT INTO kanban_columns (name, display_name, sort_order) VALUES (%s,%s,%s) RETURNING id""",
            (name, display_name, new_order)
        )
        lid = cur.fetchone()[0]
        conn.commit()
        return jsonify({'id': lid, 'name': name, 'display_name': display_name}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()


# ── Phase 5 API: PUT /api/column/<name> (update column) ──
@csrf.exempt
@app.route('/api/column/<col_name>', methods=['PUT'])
def update_column(col_name):
    """Update a kanban column's display name or sort order."""
    data = request.json or {}
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        updates = []
        params = []
        if 'display_name' in data and data['display_name']:
            updates.append("display_name=%s")
            params.append(data['display_name'])
        if 'sort_order' in data and data['sort_order'] is not None:
            updates.append("sort_order=%s")
            params.append(int(data['sort_order']))

        if not updates:
            return jsonify({'error': 'nothing to update'}), 400

        params.append(col_name)
        query = f"UPDATE kanban_columns SET {', '.join(updates)} WHERE name=%s"
        cur.execute(query, params)
        conn.commit()
        return jsonify({'status': 'ok', 'updated': cur.rowcount > 0})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()


# ── Phase 5 API: DELETE /api/column/<name> (delete column) ──
@csrf.exempt
@app.route('/api/column/<col_name>', methods=['DELETE'])
def delete_column(col_name):
    """Delete a kanban column. Tasks in it are moved to 'todo' first."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Move tasks out of this column first
        cur.execute(
            "UPDATE kanban_tasks SET column_name='todo' WHERE column_name=%s AND column_name != 'done'",
            (col_name,)
        )

        # Delete the column definition
        cur.execute("DELETE FROM kanban_columns WHERE name=%s", (col_name,))
        conn.commit()
        return jsonify({'status': 'ok', 'moved_tasks': cur.rowcount})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()


# ── Phase 5 API: POST /api/tasks/batch-priority (batch update priority) ──
@csrf.exempt
@app.route('/api/tasks/batch-priority', methods=['POST'])
def batch_update_priority():
    """Batch update priority for selected tasks."""
    data = request.json or {}
    task_ids = data.get('task_ids', [])
    priority = data.get('priority')
    if not task_ids or not priority:
        return jsonify({'error': 'missing task_ids or priority'}), 400

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    updated = 0
    try:
        for tid in task_ids:
            # Read old priority before update (for activity logging)
            cur.execute("SELECT title, priority FROM kanban_tasks WHERE id=%s", (tid,))
            row = cur.fetchone()
            if not row:
                continue

            old_priority = row[1] or 'medium'
            new_priority = priority.lower().strip()
            if new_priority not in ('high', 'medium', 'low'):
                continue

            try:
                cur2 = conn.cursor()
                try:
                    cur2.execute(
                        "UPDATE kanban_tasks SET priority=%s WHERE id=%s",
                        (new_priority, tid)
                    )
                    conn.commit()

                    # Log activity for each task
                    if row[1] != new_priority:
                        cur3 = conn.cursor()
                        try:
                            cur3.execute(
                                "INSERT INTO activity_log (task_id, actor_email, action, field_name, old_value, new_value) VALUES (%s,%s,%s,%s,%s,%s)",
                                (tid, session.get('user_email', ''), 'updated', 'priority', old_priority, new_priority)
                            )
                            conn.commit()
                        finally:
                            cur3.close()

                    updated += 1
                finally:
                    cur2.close()
            except Exception:
                continue

        return jsonify({'updated': updated})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()


# ── Phase 5 API: POST /api/task/<tid>/comment (add comment) ──
@csrf.exempt
@app.route('/api/task/<tid>/comment', methods=['POST'])
def add_comment(tid):
    """Add a comment to a task."""
    data = request.json or {}
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'error': 'missing content'}), 400

    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        # Verify task exists
        cur.execute("SELECT title FROM kanban_tasks WHERE id=%s", (tid,))
        if not cur.fetchone():
            return jsonify({'error': 'task not found'}), 404

        cur.execute(
            """INSERT INTO kanban_comments (task_id, actor_email, content) VALUES (%s,%s,%s) RETURNING id""",
            (tid, session.get('user_email', ''), content)
        )
        cid = cur.fetchone()[0]
        conn.commit()

        # Log activity
        try:
            cur2 = conn.cursor()
            try:
                cur2.execute(
                    "INSERT INTO activity_log (task_id, actor_email, action, field_name, old_value, new_value) VALUES (%s,%s,%s,%s,%s,%s)",
                    (tid, session.get('user_email', ''), 'commented', None, '📝 新增留言', content[:80])
                )
                conn.commit()
            finally:
                cur2.close()
        except Exception as e:
            app.logger.warning("Activity log failed for comment on task %s: %s", tid, str(e))

        return jsonify({'id': cid}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()


# ── Phase 5 API: GET /api/task/<tid>/comments (list comments for task) ──
@csrf.exempt
@app.route('/api/task/<tid>/comments', methods=['GET'])
def get_comments(tid):
    """Return all comments for a task."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, actor_email, content, created_at FROM kanban_comments WHERE task_id=%s ORDER BY created_at ASC",
            (tid,)
        )
        rows = cur.fetchall()
        return jsonify([
            {
                'id': r[0],
                'actor_email': r[1] or '',
                'content': r[2] or '',
                'created_at': r[3].isoformat() if r[3] else None,
            } for r in rows
        ])
    finally:
        cur.close()
        conn.close()


# ── Phase 5 API: DELETE /api/comment/<cid> (delete comment) ──
@csrf.exempt
@app.route('/api/comment/<int:cid>', methods=['DELETE'])
def delete_comment(cid):
    """Delete a comment."""
    conn = create_engine(app.config['SQLALCHEMY_DATABASE_URI']).raw_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM kanban_comments WHERE id=%s", (cid,))
        conn.commit()
        return jsonify({'deleted': cur.rowcount > 0})
    finally:
        cur.close()
        conn.close()


# Ensure Phase 5 tables on module import (gunicorn imports but doesn't run __main__)
try:
    ensure_kanban_columns_table()
    ensure_kanban_comments_table()
except Exception as e:
    app.logger.warning("Phase 5 table init failed at import: %s", str(e))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)


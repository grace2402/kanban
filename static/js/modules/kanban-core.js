// ==========================================
// Kanban Board - Module: Core Utilities & Init
// ==========================================
// Handles: constants, helper functions, task loading/syncing, dashboard stats init
// ==========================================

/* Global state */
var tasks = [];
var editingId = null;
var currentColumn = 'backlog';
var batchMode = false;
var selectedTaskIds = {};
var searchQuery = '';
var priorityFilter = '';
var labelFilter = '';
var allLabelsCache = [];
var allColumns = [];

const PRIORITY_LABELS = { high: '\u9ad8', medium: '\u4e2d', low: '\u4f4e' }; // 高/中/低
var DEFAULT_COLUMNS = ['backlog', 'todo', 'in_progress', 'review', 'done'];
var COLUMN_ICONS = {'backlog':'\u{1f4cb}','todo':'\u{1f4dd}','in_progress':'\u{1f528}','review':'\u{1f50d}','done':'\u2705'};

const DEFAULT_TASKS = [
    {id:'t1', title:'\u6b77\u53f2\u6578\u64da\u8cc7\u6599\u5eab\u5316', description:'\u5c07 MQTT \u6b77\u53f2\u8cc7\u6599\u5b58\u5165 SQLite/PostgreSQL\uff0c\u53d6\u4ee3 localStorage', column:'backlog', priority:'high'},
    {id:'t2', title:'\u8b66\u5831\u901a\u77e5\u7cfb\u7d71', description:'SOC/SOH/\u6eab\u5ea6\u7570\u5e38\u6642\u89f8\u767c Telegram Line \u901a\u77e5', column:'backlog', priority:'high'},
    {id:'t3', title:'\u6578\u64da\u6c47\u51fa\u529f\u80fd', description:'CSV/PDF \u6c47\u51fa\u76e3\u63a7\u5831\u544a\uff0c\u652f\u63f4\u6642\u9593\u7bc4\u570d\u7b49\u9078', column:'backlog', priority:'medium'},
    {id:'t4', title:'\u6848\u5834\u5716\u5716\u8996\u89ba\u5316', description:'Leaflet/Mapbox \u6574\u5408\uff0c\u5728\u5716\u4e0a\u986f\u793a\u6848\u5834\u72c0\u614b', column:'backlog', priority:'low'},
    {id:'t5', title:'\u6b0a\u9650\u7ba1\u7406', description:'\u4e0d\u540c\u89d2\u8272\u67e5\u770b\u4e0d\u540c\u6848\u5834\u8cc7\u6599\u7684 RBAC \u7cfb\u7d71', column:'backlog', priority:'medium'},
    {id:'t6', title:'Forecast UI \u512a\u5316', description:'monitoring-forecast-ui.js \u8996\u89ba\u5316\u6539\u9032\uff0c\u52a0\u5165\u8da8\u52e2\u5716\u8868', column:'todo', priority:'high'},
    {id:'t7', title:'\u6df1\u8272/\u4eae\u8272\u4e3b\u984c\u5207\u63db', description:'\u4f7f\u7528 teaasia-css-white-theme skill \u5be6\u4f5c\u4e3b\u984c\u5207\u63db', column:'todo', priority:'medium'},
    {id:'t8', title:'Kanban \u770b\u677f\u7cfb\u7d71', description:'\u5efa\u7acb\u5c08\u6848\u7ba1\u7406\u770b\u677f\uff0c\u6574\u5408\u5230 TeaAsia \u7cfb\u7d71', column:'in_progress', priority:'high'},
    {id:'t9', title:'MQTT \u65b7\u7dda\u91cd\u9023\u6a5f\u5236', description:'monitoring-mqtt.js \u81ea\u52d5\u91cd\u9023 + \u6307\u6578\u9000\u907f\u7b56\u7565', column:'review', priority:'high'},
    {id:'t10', title:'MQTT WebSocket \u9023\u7dda', description:'monitoring-mqtt.js mqtt.js CDN ws broker', column:'done', priority:'high'},
    {id:'t11', title:'Topic \u8cfc\u606f\u89e3\u6790', description:'monitoring-parser.js data \u6307 deviceUuid \u5206\u7d44', column:'done', priority:'high'},
    {id:'t12', title:'\u6848\u5834\u5361\u7247\u52d5\u614b\u5efa\u7acb', description:'monitoring-card.js SITES array to card HTML', column:'done', priority:'high'},
    {id:'t13', title:'\u591a\u6848\u5834\u76e3\u63a7\u9801\u9762', description:'multi_site_monitoring plus monitoring_system \u8def\u7531', column:'done', priority:'high'},
    {id:'t14', title:'Forecast \u9810\u6e2c\u5f15\u64ce', description:'monitoring-forecast.js localStorage 30\u5929\u7559\u5b58 15min interval', column:'done', priority:'medium'}
];

/* ── Utility functions ── */

function getTodayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}

function getTodayStrFromObj(dt) {
    var y = dt.getFullYear();
    var m = String(dt.getMonth()+1).padStart(2,'0');
    var d = String(dt.getDate()).padStart(2,'0');
    return y + '-' + m + '-' + d;
}

function parseTimestamp(ts) {
    if (!ts) return null;
    if (typeof ts === 'string' && !ts.includes('T')) return new Date(ts + 'T00:00');
    if (typeof ts === 'string' && (ts.indexOf('+') > 0 || ts.slice(-1) === 'Z')) return new Date(ts);
    return new Date(ts + 'Z');
}

function escHtml(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

/* ── Task loading & syncing ── */

function saveTasks() {
    localStorage.setItem('kanban_tasks', JSON.stringify(tasks));
}

function loadTasks() {
    tasks = [];
    var s = localStorage.getItem('kanban_tasks');
    if (s) tasks = JSON.parse(s);
    
    fetch('/api/tasks', { cache: 'no-store', credentials: 'same-origin' })
        .then(function(r) { 
            console.log('[kanban] /api/tasks response status:', r.status);
            return r.json().then(function(data) {
                if (!r.ok || !Array.isArray(data)) {
                    console.warn('[kanban] API returned non-OK status', r.status, data);
                    var isPublic = false;
                    try { isPublic = !window.currentUserEmail; } catch(e) {}
                    if (!isPublic && tasks.length > 0) {
                        console.log('[kanban] Keeping cached tasks for logged-in user:', tasks.length);
                    } else {
                        tasks = DEFAULT_TASKS.slice();
                        saveTasks();
                    }
                    renderBoard();
                    loadDashboardStats();
                    return;
                }
                console.log('[kanban] Loaded', data.length, 'tasks from DB');
                tasks = data;
                saveTasks();  
                renderBoard();
                loadDashboardStats();
            });
        })
        .catch(function(err) {
            console.warn('API unavailable, using localStorage only', err);
            if (tasks.length === 0) {
                tasks = DEFAULT_TASKS.slice();
                saveTasks();
            }
            renderBoard();
            loadDashboardStats();
        });
}

function syncFromServer() {
    fetch('/api/tasks', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(data) { tasks = data; saveTasks(); renderBoard(); loadDashboardStats(); })
        .catch(function() {});
}

function clearSelection() {
    selectedTaskIds = {};
    renderBoard();
    loadDashboardStats();
}

/* ── Dashboard stats ── */

function loadDashboardStats() {
    var total = tasks.length || 0;
    document.getElementById('totalTasks').textContent = total;
    
    var overdueCount = 0;
    for (var i = 0; i < tasks.length; i++) {
        if (isOverdue(tasks[i])) overdueCount++;
    }
    document.getElementById('overdueCount').textContent = overdueCount;
    
    var columns = allColumns.length > 0 ? allColumns : DEFAULT_COLUMNS;
    var colCounts = {};
    for (var i = 0; i < tasks.length; i++) {
        var c = tasks[i].column || '';
        colCounts[c] = (colCounts[c] || 0) + 1;
    }
    
    // Map internal column names to the fixed HTML IDs in index.html dashboard-stats section
    var ID_MAP = { 'backlog': 'colBacklog', 'todo': 'colTodo', 'in_progress': 'colProgress', 'review': 'colReview', 'done': 'colDone' };
    
    columns.forEach(function(col) {
        var colName = typeof col === 'string' ? col : (col.name || '');
        for (var srcCol in ID_MAP) {
            if (srcCol === colName && document.getElementById(ID_MAP[srcCol])) {
                document.getElementById(ID_MAP[srcCol]).textContent = colCounts[colName] || 0;
                break;
            }
        }
    });
}

function isOverdue(task) {
    if (!task.end_time || task.column === 'done') return false;
    try {
        var endTime = parseTimestamp(task.end_time);
        return endTime < new Date();
    } catch(e) {
        return false;
    }
}

/* ── Dark mode ── */

function toggleDarkMode() {
    var dark = !document.body.classList.contains('dark-mode');
    document.body.classList.toggle('dark-mode', dark);
    localStorage.setItem('kanban-dark-mode', dark ? '1' : '0');
    
    var btn = document.getElementById('darkModeToggle');
    if (btn) {
        btn.textContent = dark ? '\u2600\ufffc' : '\u{1f335}\ufe0f'; // ☀️ / 🌓
    }
}

function checkDarkModePreference() {
    var saved = localStorage.getItem('kanban-dark-mode') || '0';
    if (saved === '1') {
        document.body.classList.add('dark-mode');
        var btn = document.getElementById('darkModeToggle');
        if (btn) btn.textContent = '\u2600\ufffc';
    }
}

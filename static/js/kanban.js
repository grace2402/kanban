// ==========================================
// Kanban Board - DB-Backed JavaScript (Phase 3 Updated)
// Syncs with PostgreSQL via Flask API
// ==========================================

let tasks = [];
let editingId = null;
let currentColumn = 'backlog';
const PRIORITY_LABELS = { high: '高', medium: '中', low: '低' };

const DEFAULT_TASKS = [
    {id:'t1', title:'歷史數據資料庫化', description:'將 MQTT 歷史資料存入 SQLite/PostgreSQL，取代 localStorage', column:'backlog', priority:'high'},
    {id:'t2', title:'告警通知系統', description:'SOC/SOH/溫度異常時觸發 Telegram Line 通知', column:'backlog', priority:'high'},
    {id:'t3', title:'數據匯出功能', description:'CSV/PDF 匯出監控報告，支援時間範圍篩選', column:'backlog', priority:'medium'},
    {id:'t4', title:'案場地圖視覺化', description:'Leaflet/Mapbox 整合，在地圖上顯示案場狀態', column:'backlog', priority:'low'},
    {id:'t5', title:'權限管理', description:'不同角色查看不同案場資料的 RBAC 系統', column:'backlog', priority:'medium'},
    {id:'t6', title:'Forecast UI 優化', description:'monitoring-forecast-ui.js 視覺化改進，加入趨勢圖表', column:'todo', priority:'high'},
    {id:'t7', title:'深色/亮色主題切換', description:'使用 teaasia-css-white-theme skill 實作主題切換', column:'todo', priority:'medium'},
    {id:'t8', title:'Kanban 看板系統', description:'建立專案管理看板，整合到 TeaAsia 系統', column:'in_progress', priority:'high'},
    {id:'t9', title:'MQTT 斷線重連機制', description:'monitoring-mqtt.js 自動重連 + 指數退避策略', column:'review', priority:'high'},
    {id:'t10', title:'MQTT WebSocket 連線', description:'monitoring-mqtt.js mqtt.js CDN ws broker', column:'done', priority:'high'},
    {id:'t11', title:'Topic 訊息解析', description:'monitoring-parser.js data 按 deviceUuid 分組', column:'done', priority:'high'},
    {id:'t12', title:'案場卡片動態建立', description:'monitoring-card.js SITES array to card HTML', column:'done', priority:'high'},
    {id:'t13', title:'多案場監控頁面', description:'multi_site_monitoring plus monitoring_system 路由', column:'done', priority:'high'},
    {id:'t14', title:'Forecast 預測引擎', description:'monitoring-forecast.js localStorage 30天留存 15min interval', column:'done', priority:'medium'}
];

function loadTasks() {
    // Clear stale cached data — always re-fetch from server after login
    tasks = [];
    var s = localStorage.getItem('kanban_tasks');
    if (s) tasks = JSON.parse(s);
    
    fetch('/api/tasks', { cache: 'no-store', credentials: 'same-origin' })
        .then(function(r) { 
            console.log('[kanban] /api/tasks response status:', r.status);
            return r.json().then(function(data) {
                if (!r.ok || !Array.isArray(data)) {
                    // 401 or other error — fall back to defaults
                    console.warn('[kanban] API returned non-OK status', r.status, data);
                    tasks = DEFAULT_TASKS.slice();
                    saveTasks();
                    renderBoard();
                    return;
                }
                console.log('[kanban] Loaded', data.length, 'tasks from DB');
                tasks = data;
                saveTasks();  
                renderBoard();
            });
        })
        .catch(function(err) {
            console.warn('API unavailable, using localStorage only', err);
            if (tasks.length === 0) {
                tasks = DEFAULT_TASKS.slice();
                saveTasks();
            }
            renderBoard();
        });
}

function saveTasks() {
    localStorage.setItem('kanban_tasks', JSON.stringify(tasks));
}

function escHtml(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function renderCard(t) {
    var footerParts = '<span class="priority-badge ' + t.priority + '">' + PRIORITY_LABELS[t.priority] + '</span>';
    if (t.assignee_email && t.assignee_email !== '') {
        // Show all assignees as mini badges (comma-separated emails)
        var assignees = t.assignee_email.split(',').map(function(e){return e.trim();}).filter(Boolean);
        footerParts += assignees.map(function(a) {
            return '<span class="assignee-mini">👤 ' + escHtml(a.split('@')[0]) + '</span>';
        }).join('');
    }
    if (t.start_time) {
        footerParts += '<span class="time-mini">📅 ' + escHtml(new Date(t.start_time).toLocaleDateString('zh-TW')) + '</span>';
    }
    
    return '<div class="kanban-card priority-' + t.priority + '" draggable="true" ondragstart="dragStart(event)" data-id="' + t.id + '">' +
        '<div class="card-title">' + escHtml(t.title) + '</div>' +
        (t.description ? '<div class="card-desc">' + escHtml(t.description) + '</div>' : '') +
        '<div class="card-footer">' + footerParts +
            '<div class="card-actions">' +
                '<button onclick="editTask(\'' + t.id.replace(/'/g, "\\'") + '\')" title="編輯">✏️</button>' +
                '<button onclick="deleteTask(\'' + t.id.replace(/'/g, "\\'") + '\')" title="刪除">🗑️</button>' +
            '</div></div></div>';
}

function renderBoard() {
    ['backlog', 'todo', 'in_progress', 'review', 'done'].forEach(function(col) {
        var c = document.getElementById('col-' + col);
        if (!c) return;
        var filtered = tasks.filter(function(t) { return t.column === col; });
        c.innerHTML = filtered.map(renderCard).join('');
        var countEl = document.getElementById('count-' + col);
        if (countEl) countEl.textContent = filtered.length;
    });
}

function dragStart(e) {
    e.dataTransfer.setData('text/plain', e.target.dataset.id);
    e.target.classList.add('dragging');
}

function allowDrop(e) {
    e.preventDefault();
    e.currentTarget.classList.add('drop-zone-active');
}

function dropCard(e, col) {
    e.preventDefault();
    document.querySelectorAll('.column-body').forEach(function(c) { c.classList.remove('drop-zone-active'); });
    
    var id = e.dataTransfer.getData('text/plain');
    var task = tasks.find(function(t) { return t.id === id; });
    if (!task) return;
    
    task.column = col;
    saveTasks();
    renderBoard();
    
    fetch('/api/task/' + id, {
        credentials: 'same-origin',
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({title: task.title, description: task.description, column: task.column, priority: task.priority})
    }).catch(function(err) { console.error('Sync failed:', err); });
}

document.addEventListener('dragend', function() {
    document.querySelectorAll('.column-body').forEach(function(c) { c.classList.remove('drop-zone-active'); });
    document.querySelectorAll('.kanban-card.dragging').forEach(function(c) { c.classList.remove('dragging'); });
});

// Modal functions
function openAddModal(col) {
    editingId = null;
    currentColumn = col;
    document.getElementById('modalTitle').textContent = '新增任務';
    document.getElementById('taskTitle').value = '';
    document.getElementById('taskDesc').value = '';
    document.getElementById('taskPriority').value = 'medium';
    
    // Reset new fields (Phase 3)
    document.getElementById('taskAssigneeInput').value = '';
    renderAssigneeBadges([]);
    document.getElementById('assignedUserEmails').value = '';
    // Keep HTML default datetime values (00:00 / 23:59) for new tasks
    
    document.getElementById('cardModal').classList.add('active');
}

function editTask(id) {
    var t = tasks.find(function(x) { return x.id === id; });
    if (!t) return;
    editingId = id;
    currentColumn = t.column;
    document.getElementById('modalTitle').textContent = '編輯任務';
    document.getElementById('taskTitle').value = t.title;
    document.getElementById('taskDesc').value = t.description || '';
    document.getElementById('taskPriority').value = t.priority;
    
    // Load assignee & time fields (Phase 3: multi)
    if (t.assignee_email && t.assignee_email !== '') {
        var emails = t.assignee_email.split(',').map(function(e){return e.trim();}).filter(Boolean);
        renderAssigneeBadges(emails);
        document.getElementById('assignedUserEmails').value = emails.join(',');
    } else {
        renderAssigneeBadges([]);
        document.getElementById('assignedUserEmails').value = '';
    }
    
    if (t.start_time) document.getElementById('taskStartTime').value = t.start_time.slice(0, 16);
    if (t.end_time) document.getElementById('taskEndTime').value = t.end_time.slice(0, 16);
    
    document.getElementById('cardModal').classList.add('active');
}

function closeModal() {
    document.getElementById('cardModal').classList.remove('active');
    editingId = null;
}

function saveTask() {
    var title = document.getElementById('taskTitle').value.trim();
    var desc = document.getElementById('taskDesc').value.trim();
    var priority = document.getElementById('taskPriority').value;
    if (!title) { alert('請輸入標題'); return; }

    // Phase 3: Read new fields (multi)
    var assigneeEmailsStr = document.getElementById('assignedUserEmails').value || null;
    var startTime = document.getElementById('taskStartTime').value || null;
    var endTime = document.getElementById('taskEndTime').value || null;

    if (editingId) {
        fetch('/api/task/' + editingId, {
            credentials: 'same-origin',
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({title: title, description: desc, column: currentColumn, priority: priority, assignee_email: assigneeEmailsStr, start_time: startTime, end_time: endTime})
        }).then(function() { syncFromServer(); });
    } else {
        var newId = 't' + Date.now();
        fetch('/api/task', { 
            credentials: 'same-origin',
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: newId, title: title, description: desc, column: currentColumn, priority: priority, assignee_email: assigneeEmailsStr, start_time: startTime, end_time: endTime})
        }).then(function() { syncFromServer(); });
    }

    // Show calendar confirmation if time is set and assignees selected
    if (startTime && endTime && assigneeEmailsStr) {
        console.log('📅 Task synced to Google Calendar for ' + assigneeEmailsStr);
    }
    
    saveTasks();
    closeModal();
}

function deleteTask(id) {
    if (!confirm('確定刪除此任務？')) return;
    
    tasks = tasks.filter(function(t) { return t.id !== id; });
    saveTasks();
    renderBoard();
    
    fetch('/api/task/' + id, { credentials: 'same-origin', method: 'DELETE' })
        .catch(function(err) { console.error('Delete failed:', err); });
}

function syncFromServer() {
    fetch('/api/tasks', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(data) { tasks = data; saveTasks(); renderBoard(); })
        .catch(function() {});
}

if (document.getElementById('cardModal')) {
document.getElementById('cardModal').addEventListener('click', function(e) {
    if (e.target === this) closeModal();
});
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeModal();
});

// Initialize on load
document.addEventListener('DOMContentLoaded', function() {
    // Check if we're actually logged in before trying to load tasks
    var userEmail = window.currentUserEmail;
    console.log('[kanban] page loaded, currentUserEmail:', JSON.stringify(userEmail));

    loadTasks();

    // Assignee Search Logic (Phase 3)
    var ddEl = document.getElementById('assigneeDropdown');
    var input = document.getElementById('taskAssigneeInput');
    
    if (ddEl && input) {
        // Pre-fetch all users on load so focus shows them instantly.
        fetch('/api/users?q=&page=1&limit=50', { credentials: 'same-origin' })
            .then(function(r) { return r.json(); })
            .then(function(users) { window._allUsers = Array.isArray(users) ? users : []; })
            .catch(function() { window._allUsers = []; });

        input.addEventListener('focus', function() {
            var q = input.value.trim();
            // Always show self first for instant feedback, then update from cache/API.
            renderSelfOnly();
            if (window._allUsers && window._allUsers.length > 0) {
                showDropdownFromCache(q);  // overlay with full cached list.
            } else {
                // Cache empty: fetch from API with current query text.
                (q ? 
                    fetch('/api/users?q=' + encodeURIComponent(q) + '&page=1&limit=50', { credentials: 'same-origin' }) :
                    fetch('/api/users?page=1&limit=50', { credentials: 'same-origin' })
                )
                .then(function(r) { return r.json(); })
                .then(function(users) {
                    window._allUsers = Array.isArray(users) ? users : [];
                    showDropdownFromCache(q);
                })
                .catch(function() {});
            }
        });

        input.addEventListener('input', function() {
            clearTimeout(window.assigneeSearchTimeout);
            var q = input.value.trim();
            if (q.length >= 1) {
                renderSelfOnly();  // instant self-show while fetching.
                // Lazy fetch: only query API when user actually types.
                window.assigneeSearchTimeout = setTimeout(function() {
                    doSearch(q);   // fetch filtered results from server.
                }, 300);
            } else {
                renderSelfOnly();  // instant self-show for empty backspace.
                if (window._allUsers && window._allUsers.length > 0) {
                    showDropdownFromCache('');
                } else {
                    // Also try fetching from API if cache is empty.
                    fetch('/api/users?page=1&limit=50', { credentials: 'same-origin' })
                        .then(function(r) { return r.json(); })
                        .then(function(users) {
                            window._allUsers = Array.isArray(users) ? users : [];
                            showDropdownFromCache('');
                        })
                        .catch(function() {});
                }
            }
        });

        document.addEventListener('click', function(e) {
            if (!e.target.closest('.form-assignee')) {
                ddEl.classList.add('hidden');
            }
        });
    }
});

/** Build and show dropdown from LOCAL cache (instant, no network delay). */
function showDropdownFromCache(q) {
    var ddEl = document.getElementById('assigneeDropdown');
    if (!ddEl) return;
    
    if (!window._allUsers || window._allUsers.length === 0) return;

    var currentUserEmail = window.currentUserEmail || '';
    var userList;

    // If typed text and we have a cached list → filter locally first (instant).
    if (q && q.length >= 1) {
        var lowerQ = q.toLowerCase();
        userList = window._allUsers.filter(function(u) {
            return u.email.toLowerCase().indexOf(lowerQ) !== -1 ||
                   (u.name || '').toLowerCase().indexOf(lowerQ) !== -1;
        });
    } else {
        // No query → show all cached users.
        userList = window._allUsers.slice();
    }

    // Ensure current user is always first, without duplicates.
    if (currentUserEmail) {
        var idx = userList.findIndex(function(u) { return u.email === currentUserEmail; });
        if (idx !== -1) {
            // Self already in results but not first → move to front.
            var selfUser = userList[idx];
            userList.splice(idx, 1);
            userList.unshift(selfUser);
        } else {
            // Self NOT in results → prepend self as fallback option.
            userList.unshift({ id: 'self', email: currentUserEmail, name: currentUserEmail.split('@')[0] });
        }
    }

    showDropdownHTML(userList.slice(0, 10));
}

/** Render the current user as a self-assign option (no API call). */
function renderSelfOnly() {
    var currentUserEmail = window.currentUserEmail || '';
    if (!currentUserEmail) return;  // not logged in → stay hidden.
    var name = currentUserEmail.split('@')[0];
    showDropdownHTML([{ id: 'self', email: currentUserEmail, name: name }]);
}

/** Debounced search: fetch from /api/users?q=QUERY&limit=10 */
function doSearch(q) {
    clearTimeout(window.assigneeSearchTimeout);
    window.assigneeSearchTimeout = setTimeout(function() {
        fetch('/api/users?q=' + encodeURIComponent(q) + '&page=1&limit=10', { credentials: 'same-origin' })
            .then(function(r) { return r.json(); })
            .then(function(users) {
                if (!Array.isArray(users)) return;
                // Ensure current user is always first.
                var currentUserEmail = window.currentUserEmail || '';
                var idx = users.findIndex(function(u) { return u.email === currentUserEmail; });
                if (idx !== -1 && idx > 0) {
                    var self = users.splice(idx, 1)[0];
                    users.unshift(self);
                } else if (idx === -1 && currentUserEmail) {
                    users.unshift({ id: 'self', email: currentUserEmail, name: currentUserEmail.split('@')[0] });
                }
                // Cap at exactly 10.
                showDropdownHTML(users.slice(0, 10));
            })
            .catch(function() {
                showDropdownHTML([]);
            });
    }, 300); // 300 ms debounce — snappy but not spammy.
}

/** Render user list into the dropdown; max 10 items. */
function showDropdownHTML(userList) {
    var ddEl = document.getElementById('assigneeDropdown');
    if (!ddEl) return;
    
    var currentUserEmail = window.currentUserEmail || '';
    if (!userList.length) {
        ddEl.innerHTML = '<div class="assignee-option" style="color:#888;">找不到帳號</div>';
        ddEl.classList.remove('hidden');
        return;
    }

    // If more than 10 total, append "…更多" hint (the scrollbar handles the rest).
    var html = userList.map(function(u) {
        var name = u.name || u.email.split('@')[0];
        var isSelf = (u.email === currentUserEmail);
        // Use single quotes for inline onclick; escape single quotes in values
        var safeEmail = u.email.replace(/'/g, "\\'");
        var safeName = name.replace(/'/g, "\\'");
        return '<div class="assignee-option" onclick="selectAssignee(\'' + safeEmail + '\', \'' + safeName + '\')">' +
               (isSelf ? '<span style="color:#e6a817;">⭐</span>' : '') +
               '<strong>' + escHtml(name) + '</strong> <span style="color:#888;">' + escHtml(u.email) + '</span></div>';
    }).join('');

    ddEl.innerHTML = html;
    ddEl.classList.remove('hidden');
}

/** Render selected assignees as badge pills in the modal. */
function renderAssigneeBadges(emails) {
    var container = document.getElementById('assigneeBadgesContainer');
    if (!container) return;
    
    emails = emails.filter(function(e){return e && e.trim();});
    
    if (emails.length === 0) {
        container.style.display = 'none';
        container.innerHTML = '';
        return;
    }
    
    container.style.display = 'flex';
    var html = emails.map(function(email, idx) {
        var name = email.split('@')[0];
        // Use single quotes for inline onclick; escape single quotes in values
        var safeEmail = email.replace(/'/g, "\\'");
        return '<span class="assignee-pill">' + escHtml(name) + ' <button type="button" onclick="removeAssignee(\'' + safeEmail + '\')" style="background:none;border:none;color:#e74c5c;cursor:pointer;font-size:12px;padding:0 0 0 2px;">&#x2715;</button></span>';
    }).join('');
    
    container.innerHTML = html;
}

function selectAssignee(email, name) {
    // Add to existing list (don't replace)
    var currentStr = document.getElementById('assignedUserEmails').value || '';
    var currentEmails = currentStr.split(',').map(function(e){return e.trim();}).filter(Boolean);
    
    // Don't add if already selected
    if (currentEmails.indexOf(email) !== -1) return;
    
    currentEmails.push(email);
    document.getElementById('assignedUserEmails').value = currentEmails.join(',');
    renderAssigneeBadges(currentEmails);
    
    // Clear input and close dropdown
    document.getElementById('taskAssigneeInput').value = '';
    document.getElementById('assigneeDropdown').innerHTML = '';
    document.getElementById('assigneeDropdown').classList.add('hidden');
}

function removeAssignee(email) {
    var currentStr = document.getElementById('assignedUserEmails').value || '';
    var emails = currentStr.split(',').map(function(e){return e.trim();}).filter(Boolean);
    emails = emails.filter(function(e){return e !== email;});
    document.getElementById('assignedUserEmails').value = emails.join(',');
    renderAssigneeBadges(emails);
}

function clearAllAssignees() {
    document.getElementById('taskAssigneeInput').value = '';
    document.getElementById('assignedUserEmails').value = '';
    renderAssigneeBadges([]);
}

// bfcache defense - re-fetch after login redirect via OAuth
window.addEventListener('pageshow', function(e) {
    if (e.persisted) {
        // Page restored from bfcache — refresh to pick up new session cookies set during OAuth redirect chain
        console.log('[kanban] bfcache restore detected, refreshing...');
        fetch('/api/tasks', { credentials: 'same-origin' })
            .then(function(r) { 
                return r.json().then(function(data) {
                    if (r.ok && Array.isArray(data)) {
                        tasks = data;
                        saveTasks();  
                        renderBoard();
                    } else {
                        // Not logged in anymore — redirect to login
                        window.location.href = '/login';
                    }
                });
            })
            .catch(function(err) { 
                console.warn('bfcache refresh failed:', err);
                tasks = DEFAULT_TASKS.slice();
                saveTasks();
                renderBoard();
            });
    }
});

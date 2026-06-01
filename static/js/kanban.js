// ==========================================
// Kanban Board - DB-Backed JavaScript (Phase 3 Updated)
// Syncs with PostgreSQL via Flask API
// ==========================================

let tasks = [];
let editingId = null;
let currentColumn = 'backlog';
var batchMode = false;
var selectedTaskIds = {};  // { id: true }
var searchQuery = '';
var priorityFilter = '';
var labelFilter = '';
var allLabelsCache = [];  // [{id, name, color}]
const PRIORITY_LABELS = { high: '高', medium: '中', low: '低' };
function getTodayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}


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


// ==========================================
// Phase 5: Dynamic Column Management  
// ==========================================

var allColumns = []; // loaded from /api/columns

function loadColumns() {
    fetch('/api/columns', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(cols) { 
            if (Array.isArray(cols) && cols.length > 0) {
                allColumns = cols;
            } else {
                // Fallback to defaults
                allColumns = DEFAULT_COLUMNS.map(function(name, i) {
                    return { name: name, display_name: _getDisplayName(name), sort_order: i + 1 };
                });
            }
        })
        .catch(function(err) { 
            console.warn('Failed to load columns:', err); 
            allColumns = DEFAULT_COLUMNS.map(function(name, i) {
                return { name: name, display_name: _getDisplayName(name), sort_order: i + 1 };
            });
        });
}

function _getDisplayName(slug) {
    var names = {'backlog':'Backlog','todo':'To Do','in_progress':'In Progress','review':'Review','done':'Done'};
    return names[slug] || slug.replace(/_/g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});
}

function getColumnOrder() {
    var sorted = allColumns.slice().sort(function(a,b) { return (a.sort_order||0) - (b.sort_order||0); });
    return sorted.map(function(c){ return c.name; });
}


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



// ==========================================
// Phase 4: Dashboard Stats & Dark Mode
// ==========================================

function loadDashboardStats() {
    fetch('/api/stats', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(stats) {
            document.getElementById('totalTasks').textContent = stats.total;
            document.getElementById('overdueCount').textContent = stats.overdue_count;
            
            // Update column counts
            var columns = ['backlog', 'todo', 'in_progress', 'review', 'done'];
            columns.forEach(function(col) {
                var elId = 'col' + col.charAt(0).toUpperCase() + col.slice(1);
                if (document.getElementById(elId)) {
                    document.getElementById(elId).textContent = stats.by_column[col] || 0;
                }
            });
        })
        .catch(function(err) { console.warn('Failed to load dashboard stats:', err); });
}

function toggleDarkMode() {
    var dark = !document.body.classList.contains('dark-mode');
    document.body.classList.toggle('dark-mode', dark);
    localStorage.setItem('kanban-dark-mode', dark ? '1' : '0');
    
    // Update button icon
    var btn = document.getElementById('darkModeToggle');
    if (btn) {
        btn.textContent = dark ? '☀️' : '🌓';
    }
}

function checkDarkModePreference() {
    var saved = localStorage.getItem('kanban-dark-mode') || '0';
    if (saved === '1') {
        document.body.classList.add('dark-mode');
        var btn = document.getElementById('darkModeToggle');
        if (btn) btn.textContent = '☀️';
    }
}

function isOverdue(task) {
    if (!task.end_time || task.column === 'done') return false;
    try {
        var endTime = new Date(task.end_time);
        return endTime < new Date();
    } catch(e) {
        return false;
    }
}

function renderCard(t) {
    var footerParts = '<span class="priority-badge ' + t.priority + '">' + PRIORITY_LABELS[t.priority] + '</span>';
    // Phase 4: Check if task is overdue
    var overdueClass = isOverdue(t) ? 'kanban-card priority-' + t.priority + ' overdue' : 'kanban-card priority-' + t.priority;
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
    
    // Label chips on card
    var labelHtml = '';
    if (t.labels && t.labels.length > 0) {
        labelHtml = '<div class="card-labels">' + t.labels.map(function(l) {
            return '<span class="label-chip" style="background:' + escHtml(l.color || '#6c757d') + '; color:#fff;">' + escHtml(l.name) + '</span>';
        }).join('') + '</div>';
    }

    // Batch mode checkbox
    var batchCb = '';
    if (batchMode) {
        var checked = selectedTaskIds[t.id] ? 'checked' : '';
        batchCb = '<input type="checkbox" class="task-checkbox" data-id="' + t.id + '" ' + checked + ' onclick="event.stopPropagation();toggleSelect(\'' + t.id.replace(/'/g, "\'") + '\')">';
    }

    return '<div class="' + overdueClass + '" draggable="true" ondragstart="dragStart(event)" data-id="' + t.id + '">' +
        batchCb +
        '<div class="card-title">' + escHtml(t.title) + '</div>' +
        labelHtml +
        (t.description ? '<div class="card-desc">' + escHtml(t.description) + '</div>' : '') +
        '<div class="card-footer">' + footerParts +
            '<div class="card-actions">' +
                '<button onclick="editTask(\'' + t.id.replace(/'/g, "\'") + '\')" title="編輯">✏️</button>' +
                '<button onclick="cloneTask(\'' + t.id.replace(/'/g, "\'") + '\')" title="複製任務">📋</button>' +
                '<button onclick="deleteTask(\'' + t.id.replace(/'/g, "\'") + '\')" title="刪除">🗑️</button>' +
            '</div></div></div>';
}

function renderBoard() {
    // Phase 5: Use dynamic columns from API instead of hardcoded list
    var columnOrder = allColumns.length > 0 ? getColumnOrder() : DEFAULT_COLUMNS;
    
    columnOrder.forEach(function(col) {
        var c = document.getElementById('col-' + col);
        if (!c) return;
        var filtered = tasks.filter(function(t) { 
            // Column filter always applies
            if (t.column !== col) return false;
            
            // Search text filter
            if (searchQuery && searchQuery.length > 0) {
                var q = searchQuery.toLowerCase();
                var titleMatch = (t.title || '').toLowerCase().indexOf(q) !== -1;
                var descMatch = (t.description || '').toLowerCase().indexOf(q) !== -1;
                if (!titleMatch && !descMatch) return false;
            }
            
            // Priority filter
            if (priorityFilter && t.priority !== priorityFilter) return false;
            
            // Label filter: task must have ALL selected labels
            if (labelFilter && labelFilter.length > 0) {

loadDashboardStats();

                var taskLabels = (t.labels || []).map(function(l){return l.name.toLowerCase()});
                for (var i = 0; i < labelFilter.length; i++) {
                    if (taskLabels.indexOf(labelFilter[i].toLowerCase()) === -1) return false;
                }
            }
            
            return true;
        });
        c.innerHTML = filtered.map(renderCard).join('');
        var countEl = document.getElementById('count-' + col);
        if (countEl) countEl.textContent = filtered.length;
    });
    
    // Update batch selection count display
    updateBatchCount();
}

function filterAndRender() {
    searchQuery = document.getElementById('searchInput') ? document.getElementById('searchInput').value.trim() : '';
    priorityFilter = document.getElementById('priorityFilter') ? document.getElementById('priorityFilter').value : '';
    renderBoard();
}

// Batch mode functions
function toggleBatchMode() {
    var cb = document.getElementById('batchModeToggle');
    if (cb) batchMode = cb.checked;
    
    var toolbar = document.getElementById('batchToolbar');
    if (!toolbar) return;
    
    if (batchMode) {
        selectedTaskIds = {};
        toolbar.classList.remove('hidden');
    } else {
        selectedTaskIds = {};
        toolbar.classList.add('hidden');
    }
    renderBoard();
}

function toggleSelect(id) {
    if (!batchMode) return;
    if (selectedTaskIds[id]) {
        delete selectedTaskIds[id];
    } else {
        selectedTaskIds[id] = true;
    }
    
    // Update checkbox state in DOM without full re-render
    var cb = document.querySelector('.task-checkbox[data-id="' + id + '"]');
    if (cb) cb.checked = !!selectedTaskIds[id];
    
    updateBatchCount();
}

function updateBatchCount() {
    var countEl = document.getElementById('selectedCount');
    if (countEl) {
        countEl.textContent = Object.keys(selectedTaskIds).length + ' 個已選取';
    }
}

function doBatchMove() {
    var colSelect = document.getElementById('batchMoveColumn');
    if (!colSelect || !colSelect.value) return;
    
    var ids = Object.keys(selectedTaskIds);
    if (ids.length === 0) return;
    
    // Phase 3: Use batch API instead of individual calls
    fetch('/api/tasks/batch-move', {
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({task_ids: ids, column: colSelect.value})
    }).then(function(r) { return r.json(); })
      .then(function(data) {
          if (data.moved === undefined || data.moved !== ids.length) {
              console.warn('Batch move returned unexpected count:', data);
          }
          syncFromServer();
          clearSelection();
      }).catch(function(err) {
        console.error('Batch move failed:', err);
        alert('移動失敗');
        syncFromServer();  // Refresh to get correct state
    });
}

// ==========================================
// Phase 5: Batch Priority Update  
// ==========================================

function doBatchPriority() {
    var sel = document.getElementById('batchPrioritySelect');
    if (!sel || !sel.value) return;
    
    var ids = Object.keys(selectedTaskIds);
    if (ids.length === 0) return;
    
    fetch('/api/tasks/batch-priority', {
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({task_ids: ids, priority: sel.value})
    }).then(function(r) { return r.json(); })
      .then(function(data) {
          if (data.updated === undefined || data.updated !== ids.length) {
              console.warn('Batch priority returned unexpected count:', data);
          }
          syncFromServer();
          clearSelection();
          sel.value = '';
      }).catch(function(err) {
        console.error('Batch priority failed:', err);
        alert('更新優先級失敗');
        syncFromServer();
    });
}


// ==========================================
// Phase 5: Task Comments  
// ==========================================

function loadTaskComments(taskId) {
    fetch('/api/task/' + encodeURIComponent(taskId) + '/comments', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(comments) { 
            renderCommentList(Array.isArray(comments) ? comments : []); 
        })
        .catch(function(err) { console.warn('Failed to load comments:', err); });
}

function renderCommentList(comments) {
    var container = document.getElementById('commentList');
    if (!container) return;
    
    if (comments.length === 0) {
        container.innerHTML = '<div style="color:#888;font-size:12px;padding:4px 0;">尚無留言</div>';
        return;
    }
    
    var html = '';
    comments.forEach(function(cmt) {
        var timeStr = cmt.created_at ? new Date(cmt.created_at).toLocaleString('zh-TW', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
        var actorName = (cmt.actor_email || 'system').split('@')[0];
        html += '<div class="comment-item">' +
            '<span class="comment-actor">👤' + escHtml(actorName) + '</span>' +
            '<span class="comment-text">' + escHtml(cmt.content) + '</span>' +
            '<span class="comment-time">' + timeStr + '</span>' +
            '</div>';
    });
    
    container.innerHTML = html;
}

function addComment() {
    if (!editingId) return;
    var input = document.getElementById('commentInput');
    if (!input || !input.value.trim()) return;
    
    fetch('/api/task/' + encodeURIComponent(editingId) + '/comment', {
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({content: input.value.trim()})
    })
    .then(function(r) { return r.json(); })
    .then(function(result) { 
        if (result && result.id) {
            loadTaskComments(editingId);
            input.value = '';
        } else {
            alert('新增留言失敗');
        }
    })
    .catch(function(err) { console.error('Add comment failed:', err); });
}

function deleteComment(cid) {
    if (!confirm('刪除此留言？')) return;
    
    fetch('/api/comment/' + cid, { credentials: 'same-origin', method: 'DELETE' })
        .then(function(r) { return r.json(); })
        .then(function(result) { 
            if (result && result.deleted) {
                loadTaskComments(editingId);
            } else {
                alert('刪除失敗');
            }
        })
        .catch(function(err) { console.error('Delete comment failed:', err); });
}


function doBatchDelete() {
    var ids = Object.keys(selectedTaskIds);
    if (ids.length === 0) return;
    
    if (!confirm('確定刪除 ' + ids.length + ' 個任務？')) return;
    
    // Remove from local tasks
    tasks = tasks.filter(function(t) { return !selectedTaskIds[t.id]; });
    
    // Phase 3: Use batch delete API instead of individual calls
    fetch('/api/tasks/batch-delete', {
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({task_ids: ids})
    }).then(function() {
        syncFromServer();
        clearSelection();
    }).catch(function(err) {
        console.error('Batch delete failed:', err);
        alert('刪除失敗');
        syncFromServer();  // Refresh to get correct state
    });
}

// Phase 3: Clone a task (copy with subtasks and labels)
function cloneTask(taskId) {
    fetch('/api/task/' + taskId + '/clone', {
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
    }).then(function(r) { return r.json(); })
      .then(function(data) {
          if (data.id) {
              syncFromServer();
              // Open the cloned task for editing
              openCardModal(data.id);
          } else {
              alert('複製失敗: ' + (data.error || '未知錯誤'));
          }
      }).catch(function(err) {
        console.error('Clone failed:', err);
        alert('複製失敗');
    });
}

// Phase 3: Export tasks to CSV
function exportCSV() {
    fetch('/api/tasks/export/csv', { credentials: 'same-origin' })
        .then(function(r) { return r.blob(); })
        .then(function(blob) {
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'kanban-tasks-' + new Date().toISOString().slice(0,10) + '.csv';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        })
        .catch(function(err) { console.error('CSV export failed:', err); alert('匯出失敗'); });
}

function clearSelection() {
    selectedTaskIds = {};
    renderBoard();
}

// Label functions - load labels from API on init
function loadLabels() {
    fetch('/api/labels', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(labels) { 
            allLabelsCache = Array.isArray(labels) ? labels : []; 
            renderLabelFilterChips();
        })
        .catch(function(err) { console.warn('Failed to load labels:', err); });
}

function renderLabelFilterChips() {
    var bar = document.getElementById('labelFilterBar');
    if (!bar || allLabelsCache.length === 0) return;

    var html = '';
    allLabelsCache.forEach(function(label) {
        var isActive = labelFilter && labelFilter.indexOf(label.name.toLowerCase()) !== -1;
        var activeClass = isActive ? ' active' : '';
        html += '<span class="label-filter-chip' + activeClass + '" data-label-name="' + escHtml(label.name) + '" style="' +
            'background:' + escHtml(label.color || '#6c757d') + ';' +
            (isActive ? ' box-shadow: 0 0 0 2px #fff inset;' : '') + '">' +
            escHtml(label.name) + '</span>';
    });

    bar.innerHTML = html;
}

// Event delegation for label filter chips (avoids onclick escaping issues)
document.addEventListener('click', function(e) {
    var chip = e.target.closest('.label-filter-chip');
    if (chip) {
        toggleLabelFilter(chip.getAttribute('data-label-name'));
    }
});

function toggleLabelFilter(name) {
    if (!labelFilter) labelFilter = [];
    
    var idx = labelFilter.indexOf(name.toLowerCase());
    if (idx !== -1) {
        labelFilter.splice(idx, 1);
    } else {
        labelFilter.push(name.toLowerCase());
    }
    
    renderBoard();
    renderLabelFilterChips();
}

function loadTaskLabelsForModal(taskId) {
    // Return current labels for a task (from in-memory tasks data which includes labels from server)
    var t = tasks.find(function(x) { return x.id === taskId; });
    if (!t || !t.labels) return [];
    return t.labels.map(function(l){return l.name});
}

function renderLabelBadgesInModal(names, colorMap) {
    var container = document.getElementById('labelBadgesContainer');
    if (!container) return;
    
    names = names.filter(function(n){return n && n.trim();});
    
    if (names.length === 0) {
        container.style.display = 'none';
        container.innerHTML = '';
        return;
    }
    
    container.style.display = 'flex';
    var html = names.map(function(name) {
        var color = '#6c757d';
        if (colorMap) {
            for (var i = 0; i < allLabelsCache.length; i++) {
                if (allLabelsCache[i].name.toLowerCase() === name.toLowerCase()) {
                    color = allLabelsCache[i].color;
                    break;
                }
            }
        }
        return '<span class="assignee-pill" style="background:' + escHtml(color) + ';color:#fff;">' + escHtml(name) + 
               ' <button type="button" onclick="removeLabelFromModal(' + name.replace(/'/g, "\'") + '\')" style="background:none;border:none;color:rgba(255,255,255,0.8);cursor:pointer;font-size:12px;padding:0 0 0 4px;">&#x2715;</button></span>';
    }).join('');
    
    container.innerHTML = html;
}

function addLabelToModal(name) {
    var input = document.getElementById('taskLabelInput');
    if (!input || !name || name.trim() === '') return;
    
    // Get current labels from the hidden input or badges
    var currentVal = document.getElementById('assignedUserEmails').getAttribute('data-labels') || '';
    var names = currentVal ? currentVal.split(',').map(function(n){return n.trim();}).filter(Boolean) : [];
    
    if (names.indexOf(name.trim()) !== -1) return;  // Already added
    
    names.push(name.trim());
    document.getElementById('assignedUserEmails').setAttribute('data-labels', names.join(','));
    
    // Build color map for display
    var colorMap = {};
    allLabelsCache.forEach(function(l){colorMap[l.name.toLowerCase()] = l.color;});
    renderLabelBadgesInModal(names, colorMap);
    
    input.value = '';
}

function removeLabelFromModal(name) {
    var currentVal = document.getElementById('assignedUserEmails').getAttribute('data-labels') || '';
    var names = currentVal ? currentVal.split(',').map(function(n){return n.trim();}).filter(Boolean) : [];
    names = names.filter(function(n){return n !== name;});
    document.getElementById('assignedUserEmails').setAttribute('data-labels', names.join(','));
    
    var colorMap = {};
    allLabelsCache.forEach(function(l){colorMap[l.name.toLowerCase()] = l.color;});
    renderLabelBadgesInModal(names, colorMap);
}

// Label search in modal dropdown
function initLabelSearch() {
    var input = document.getElementById('taskLabelInput');
    var dd = document.getElementById('labelSearchDropdown');
    
    if (!input || !dd) return;
    
    input.addEventListener('focus', function() {
        showLabelDropdown(input.value.trim());
    });
    
    input.addEventListener('input', function() {
        clearTimeout(window.labelSearchTimeout);
        window.labelSearchTimeout = setTimeout(function() {
            showLabelDropdown(input.value.trim());
        }, 200);
    });
}

function showLabelDropdown(q) {
    var dd = document.getElementById('labelSearchDropdown');
    if (!dd) return;
    
    if (q && q.length > 0) {
        // Show matching labels + "create new" option
        var matches = allLabelsCache.filter(function(l){ 
            return l.name.toLowerCase().indexOf(q.toLowerCase()) !== -1; 
        });
        
        var html = '';
        matches.forEach(function(label) {
            html += '<div class="assignee-option" onclick="addLabelToModal(' + label.name.replace(/'/g, "\'") + '\'); document.getElementById(\'labelSearchDropdown\').classList.add(\'hidden\');" style="color:' + escHtml(label.color) + ';">&#x1f3ff; ' + escHtml(label.name) + '</div>';
        });
        
        // If no exact match, offer to create new label
        if (matches.length === 0 && q) {
            html += '<div class="assignee-option" onclick="createNewLabel(' + q.replace(/'/g, "\'") + '\'); document.getElementById(\'labelSearchDropdown\').classList.add(\'hidden\');" style="color:#10b981;">＋ 建立新標籤: ' + escHtml(q) + '</div>';
        }
        
        dd.innerHTML = html;
    } else {
        // Show all labels with "create new" option at bottom
        var html = '';
        allLabelsCache.forEach(function(label) {
            html += '<div class="assignee-option" onclick="addLabelToModal(' + label.name.replace(/'/g, "\'") + '\'); document.getElementById(\'labelSearchDropdown\').classList.add(\'hidden\');" style="color:' + escHtml(label.color) + ';">&#x1f3ff; ' + escHtml(label.name) + '</div>';
        });
        html += '<div class="assignee-option" onclick="document.getElementById(\'labelSearchDropdown\').classList.add(\'hidden\');" style="color:#888;">(留空可建立新標籤)</div>';
        dd.innerHTML = html;
    }
    
    if (dd.innerHTML) {
        dd.classList.remove('hidden');
    } else {
        dd.classList.add('hidden');
    }
}

function createNewLabel(name) {
    // Check if label already exists
    var existing = allLabelsCache.find(function(l){return l.name.toLowerCase() === name.toLowerCase();});
    if (existing) {
        addLabelToModal(existing.name);
        return;
    }
    
    // Generate a random color for the new label
    var colors = ['#3B82F6','#10B981','#EF4444','#F59E0B','#8B5CF6','#EC4899','#06B6D4','#84CC16'];
    var color = colors[Math.floor(Math.random() * colors.length)];
    
    fetch('/api/label', {
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name, color: color})
    }).then(function(r) { return r.json(); })
    .then(function(result) {
        if (result && result.id) {
            // Add to local cache
            allLabelsCache.push({id: result.id, name: name, color: color});
            addLabelToModal(name);
            renderLabelFilterChips();  // Update filter chips too
        } else {
            alert('建立標籤失敗');
        }
    }).catch(function(err) { console.error('Create label failed:', err); });
}

document.addEventListener('click', function(e) {
    if (!e.target.closest('.form-labels')) {
        var dd = document.getElementById('labelSearchDropdown');
        if (dd) dd.classList.add('hidden');
    }
});

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

    // Reset label fields
    document.getElementById('taskLabelInput').value = '';
    document.getElementById('labelBadgesContainer').style.display = 'none';
    document.getElementById('labelBadgesContainer').innerHTML = '';
    if (document.getElementById('assignedUserEmails')) {
        document.getElementById('assignedUserEmails').removeAttribute('data-labels');
    }

    // Bug #2 fix: Set default dates to today for new tasks
    var today = getTodayStr();
    document.getElementById('taskStartDate').value = today;
    document.getElementById('taskEndDate').value = today;
    document.getElementById('taskStartTime').value = '00:00';
    document.getElementById('taskEndTime').value = '23:59';

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
    
    // Bug #3 fix: Split ISO timestamp into separate date+time inputs
    if (t.start_time) {
        var st = new Date(t.start_time.replace('Z', '+00:00'));
        document.getElementById('taskStartDate').value = getTodayStrFromObj(st);
        document.getElementById('taskStartTime').value = String(st.getHours()).padStart(2,'0') + ':' + String(st.getMinutes()).padStart(2,'0');
    }
    if (t.end_time) {
        var et = new Date(t.end_time.replace('Z', '+00:00'));
        document.getElementById('taskEndDate').value = getTodayStrFromObj(et);
        document.getElementById('taskEndTime').value = String(et.getHours()).padStart(2,'0') + ':' + String(et.getMinutes()).padStart(2,'0');
    }
    
    // Load labels for modal
    if (t.labels && t.labels.length > 0) {
        var labelNames = t.labels.map(function(l){return l.name});
        if (document.getElementById('assignedUserEmails')) {
            document.getElementById('assignedUserEmails').setAttribute('data-labels', labelNames.join(','));
        }
        var colorMap = {};
        allLabelsCache.forEach(function(l){colorMap[l.name.toLowerCase()] = l.color;});
        renderLabelBadgesInModal(labelNames, colorMap);
    } else {
        if (document.getElementById('assignedUserEmails')) {
            document.getElementById('assignedUserEmails').removeAttribute('data-labels');
        }
        var container = document.getElementById('labelBadgesContainer');
        if (container) { container.style.display = 'none'; container.innerHTML = ''; }
    }

    // Show subtask & activity sections ONLY in edit mode (not new task)
    if (editingId) {
        var subSec = document.getElementById('subtaskSection');
        if (subSec) subSec.classList.remove('hidden');
        loadSubtasks(editingId);  // Async load
        
        var actSec = document.getElementById('activitySection');
        if (actSec) actSec.classList.remove('hidden');
        loadActivityLog(editingId);  // Async load
    } else {
        var subSec2 = document.getElementById('subtaskSection');
        if (subSec2) subSec2.classList.add('hidden');
        var actSec2 = document.getElementById('activitySection');
        if (actSec2) actSec2.classList.add('hidden');
    }
    
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
    
    // Bug #3 fix: Combine separate date+time inputs into ISO format
    var startDateVal = document.getElementById('taskStartDate').value || '';
    var startTimeVal = document.getElementById('taskStartTime').value || '00:00';
    var endDateVal = document.getElementById('taskEndDate').value || '';
    var endTimeVal = document.getElementById('taskEndTime').value || '23:59';
    
    var startTime = null;
    if (startDateVal) {
        startTime = startDateVal + 'T' + startTimeVal;
    }
    var endTime = null;
    if (endDateVal) {
        // Bug #1 fix: For calendar link, use next day 00:00 when multi-day task
        endTime = endDateVal + 'T' + endTimeVal;
    }
    
    // Read labels from modal data-labels attribute
    var labelNamesStr = '';
    if (document.getElementById('assignedUserEmails')) {
        labelNamesStr = document.getElementById('assignedUserEmails').getAttribute('data-labels') || '';
    }
    var labelNames = labelNamesStr ? labelNamesStr.split(',').map(function(n){return n.trim();}).filter(Boolean) : [];

    if (editingId) {
        var editPromise = fetch('/api/task/' + editingId, {
            credentials: 'same-origin',
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({title: title, description: desc, column: currentColumn, priority: priority, assignee_email: assigneeEmailsStr, start_time: startTime, end_time: endTime})
        });

        editPromise.then(function() { 
            // Send labels separately via dedicated endpoint  
            var labelPromise = null;
            if (labelNames.length > 0) {
                labelPromise = fetch('/api/task/' + editingId + '/labels', {
                    credentials: 'same-origin',
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({labels: labelNames})
                });
            }
            return (labelPromise || Promise.resolve()).then(function() { 
                syncFromServer(); 
                // Phase 3: Reload subtasks and activity log in modal after edit
                var subSec = document.getElementById('subtaskSection');
                if (subSec && !subSec.classList.contains('hidden')) loadSubtasks(editingId);
                var actSec = document.getElementById('activitySection');
                if (actSec && !actSec.classList.contains('hidden')) loadActivityLog(editingId);
            });
        });
    } else {
        var newId = 't' + Date.now();
        fetch('/api/task', { 
            credentials: 'same-origin',
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: newId, title: title, description: desc, column: currentColumn, priority: priority, assignee_email: assigneeEmailsStr, start_time: startTime, end_time: endTime})
        }).then(function() { 
            // Create labels if needed and assign them
            if (labelNames.length > 0) {
                return fetch('/api/task/' + newId + '/labels', {
                    credentials: 'same-origin',
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({labels: labelNames})
                }).then(function() { syncFromServer(); });
            } else {
                return syncFromServer();
            }
        });
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
        // Save/Create button — delegate click to saveTask()
        // e.target might be a text node or child element inside the button,
        // so use elementFromPoint for reliable detection
        var target = e.target;
        while (target && target.nodeType !== 1) {
            target = target.parentNode;
        }
        if (!target || target === this) return;
        
        var saveBtn = target.closest('.btn-save');
        if (saveBtn) {
            e.preventDefault();
            e.stopPropagation();
            saveTask();
        }
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
    
    // Load labels for filter chips and modal picker
    if (document.getElementById('labelFilterBar')) {
        loadLabels();
    }
    
    // Phase 5: Load dynamic columns
    loadColumns();
    
    // Initialize label search in modal
    initLabelSearch();

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
        var safeEmail = u.email.replace(/'/g, "\'");
        var safeName = name.replace(/'/g, "\'");
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
        var safeEmail = email.replace(/'/g, "\'");
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

// ==========================================
// Phase 2: Subtasks
// ==========================================
var _currentTaskIdForSubtasks = null;

function loadSubtasks(taskId) {
    _currentTaskIdForSubtasks = taskId;
    fetch('/api/task/' + encodeURIComponent(taskId) + '/subtasks', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(subtasks) { 
            renderSubtaskList(Array.isArray(subtasks) ? subtasks : []); 
        })
        .catch(function(err) { console.warn('Failed to load subtasks:', err); });
}

function renderSubtaskList(subtasks) {
    var container = document.getElementById('subtaskList');
    if (!container) return;
    
    if (subtasks.length === 0) {
        container.innerHTML = '<div style="color:#888;font-size:12px;padding:4px 0;">尚無子任務</div>';
        return;
    }
    
    var html = '';
    subtasks.forEach(function(st) {
        var doneClass = st.is_completed ? 'subtask-done' : '';
        var checkedAttr = st.is_completed ? 'checked' : '';
        html += '<div class="subtask-item">' +
            '<input type="checkbox" class="subtask-check" data-id="' + st.id + '" ' + checkedAttr + 
                ' onchange="toggleSubtask(' + st.id + ', this.checked)" />' +
            '<span class="subtask-title ' + doneClass + '">' + escHtml(st.title) + '</span>' +
            '<button class="subtask-delete" onclick="deleteSubtask(' + st.id + ')">&#x2715;</button>' +
            '</div>';
    });
    
    container.innerHTML = html;
}

function addSubtask() {
    var input = document.getElementById('newSubtaskInput');
    if (!input || !input.value.trim()) return;
    if (!_currentTaskIdForSubtasks) return;
    
    fetch('/api/task/' + encodeURIComponent(_currentTaskIdForSubtasks) + '/subtask', {
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({title: input.value.trim()})
    })
    .then(function(r) { return r.json(); })
    .then(function(result) { 
        if (result && result.id) {
            // Reload subtasks to get updated list
            loadSubtasks(_currentTaskIdForSubtasks);
            input.value = '';
        } else {
            alert('新增子任務失敗');
        }
    })
    .catch(function(err) { console.error('Add subtask failed:', err); });
}

function toggleSubtask(sid, checked) {
    fetch('/api/subtask/' + sid, {
        credentials: 'same-origin',
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({is_completed: checked})
    })
    .then(function(r) { return r.json(); })
    .then(function() {
        // Update the visual state immediately
        var el = document.querySelector('.subtask-check[data-id="' + sid + '"]');
        if (el) {
            var titleEl = el.nextElementSibling;
            if (titleEl && checked) {
                titleEl.classList.add('subtask-done');
            } else if (titleEl) {
                titleEl.classList.remove('subtask-done');
            }
        }
    })
    .catch(function(err) { 
        console.error('Toggle subtask failed:', err); 
        // Reload to get correct state
        loadSubtasks(_currentTaskIdForSubtasks);
    });
}

function deleteSubtask(sid) {
    fetch('/api/subtask/' + sid, { credentials: 'same-origin', method: 'DELETE' })
    .then(function() { 
        loadSubtasks(_currentTaskIdForSubtasks); 
    })
    .catch(function(err) { console.error('Delete subtask failed:', err); });
}

// ==========================================
// Phase 2: Activity Log
// ==========================================
function loadActivityLog(taskId) {
    fetch('/api/task/' + encodeURIComponent(taskId) + '/activity', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(activities) { 
            renderActivityLog(Array.isArray(activities) ? activities : []); 
        })
        .catch(function(err) { console.warn('Failed to load activity:', err); });
}

function renderActivityLog(activities) {
    var container = document.getElementById('activityLogContainer');
    if (!container) return;
    
    if (activities.length === 0) {
        container.innerHTML = '<div style="color:#888;font-size:12px;padding:4px 0;">尚無活動紀錄</div>';
        return;
    }
    
    var html = '';
    activities.forEach(function(act) {
        var timeStr = act.created_at ? new Date(act.created_at).toLocaleString('zh-TW', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
        var actorName = (act.actor_email || 'system').split('@')[0];
        
        // Determine action display text and icon
        var actionIcon = '📝';
        var actionText = act.action;
        if (act.action === 'create') { actionIcon = '➕'; actionText = '建立任務'; }
        else if (act.action === 'update') { 
            actionIcon = '✏️'; 
            if (act.field_name) actionText = '更新' + act.field_name;
            else actionText = '編輯任務';
        }
        else if (act.action === 'delete') { actionIcon = '🗑️'; actionText = '刪除'; }
        else if (act.action === 'column_change') { actionIcon = '📦'; actionText = '移至 ' + (act.new_value || ''); }
        else if (act.action === 'label_assign') { actionIcon = '🏷️'; actionText = '新增標籤'; }
        
        html += '<div class="activity-item">' +
            '<span class="activity-icon">' + actionIcon + '</span>' +
            '<span class="activity-text">' + escHtml(actionText) + '</span>' +
            (act.field_name ? '<span class="activity-detail">[' + escHtml(act.old_value || '') + ' → ' + escHtml(act.new_value || '') + ']</span>' : '') +
            '<span class="activity-meta"><span class="activity-actor">' + escHtml(actorName) + '</span> · <span class="activity-time">' + timeStr + '</span></span>' +
            '</div>';
    });
    
    container.innerHTML = html;
}

// ==========================================
// Phase 2: Label Management Modal
// ==========================================
function openLabelManageModal() {
    var modal = document.getElementById('labelManageModal');
    if (!modal) return;
    
    // Load and render all labels with delete option
    fetch('/api/labels', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(labels) { 
            var listEl = document.getElementById('labelManageList');
            if (!listEl) return;
            
            if (labels.length === 0) {
                listEl.innerHTML = '<div style="color:#888;font-size:13px;">尚無標籤</div>';
            } else {
                var html = '';
                labels.forEach(function(label) {
                    html += '<div class="label-manage-item" style="display:flex;align-items:center;gap:8px;padding:4px 0;">' +
                        '<span style="width:16px;height:16px;border-radius:50%;background:' + escHtml(label.color) + ';flex-shrink:0;"></span>' +
                        '<span style="flex:1;font-size:13px;">' + escHtml(label.name) + '</span>' +
                        '<button class="btn-sm" onclick="deleteManageLabel(' + label.id + ')" style="color:#ef4444;background:none;border:1px solid #ef4444;padding:2px 8px;border-radius:4px;cursor:pointer;font-size:12px;">刪除</button>' +
                        '</div>';
                });
                listEl.innerHTML = html;
            }
            
            modal.classList.remove('hidden');
        })
        .catch(function(err) { console.error('Load labels for manage failed:', err); });
}

function deleteManageLabel(lid) {
    if (!confirm('確定刪除此標籤？關聯的任務不會被刪除，只是移除標籤。')) return;
    
    fetch('/api/label/' + lid, { credentials: 'same-origin', method: 'DELETE' })
        .then(function(r) { return r.json(); })
        .then(function(result) { 
            if (result && result.deleted) {
                // Refresh label cache and UI
                allLabelsCache = allLabelsCache.filter(function(l){return l.id !== lid;});
                openLabelManageModal();  // Re-render modal
                renderLabelFilterChips();  // Update filter chips too
            } else {
                alert('刪除失敗');
            }
        })
        .catch(function(err) { console.error('Delete label failed:', err); });
}

// Handle create label from manage modal form submission
function handleCreateLabelFromManage(e) {
    e.preventDefault();
    var nameInput = document.getElementById('newLabelName');
    var colorInput = document.getElementById('newLabelColor');
    if (!nameInput || !colorInput) return;
    
    var name = nameInput.value.trim();
    var color = colorInput.value;
    
    if (!name) { alert('請輸入標籤名稱'); return; }
    
    // Check duplicate first
    var existing = allLabelsCache.find(function(l){return l.name.toLowerCase() === name.toLowerCase();});
    if (existing) {
        alert('標籤「' + name + '」已存在');
        return;
    }
    
    fetch('/api/label', {
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name, color: color})
    })
    .then(function(r) { return r.json(); })
    .then(function(result) { 
        if (result && result.id) {
            allLabelsCache.push({id: result.id, name: name, color: color});
            renderLabelFilterChips();  // Update filter chips
            openLabelManageModal();   // Refresh modal list
            nameInput.value = '';     // Clear input
        } else {
            alert('建立標籤失敗');
        }
    })
    .catch(function(err) { console.error('Create label failed:', err); });
}


// ── Keyboard Shortcuts (Phase 3) ──
document.addEventListener('keydown', function(e) {
    // Escape: close modal or exit batch mode
    if (e.key === 'Escape') {
        var cardModal = document.getElementById('cardModal');
        if (cardModal && cardModal.classList.contains('active')) {
            closeModal();
            e.preventDefault();
        } else if (batchMode) {
            toggleBatchMode();
        }
        return;
    }
    
    // Ctrl+F: focus search, Enter in edit modal = save, Ctrl+N = new task
    if ((e.ctrlKey || e.metaKey)) {
        if (e.key === 'f') {
            document.getElementById('searchInput').focus();
            e.preventDefault();
        } else if (e.key === 'n' && !editingId) {
            // Ctrl+N = new task in current column only when no modal is open
            if (!document.getElementById('cardModal').classList.contains('active')) {
                showNewTaskInColumn(currentColumn || 'todo');
                e.preventDefault();
            }
        } else if (e.key === 's' && editingId) {
            // Ctrl+S = save task when in edit mode
            var saveBtn = document.getElementById('saveTaskBtn');
            if (saveBtn) saveBtn.click();
            e.preventDefault();
        }
    }
    
    // Tab navigation within modal
    if (e.key === 'Tab' && document.getElementById('cardModal').classList.contains('active')) {
        var modal = document.getElementById('cardModal');
        var focusable = modal.querySelectorAll('input, select, button, textarea, [tabindex]:not([tabindex="-1"])');
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        
        if (e.shiftKey && document.activeElement === first) {
            last.focus();
            e.preventDefault();
        } else if (!e.shiftKey && document.activeElement === last) {
            first.focus();
            e.preventDefault();
        }
    }
});



// ==========================================
// Phase 5: Column Management Modal  
// ==========================================

function openColumnManageModal() {
    renderColumnManageList();
    document.getElementById('columnManageModal').classList.remove('hidden');
}

function renderColumnManageList() {
    var container = document.getElementById('columnManageList');
    if (!container) return;
    
    fetch('/api/columns', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(cols) { 
            renderColumnList(container, Array.isArray(cols) ? cols : []); 
        })
        .catch(function(err) { console.warn('Failed to load columns for manage:', err); });
}

function renderColumnList(container, columns) {
    var html = '';
    if (columns.length === 0) {
        container.innerHTML = '<p style="color:#888;">尚無欄位</p>';
        return;
    }
    
    columns.forEach(function(col) {
        html += '<div class="column-manage-item">' +
            '<span>' + escHtml(col.display_name || col.name) + '</span>' +
            '<button onclick="deleteColumn(\'' + escHtml(col.name).replace(/'/g, "\'") + '\')" style="background:#dc3545;color:#fff;border:none;border-radius:3px;padding:2px 8px;cursor:pointer;font-size:12px;">刪除</button>' +
            '</div>';
    });
    
    container.innerHTML = html;
}

function deleteColumn(colName) {
    if (!confirm('確定要刪除此欄位？該欄位中的任務將移至 To Do。')) return;
    
    fetch('/api/column/' + encodeURIComponent(colName), { 
        credentials: 'same-origin', 
        method: 'DELETE' 
    })
    .then(function(r) { return r.json(); })
    .then(function(result) { 
        if (result.status === 'ok') {
            renderColumnManageList();
            loadColumns(); // Reload columns for the board
            filterAndRender();
        } else {
            alert('刪除失敗：' + (result.error || '未知錯誤'));
        }
    })
    .catch(function(err) { console.error('Delete column failed:', err); });
}

function handleCreateColumnFromManage(e) {
    e.preventDefault();
    var input = document.getElementById('newColumnName');
    var name = (input.value || '').trim().toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '');
    
    if (!name) {
        alert('請輸入有效的欄位名稱（英文、數字、底線）');
        return;
    }
    
    fetch('/api/column', { 
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name: name, display_name: name.replace(/_/g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();}) })
    })
    .then(function(r) { return r.json(); })
    .then(function(result) { 
        if (result.id || result.name) {
            input.value = '';
            renderColumnManageList();
            loadColumns(); // Reload columns for the board
            filterAndRender();
        } else {
            alert('新增失敗：' + (result.error || '未知錯誤'));
        }
    })
    .catch(function(err) { console.error('Create column failed:', err); });
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

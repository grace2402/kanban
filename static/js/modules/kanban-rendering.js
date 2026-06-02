// ==========================================
// Kanban Board - Module: Rendering & Columns
// ==========================================
// Handles: column DOM creation, card HTML generation, board rendering, filtering
// ==========================================

/* ── Column Layout ── */

function _getDisplayName(slug) {
    for (var i = 0; i < allColumns.length; i++) {
        if (allColumns[i].name === slug && allColumns[i].display_name) {
            return allColumns[i].display_name;
        }
    }
    var names={'backlog':'Backlog','todo':'To Do','in_progress':'In Progress','review':'Review','done':'Done'};
    return names[slug] || slug.replace(/_/g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});
}

function getColumnOrder() {
    var sorted = allColumns.slice().sort(function(a,b) { return (a.sort_order||0) - (b.sort_order||0); });
    return sorted.map(function(c){ return c.name; });
}

function renderColumnLayout() {
    var board = document.getElementById('kanbanBoard');
    if (!board) return;
    
    // Preserve login gate overlay
    var loginGate = board.querySelector('.login-gate');
    var existingCols = board.querySelectorAll('.kanban-column');
    for (var i = 0; i < existingCols.length; i++) {
        board.removeChild(existingCols[i]);
    }
    
    allColumns.forEach(function(col) {
        var icon = col.display_name && /\p{Emoji}/u.test(col.display_name ? col.display_name.match(/^(\p{Emoji}\s*)?/) : '') 
            ? '' 
            : (COLUMN_ICONS[col.name] || '');
        
        var displayName = col.display_name || _getDisplayName(col.name);
        
        var div = document.createElement('div');
        div.className = 'kanban-column column-' + col.name;
        div.setAttribute('data-column', col.name);
        
        div.innerHTML = 
            '<div class="column-header"><span>' + (icon ? icon + ' ' : '') + escHtml(displayName) + '</span><span class="column-count" id="count-' + col.name + '">0</span></div>' +
            '<div class="column-body" id="col-' + col.name + '" ondragover="allowDrop(event)" ondrop="dropCard(event, \'' + col.name + '\')"></div>' +
            '<button class="add-card-btn" onclick="openAddModal(\'' + col.name + "')\">+ 新增任務</button>";
        
        board.appendChild(div);
    });
}

function loadColumns(callback) {
    fetch('/api/columns', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(cols) { 
            if (Array.isArray(cols) && cols.length > 0) {
                allColumns = cols;
            } else {
                allColumns = DEFAULT_COLUMNS.map(function(name, i) {
                    return { name: name, display_name: _getDisplayName(name), sort_order: i + 1 };
                });
            }
            renderColumnLayout();
            if (callback) callback();
        })
        .catch(function(err) { 
            console.warn('Failed to load columns:', err); 
            allColumns = DEFAULT_COLUMNS.map(function(name, i) {
                return { name: name, display_name: _getDisplayName(name), sort_order: i + 1 };
            });
            renderColumnLayout();
            if (callback) callback();
        });
}

/* ── Task Card Rendering ── */

function renderCard(t) {
    var footerParts = '<span class="priority-badge ' + t.priority + '">' + PRIORITY_LABELS[t.priority] + '</span>';
    var overdueClass = isOverdue(t) ? 'kanban-card priority-' + t.priority + ' overdue' : 'kanban-card priority-' + t.priority;
    
    if (t.assignee_email && t.assignee_email !== '') {
        var assignees = t.assignee_email.split(',').map(function(e){return e.trim();}).filter(Boolean);
        footerParts += assignees.map(function(a) {
            return '<span class="assignee-mini">👤 ' + escHtml(a.split('@')[0]) + '</span>';
        }).join('');
    }
    if (t.start_time) {
        var st = parseTimestamp(t.start_time);
        footerParts += '<span class="time-mini">📅 ' + escHtml(st.toLocaleDateString('zh-TW')) + '</span>';
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
        batchCb = '<input type="checkbox" class="task-checkbox" data-id="' + t.id + '" ' + checked + 
                  ' onclick="event.stopPropagation();toggleSelect(\'' + t.id.replace(/'/g, "\\'") + '\')">';
    }

    // Overdue warning icon
    var overdueIcon = isOverdue(t) ? '<span class="overdue-icon" title="已逾期">⚠️</span>' : '';

    return '<div class="' + overdueClass + '" draggable="true" ondragstart="dragStart(event)" data-id="' + t.id + '">' +
        batchCb +
        overdueIcon +
        '<div class="card-title">' + escHtml(t.title) + '</div>' +
        labelHtml +
        (t.description ? '<div class="card-desc">' + escHtml(t.description) + '</div>' : '') +
        '<div class="card-footer">' + footerParts +
            '<div class="card-actions">' +
                '<button onclick="editTask(\'' + t.id.replace(/'/g, "\\'") + '\')" title="編輯">✏️</button>' +
                '<button onclick="cloneTask(\'' + t.id.replace(/'/g, "\\'") + '\')" title="複製任務">📋</button>' +
                '<button onclick="deleteTask(\'' + t.id.replace(/'/g, "\\'") + '\')" title="刪除">🗑️</button>' +
            '</div></div></div>';
}

/* ── Board Rendering & Filtering ── */

function renderBoard() {
    var columnOrder = allColumns.length > 0 ? getColumnOrder() : DEFAULT_COLUMNS;
    
    columnOrder.forEach(function(col) {
        var c = document.getElementById('col-' + col);
        if (!c) return;
        
        var filtered = tasks.filter(function(t) { 
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
    
    updateBatchCount();
}

function filterAndRender() {
    searchQuery = document.getElementById('searchInput') ? document.getElementById('searchInput').value.trim() : '';
    priorityFilter = document.getElementById('priorityFilter') ? document.getElementById('priorityFilter').value : '';
    renderBoard();
    loadDashboardStats();
}

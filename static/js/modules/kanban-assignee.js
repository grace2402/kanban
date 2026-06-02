// ==========================================
// Kanban Board - Module: Assignee Search & Column Manage
// ==========================================
// Handles: user search dropdowns, column management modal
// ==========================================

/* ── Assignee Search Logic ── */

var _assigneeInitDone = false;

function initAssigneeSearch() {
    var ddEl = document.getElementById('assigneeDropdown');
    var input = document.getElementById('taskAssigneeInput');
    
    if (!ddEl || !input) return;
    if (_assigneeInitDone) return;  // Already initialized
    _assigneeInitDone = true;

    // Pre-fetch all users on load so focus shows them instantly.
    fetch('/api/users?q=&page=1&limit=50', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(users) { window._allUsers = Array.isArray(users) ? users : []; })
        .catch(function() { window._allUsers = []; });

    input.addEventListener('focus', function() {
        var q = input.value.trim();
        renderSelfOnly();
        if (window._allUsers && window._allUsers.length > 0) {
            showDropdownFromCache(q);
        } else {
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
            renderSelfOnly();
            window.assigneeSearchTimeout = setTimeout(function() {
                doSearch(q);
            }, 300);
        } else {
            renderSelfOnly();
            if (window._allUsers && window._allUsers.length > 0) {
                showDropdownFromCache('');
            } else {
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
    }, true);  // useCapture to ensure this runs before other listeners
}

/** Build and show dropdown from LOCAL cache (instant, no network delay). */
function showDropdownFromCache(q) {
    var ddEl = document.getElementById('assigneeDropdown');
    if (!ddEl) return;
    
    if (!window._allUsers || window._allUsers.length === 0) return;

    var currentUserEmail = window.currentUserEmail || '';
    var userList;

    if (q && q.length >= 1) {
        var lowerQ = q.toLowerCase();
        userList = window._allUsers.filter(function(u) {
            return u.email.toLowerCase().indexOf(lowerQ) !== -1 ||
                   (u.name || '').toLowerCase().indexOf(lowerQ) !== -1;
        });
    } else {
        userList = window._allUsers.slice();
    }

    if (currentUserEmail) {
        var idx = userList.findIndex(function(u) { return u.email === currentUserEmail; });
        if (idx !== -1) {
            var selfUser = userList[idx];
            userList.splice(idx, 1);
            userList.unshift(selfUser);
        } else {
            userList.unshift({ id: 'self', email: currentUserEmail, name: currentUserEmail.split('@')[0] });
        }
    }

    showDropdownHTML(userList.slice(0, 10));
}

/** Render the current user as a self-assign option (no API call). */
function renderSelfOnly() {
    var currentUserEmail = window.currentUserEmail || '';
    if (!currentUserEmail) return;
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
                var currentUserEmail = window.currentUserEmail || '';
                var idx = users.findIndex(function(u) { return u.email === currentUserEmail; });
                if (idx !== -1 && idx > 0) {
                    var self = users.splice(idx, 1)[0];
                    users.unshift(self);
                } else if (idx === -1 && currentUserEmail) {
                    users.unshift({ id: 'self', email: currentUserEmail, name: currentUserEmail.split('@')[0] });
                }
                showDropdownHTML(users.slice(0, 10));
            })
            .catch(function() {
                showDropdownHTML([]);
            });
    }, 300);
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

    var html = userList.map(function(u) {
        var name = u.name || u.email.split('@')[0];
        var isSelf = (u.email === currentUserEmail);
        var safeEmail = u.email.replace(/\'/g, "\\\\'");
        var safeName = name.replace(/\'/g, "\\\\'");
        return '<div class="assignee-option" onclick="selectAssignee(\'' + safeEmail + '\', \'' + safeName + '\')">' +
               (isSelf ? '<span style="color:#e6a817;">⭐</span>' : '') +
               '<strong>' + escHtml(name) + '</strong> <span style="color:#888;">' + escHtml(u.email) + '</span></div>';
    }).join('');

    ddEl.innerHTML = html;
    ddEl.classList.remove('hidden');
}


/* ── Column Management Modal ── */

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
            '<button onclick="deleteColumn(\'' + escHtml(col.name).replace(/\'/g, "\\\\'") + '\')" style="background:#dc3545;color:#fff;border:none;border-radius:3px;padding:2px 8px;cursor:pointer;font-size:12px;">刪除</button>' +
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

/* ── Label Manage Modal ── */

function openLabelManageModal() {
    var modal = document.getElementById('labelManageModal');
    if (!modal) return;
    
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
                allLabelsCache = allLabelsCache.filter(function(l){return l.id !== lid;});
                openLabelManageModal();  // Re-render modal
                renderLabelFilterChips();  // Update filter chips too
            } else {
                alert('刪除失敗');
            }
        })
        .catch(function(err) { console.error('Delete label failed:', err); });
}

function handleCreateLabelFromManage(e) {
    e.preventDefault();
    var nameInput = document.getElementById('newLabelName');
    var colorInput = document.getElementById('newLabelColor');
    if (!nameInput || !colorInput) return;
    
    var name = nameInput.value.trim();
    var color = colorInput.value;
    
    if (!name) { alert('請輸入標籤名稱'); return; }
    
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

/* ── Show new task in column shortcut ── */

function showNewTaskInColumn(col) {
    currentColumn = col;
    openAddModal(col);
}

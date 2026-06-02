// ==========================================
// Kanban Board - Module: Comments & Subtasks
// ==========================================
// Handles: task comments, subtask management, activity log display
// ==========================================

var _currentTaskIdForSubtasks = null;

/* ── Task Comments ── */

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

/* ── Subtasks ── */

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

/* ── Activity Log ── */

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

// ==========================================
// Kanban Board - Module: Card Modal & Task CRUD
// ==========================================
// Handles: open/edit/save/delete task modals, keyboard shortcuts, bfcache defense
// ==========================================

/* ── Modal Open/Close ── */

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

function openCardModal(id) {
    editingId = id;
    var t = tasks.find(function(x) { return x.id === id; });
    if (!t) return;
    currentColumn = t.column;
    editTask(id);  // Reuse existing edit logic
}

function editTask(id) {
    var t = tasks.find(function(x) { return x.id === id; });
    if (!t) return;
    editingId = id;
    currentColumn = t.column;
    document.getElementById('modalTitle').textContent = '編輯任務';
    document.getElementById('taskTitle').value = t.title;
    
    // Load assignee & time fields (Phase 3: multi)
    if (t.assignee_email && t.assignee_email !== '') {
        var emails = t.assignee_email.split(',').map(function(e){return e.trim();}).filter(Boolean);
        renderAssigneeBadges(emails);
        document.getElementById('assignedUserEmails').value = emails.join(',');
    } else {
        renderAssigneeBadges([]);
        document.getElementById('assignedUserEmails').value = '';
    }
    
    // Bug #3 fix: Split ISO timestamp into separate date+time inputs using parseTimestamp
    if (t.start_time) {
        var st = parseTimestamp(t.start_time);
        document.getElementById('taskStartDate').value = getTodayStrFromObj(st);
        document.getElementById('taskStartTime').value = String(st.getHours()).padStart(2,'0') + ':' + String(st.getMinutes()).padStart(2,'0');
    }
    if (t.end_time) {
        var et = parseTimestamp(t.end_time);
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

/* ── Save Task (Create/Update) ── */

function saveTask() {
    var title = document.getElementById('taskTitle').value.trim();
    var desc = document.getElementById('taskDesc').value.trim();
    var priority = document.getElementById('taskPriority').value;
    if (!title) { alert('請輸入標題'); return; }

    // Hide save button immediately to prevent double-clicks (Issue #2)
    var btn = document.getElementById('saveTaskBtn');
    if (btn) {
        btn.style.visibility = 'hidden';
    }

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
        // BUG #2 fix: Wait for server sync BEFORE closing modal
        fetch('/api/task/' + editingId, {
            credentials: 'same-origin',
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({title: title, description: desc, column: currentColumn, priority: priority, assignee_email: assigneeEmailsStr, start_time: startTime, end_time: endTime})
        })
        .then(function() { 
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
            return (labelPromise || Promise.resolve());
        })
        .then(function() { 
            // BUG #2 fix: Wait for server sync to complete before closing modal
            return fetch('/api/tasks', { credentials: 'same-origin' }).then(function(r) { return r.json(); });
        })
        .then(function(data) { tasks = data; saveTasks(); renderBoard(); })
        .catch(function(err) { console.error('Save failed:', err); })
        .finally(function() { 
            if (btn) btn.style.visibility = 'visible';  // Restore button visibility
            closeModal(); 
            loadDashboardStats(); 
        });
    } else {
        var newId = 't' + Date.now();
        fetch('/api/task', { 
            credentials: 'same-origin',
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: newId, title: title, description: desc, column: currentColumn, priority: priority, assignee_email: assigneeEmailsStr, start_time: startTime, end_time: endTime})
        })
        .then(function() { 
            // Create labels if needed and assign them
            if (labelNames.length > 0) {
                return fetch('/api/task/' + newId + '/labels', {
                    credentials: 'same-origin',
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({labels: labelNames})
                });
            }
        })
        .then(function() { 
            // BUG #2 fix: Wait for server sync to complete before closing modal
            return fetch('/api/tasks', { credentials: 'same-origin' }).then(function(r) { return r.json(); });
        })
        .then(function(data) { tasks = data; saveTasks(); renderBoard(); })
        .catch(function(err) { console.error('Create failed:', err); })
        .finally(function() { 
            if (btn) btn.style.visibility = 'visible';  // Restore button visibility
            closeModal(); 
            loadDashboardStats(); 
        });
    }
}

function deleteTask(id) {
    if (!confirm('確定刪除此任務？')) return;
    
    tasks = tasks.filter(function(t) { return t.id !== id; });
    saveTasks();
    renderBoard();
    loadDashboardStats();  // Issue #3 fix: update stats after deletion
    
    fetch('/api/task/' + id, { credentials: 'same-origin', method: 'DELETE' })
        .catch(function(err) { console.error('Delete failed:', err); });
}

/* ── Keyboard Shortcuts ── */

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeModal();
});

// Modal click handler for save button & close on backdrop click
if (document.getElementById('cardModal')) {
    document.getElementById('cardModal').addEventListener('click', function(e) {
        if (e.target === this) closeModal();
        
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

/* ── bfcache defense ── */

window.addEventListener('pageshow', function(e) {
    if (e.persisted) {
        console.log('[kanban] bfcache restore detected, refreshing...');
        fetch('/api/tasks', { credentials: 'same-origin' })
            .then(function(r) { 
                return r.json().then(function(data) {
                    if (r.ok && Array.isArray(data)) {
                        tasks = data;
                        saveTasks();  
                        renderBoard();
                        loadDashboardStats();
                    } else {
                        window.location.href = '/login';
                    }
                });
            })
            .catch(function(err) { 
                console.warn('bfcache refresh failed:', err);
                tasks = DEFAULT_TASKS.slice();
                saveTasks();
                renderBoard();
                loadDashboardStats();
            });
    }
});

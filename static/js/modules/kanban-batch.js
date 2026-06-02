// ==========================================
// Kanban Board - Module: Batch Operations & Task CRUD
// ==========================================
// Handles: batch mode, mass move/priority/delete, clone, CSV export
// ==========================================

/* ── Batch Mode UI ── */

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
    loadDashboardStats();
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

/* ── Batch Actions ── */

function doBatchMove() {
    var colSelect = document.getElementById('batchMoveColumn');
    if (!colSelect || !colSelect.value) return;
    
    var ids = Object.keys(selectedTaskIds);
    if (ids.length === 0) return;
    
    fetch('/api/tasks/batch-move', {
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({task_ids: ids, column: colSelect.value})
    }).then(function(r) { return r.json(); })
      .then(function(data) {
          if (data.moved !== undefined && data.moved === ids.length) {
              // OK
          } else {
              console.warn('Batch move returned unexpected count:', data);
          }
          syncFromServer();
          clearSelection();
      }).catch(function(err) {
        console.error('Batch move failed:', err);
        alert('移動失敗');
        syncFromServer();
    });
}

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
          if (data.updated !== undefined && data.updated === ids.length) {
              // OK
          } else {
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

function doBatchDelete() {
    var ids = Object.keys(selectedTaskIds);
    if (ids.length === 0) return;
    
    if (!confirm('確定刪除 ' + ids.length + ' 個任務？')) return;
    
    tasks = tasks.filter(function(t) { return !selectedTaskIds[t.id]; });
    
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
        syncFromServer();
    });
}

/* ── Clone & Export ── */

function cloneTask(taskId) {
    fetch('/api/task/' + taskId + '/clone', {
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
    }).then(function(r) { return r.json(); })
      .then(function(data) {
          if (data.id) {
              syncFromServer();
              openCardModal(data.id);
          } else {
              alert('複製失敗: ' + (data.error || '未知錯誤'));
          }
      }).catch(function(err) {
        console.error('Clone failed:', err);
        alert('複製失敗');
    });
}

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


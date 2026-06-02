// ==========================================
// Kanban Board - Module: Drag & Drop
// ==========================================
// Handles: drag start, drop zone handling, card movement, sync to server
// ==========================================

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
    loadDashboardStats();  // Issue #3 fix: update stats after drag-and-drop
    
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


// ── Column Reorder Drag & Drop (NEW Feature) ──

var _reorderSourceCol = null;  // source column name during reorder drag

document.addEventListener('dragstart', function(e) {
    var header = e.target.closest('.column-header');
    if (!header) return;
    
    var colEl = header.closest('.kanban-column');
    if (!colEl) return;
    
    _reorderSourceCol = colEl.getAttribute('data-column');
    e.dataTransfer.setData('text/plain', 'COLUMN:' + _reorderSourceCol);
    e.dataTransfer.effectAllowed = 'move';
    header.style.opacity = '0.5';
});

document.addEventListener('dragover', function(e) {
    var colEl = e.target.closest('.kanban-column');
    if (!colEl || !_reorderSourceCol) return;
    
    e.preventDefault();
    var targetName = colEl.getAttribute('data-column');
    if (targetName !== _reorderSourceCol) {
        colEl.querySelector('.column-header').style.borderTop = '3px solid #3b82f6';
    }
});

document.addEventListener('dragleave', function(e) {
    var header = e.target.closest('.column-header');
    if (header) {
        header.style.borderTop = '';
    }
});

document.addEventListener('drop', function(e) {
    var colEl = e.target.closest('.kanban-column');
    if (!colEl || !_reorderSourceCol) return;
    
    e.preventDefault();
    document.querySelectorAll('.column-header').forEach(function(h) { h.style.borderTop = ''; });
    
    var targetName = colEl.getAttribute('data-column');
    if (targetName === _reorderSourceCol) return;  // dropped on self
    
    _updateColumnSortOrder(_reorderSourceCol, targetName);
});

function _updateColumnSortOrder(sourceCol, targetCol) {
    fetch('/api/columns/reorder', {
        credentials: 'same-origin',
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ source_column: sourceCol, target_column: targetCol })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) { 
        if (data.status === 'ok') {
            loadColumns();  // Re-render board with new order
        } else {
            alert('移動欄位順序失敗：' + (data.error || '未知錯誤'));
        }
    })
    .catch(function(err) { 
        console.error('Column reorder failed:', err); 
        alert('移動欄位順序失敗'); 
    });
}


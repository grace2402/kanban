// ==========================================
// Kanban Board - Module: Labels & Assignees
// ==========================================
// Handles: label filter chips, label CRUD in modals, assignee search UI
// ==========================================

/* ── Label Filter Chips ── */

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
    loadDashboardStats();
    renderLabelFilterChips();
}

/* ── Label Modal Helpers ── */

function loadTaskLabelsForModal(taskId) {
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
               ' <button type="button" onclick="removeLabelFromModal(\'' + name.replace(/\'/g, "\\\\'") + '\')" style="background:none;border:none;color:rgba(255,255,255,0.8);cursor:pointer;font-size:12px;padding:0 0 0 4px;">&#x2715;</button></span>';
    }).join('');
    
    container.innerHTML = html;
}

function addLabelToModal(name) {
    var input = document.getElementById('taskLabelInput');
    if (!input || !name || name.trim() === '') return;
    
    var currentVal = document.getElementById('assignedUserEmails').getAttribute('data-labels') || '';
    var names = currentVal ? currentVal.split(',').map(function(n){return n.trim();}).filter(Boolean) : [];
    
    if (names.indexOf(name.trim()) !== -1) return;
    
    names.push(name.trim());
    document.getElementById('assignedUserEmails').setAttribute('data-labels', names.join(','));
    
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

function initLabelSearch() {
    var input = document.getElementById('taskLabelInput');
    var dd = document.getElementById('labelSearchDropdown');
    
    if (!input || !dd) return;
    
    input.addEventListener('focus', function() { showLabelDropdown(input.value.trim()); });
    
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
        var matches = allLabelsCache.filter(function(l){ 
            return l.name.toLowerCase().indexOf(q.toLowerCase()) !== -1; 
        });
        
        var html = '';
        matches.forEach(function(label) {
            html += '<div class="assignee-option" onclick="addLabelToModal(' + label.name.replace(/\'/g, "\\\\'") + '); document.getElementById(\'labelSearchDropdown\').classList.add(\'hidden\');" style="color:' + escHtml(label.color) + ';">&#x1f3ff; ' + escHtml(label.name) + '</div>';
        });
        
        if (matches.length === 0 && q) {
            html += '<div class="assignee-option" onclick="createNewLabel(' + q.replace(/\'/g, "\\\\'") + '); document.getElementById(\'labelSearchDropdown\').classList.add(\'hidden\');" style="color:#10b981;">＋ 建立新標籤: ' + escHtml(q) + '</div>';
        }
        
        dd.innerHTML = html;
    } else {
        var html = '';
        allLabelsCache.forEach(function(label) {
            html += '<div class="assignee-option" onclick="addLabelToModal(' + label.name.replace(/\'/g, "\\\\'") + '); document.getElementById(\'labelSearchDropdown\').classList.add(\'hidden\');" style="color:' + escHtml(label.color) + ';">&#x1f3ff; ' + escHtml(label.name) + '</div>';
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
    var existing = allLabelsCache.find(function(l){return l.name.toLowerCase() === name.toLowerCase();});
    if (existing) { addLabelToModal(existing.name); return; }
    
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
            allLabelsCache.push({id: result.id, name: name, color: color});
            addLabelToModal(name);
            renderLabelFilterChips();
        } else { alert('建立標籤失敗'); }
    }).catch(function(err) { console.error('Create label failed:', err); });
}

document.addEventListener('click', function(e) {
    if (!e.target.closest('.form-labels')) {
        var dd = document.getElementById('labelSearchDropdown');
        if (dd) dd.classList.add('hidden');
    }
});


/* ── Assignee Search UI ── */

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
        var safeEmail = email.replace(/\'/g, "\\\\'");
        return '<span class="assignee-pill">' + escHtml(name) + ' <button type="button" onclick="removeAssignee(\'' + safeEmail + '\')" style="background:none;border:none;color:#e74c5c;cursor:pointer;font-size:12px;padding:0 0 0 2px;">&#x2715;</button></span>';
    }).join('');
    
    container.innerHTML = html;
}

function selectAssignee(email, name) {
    var currentStr = document.getElementById('assignedUserEmails').value || '';
    var currentEmails = currentStr.split(',').map(function(e){return e.trim();}).filter(Boolean);
    
    if (currentEmails.indexOf(email) !== -1) return;
    
    currentEmails.push(email);
    document.getElementById('assignedUserEmails').value = currentEmails.join(',');
    renderAssigneeBadges(currentEmails);
    
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

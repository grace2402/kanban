// ==========================================
// Calendar View - Monthly calendar rendering
// Shows tasks assigned to current user or all tasks
// ==========================================

var calendarMode = 'mine';  // 'mine' | 'all'
var calYear, calMonth;      // Current displayed month (1-indexed)
var calTasks = [];            // Tasks fetched from API
var currentUserEmail = '';  // Set by template via data attribute
var selectedPersonEmails = []; // Multi-select array for 'all' mode

function initCalendar() {
    // Try to read year/month from URL query params first
    var params = new URLSearchParams(window.location.search);
    var urlYear = parseInt(params.get('year'));
    var urlMonth = parseInt(params.get('month'));
    
    if (urlYear && urlMonth >= 1 && urlMonth <= 12) {
        calYear = urlYear;
        calMonth = urlMonth;
    } else {
        // Fall back to current date
        var now = new Date();
        calYear = now.getFullYear();
        calMonth = now.getMonth() + 1;
    }
    renderCalendar();
}

function changeMonth(delta) {
    calMonth += delta;
    if (calMonth < 1) { calMonth = 12; calYear--; }
    if (calMonth > 12) { calMonth = 1; calYear++; }
    
    // Reload assignees when switching months in 'all' mode
    if (calendarMode === 'all') {
        renderPersonCheckboxes();  // Keep selectedPersonEmails unchanged — user's selection persists across month changes.
    }
    
    renderCalendar();
}

function switchCalendarMode(mode) {
    calendarMode = mode;
    
    // Show/hide person filter wrapper (only in 'all' mode)
    var filterWrapper = document.getElementById('filterWrapper');
    if (filterWrapper) {
        filterWrapper.style.display = mode === 'all' ? 'flex' : 'none';
        if (mode === 'all') {
            renderPersonCheckboxes();  // Load assignee list when switching to all mode
            // Don't reset selectedPersonEmails — keep user's selection across mode switches.
        } else {
            var filterList = document.getElementById('personFilterList');
            if (filterList) filterList.classList.remove('open');
        }
    }
    
    document.querySelectorAll('.toggle-btn').forEach(function(btn) {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    renderCalendar();
}

function getMonthLabel(year, month) {
    return year + '年' + formatPadded(month) + '月';
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    try {
        var d = new Date(dateStr);
        return (d.getMonth() + 1) + '/' + d.getDate();
    } catch(e) {
        return dateStr;
    }
}

function formatPadded(n) {
    return n < 10 ? '0' + n : '' + n;
}

function escHtml(s) {
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

function renderCalendar() {
    // Update label
    document.getElementById('currentMonthLabel').textContent = getMonthLabel(calYear, calMonth);

    // Always fetch all month's tasks without backend user filter — clicking any day cell should show ALL content for that day.
    var url = '/api/calendar?month=' + calYear + '-' + formatPadded(calMonth);

    fetch(url, { credentials: 'same-origin' })
        .then(function(r) { 
            if (!r.ok) console.error('[kanban] /api/calendar status:', r.status);
            return r.json(); 
        })
        .then(function(data) {
            calTasks = data || [];
            buildCalendarGrid();
        })
        .catch(function(err) {
            console.error('取得行事曆任務失敗:', err);
            calTasks = [];
            buildCalendarGrid();
        });
}

function getDaysInMonth(year, month) {
    return new Date(year, month, 0).getDate();
}

function getFirstDayOfMonth(year, month) {
    // Returns day of week (0=Sun, 1=Mon, ..., 6=Sat)
    return new Date(year, month - 1, 1).getDay();
}

// Monday-first order: 一二三四五六日
var DAY_NAMES = ['一', '二', '三', '四', '五', '六', '日'];

function buildCalendarGrid() {
    var grid = document.getElementById('calendarGrid');
    if (!grid) return;

    // Clear existing
    grid.innerHTML = '';

    // Day headers (Monday first)
    DAY_NAMES.forEach(function(name) {
        var header = document.createElement('div');
        header.className = 'calendar-day-header';
        header.textContent = name;
        grid.appendChild(header);
    });

    // Calculate offset: convert Sunday=0..Sat=6 to Mon=0..Sun=6
    // (day + 6) % 7 maps: Sun(0)->6, Mon(1)->0, Tue(2)->1, ..., Sat(6)->5
    var firstDay = getFirstDayOfMonth(calYear, calMonth);
    var offset = (firstDay + 6) % 7;
    var daysInMonth = getDaysInMonth(calYear, calMonth);

    // Empty cells before first day (using Monday-first offset)
    for (var i = 0; i < offset; i++) {
        var emptyCell = document.createElement('div');
        emptyCell.className = 'calendar-cell empty';
        grid.appendChild(emptyCell);
    }

    // Day cells
    var today = new Date();
    for (var d = 1; d <= daysInMonth; d++) {
        var cell = document.createElement('div');
        cell.className = 'calendar-cell';

        // Mark today
        if (today.getFullYear() === calYear && today.getMonth() + 1 === calMonth && today.getDate() === d) {
            cell.classList.add('today');
        }

        // Date number
        var dateLabel = document.createElement('div');
        dateLabel.className = 'cell-date';
        dateLabel.textContent = d;
        cell.appendChild(dateLabel);

        // Find tasks for this day — match if the day falls within [start_time, end_time] range AND matches multi-select filter
        var dateStr = calYear + '-' + formatPadded(calMonth) + '-' + formatPadded(d);
        
        // Helper to check if task assignee matches selected emails (handles comma-separated multi-assignees)
        function matchesMultiSelect(task) {
            if (!task.assignee_email || selectedPersonEmails.length === 0) return true;
            var taskAssignees = task.assignee_email.split(',').map(function(e){return e.trim();});
            // Task matches if ANY of its assignees are in the selected list
            return taskAssignees.some(function(a){ return selectedPersonEmails.indexOf(a) !== -1; });
        }

        var dayTasks = calTasks.filter(function(t) {
            if (!t.start_time) return false;
            // Extract YYYY-MM-DD from start_time (handles ISO with timezones like "2026-05-19T09:00:00+08:00")
            var startD = t.start_time.substring(0, 10);
            if (dateStr < startD) return false; // day is before event starts
            // If there's an end_time, check it too
            if (t.end_time) {
                var endD = t.end_time.substring(0, 10);
                if (dateStr > endD) return false; // day is after event ends
                return matchesMultiSelect(t);
            }
            // No end_time: show only on the exact start date
            return dateStr === startD && matchesMultiSelect(t);
        });

        // Add click handler on cell — EVERY day is clickable (even empty days)
        (function(cellDateForModal) {
            cell.addEventListener('click', function(e) {
                // For each day, fetch the tasks from calTasks and show modal
                var dateStr = cellDateForModal;
                
                function matchesMultiSelect(task) {
                    if (!task.assignee_email || selectedPersonEmails.length === 0) { console.log('[MS] no filter needed'); return true; }
                    var taskAssignees = task.assignee_email.split(',').map(function(e){return e.trim();});
                    const result = taskAssignees.some(function(a){ return selectedPersonEmails.indexOf(a) !== -1; });
                    console.log('[MS]', task.title, 'assignees:', taskAssignees, 'result:', result);
                    return result;
                }
                
                // Re-derive dayTasks (same logic as above, but now for any day)
                console.log('[CLICK] calTasks.length:', calTasks ? calTasks.length : 'null', 'selectedPersonEmails:', JSON.stringify(selectedPersonEmails));
                var dayT = calTasks.filter(function(t) {
                    if (!t.start_time) return false;
                    var startD = t.start_time.substring(0, 10);
                    if (dateStr < startD) return false;
                    if (t.end_time) {
                        var endD = t.end_time.substring(0, 10);
                        if (dateStr > endD) return false;
                        return matchesMultiSelect(t);
                    }
                    return dateStr === startD && matchesMultiSelect(t);
                });
                
                console.log('[CLICK] dayT.length:', dayT.length);
                openTaskModalForDay(cellDateForModal, dayT);
                // Prevent the global "click outside to close" listener from immediately closing the modal
                e.stopPropagation();
            });
        })(calYear + '-' + formatPadded(calMonth) + '-' + formatPadded(d));

        // Render task pills inside the cell (for visual preview only)
        
        /* ── Detect per-person time overlaps ── */
        function isAllDay(startTime, endTime) {
            if (!startTime || !endTime) return false;
            var m = startTime.match(/(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?:\+\d{2}:?\d{2})?$/);
            var e = endTime.match(/(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?:\+\d{2}:?\d{2})?$/);
            if (!m || !e) return false;
            return parseInt(m[2]) === 0 && parseInt(m[3]) === 0 && parseInt(e[2]) === 23 && parseInt(e[3]) === 59;
        }

        function taskTimeRange(t, dayStr) {
            // Return [startMs, endMs] in local time for this specific day.
            // Returns null if no usable time info.
            var hasStart = t.start_time && !isAllDay(t.start_time, t.end_time);
            if (!hasStart) return null;
            try {
                var startD = new Date(t.start_time);
                var sH = startD.getHours(), sM = startD.getMinutes();
                // Clip to the displayed day boundaries
                var dayStart = new Date(dayStr + 'T00:00:00').getTime();
                var dayEnd   = new Date(dayStr + 'T23:59:59').getTime();
                var sMs = startD.getTime();
                // If task starts before this day, clamp to dayStart (it's still active)
                if (sMs < dayStart) { /* will use dayStart below */ }
                var eTime = t.end_time && !isAllDay(t.start_time, t.end_time) ? new Date(t.end_time).getTime() : dayEnd;
                return [Math.max(sMs, dayStart), Math.min(eTime, dayEnd)];
            } catch(_) { return null; }
        }

        function overlaps(aStart, aEnd, bStart, bEnd) {
            // Two intervals overlap if one starts before the other ends AND vice versa.
            return aStart < bEnd && bStart < aEnd;
        }

        // Build overlap map: task.id → true if this task has any time conflict with another task on same day for overlapping assignees
        var overlapSet = {};
        for (var i = 0; i < dayTasks.length; i++) {
            var rangeA = taskTimeRange(dayTasks[i], dateStr);
            if (!rangeA) continue; // all-day or no time info — skip specific-time overlap detection
            
            for (var j = i + 1; j < dayTasks.length; j++) {
                var rangeB = taskTimeRange(dayTasks[j], dateStr);
                if (!rangeB) continue;
                
                // Check shared assignees
                var emailsA = (dayTasks[i].assignee_email || '').split(',').map(function(e){return e.trim();}).filter(Boolean);
                var emailsB = (dayTasks[j].assignee_email || '').split(',').map(function(e){return e.trim();}).filter(Boolean);
                var shared = false;
                for (var ai = 0; ai < emailsA.length; ai++) {
                    for (var bi = 0; bi < emailsB.length; bi++) {
                        if (emailsA[ai] === emailsB[bi]) { shared = true; break; }
                    }
                    if (shared) break;
                }
                if (shared && overlaps(rangeA[0], rangeA[1], rangeB[0], rangeB[1])) {
                    overlapSet[dayTasks[i].id] = true;
                    overlapSet[dayTasks[j].id] = true;
                }
            }
        }

        dayTasks.forEach(function(task) {
            var taskEl = document.createElement('div');
            // Priority class for base color + deterministic task-specific color class so tasks in same day are visually distinct
            var COLOR_CLASSES = ['color-blue', 'color-purple', 'color-teal', 'color-pink', 'color-indigo', 'color-cyan'];
            function hashTaskId(id) {
                var hash = 0;
                for (var i = 0; i < id.length; i++) {
                    hash = ((hash << 5) - hash) + id.charCodeAt(i);
                    hash |= 0; // Convert to 32bit int
                }
                return Math.abs(hash) % COLOR_CLASSES.length;
            }
            var extraClass = (task.id in overlapSet) ? ' overlap-highlight' : '';
            taskEl.className = 'cell-task priority-' + (task.priority || 'medium') + ' ' + COLOR_CLASSES[hashTaskId(task.id)] + extraClass;
            taskEl.textContent = task.title;
            taskEl.title = task.title + (task.assignee_email ? '\n👤 ' + task.assignee_email : '');

            // Click to navigate directly to kanban — stopPropagation prevents the parent cell's click handler (which opens day detail modal) from firing first.
            taskEl.addEventListener('click', function(e) {
                e.stopPropagation();
                window.location.href = '/#' + encodeURIComponent(task.id);
            });

            cell.appendChild(taskEl);

            // Show assignee if available and mode is "mine"
            if (calendarMode === 'mine' && task.assignee_email) {
                var asgn = document.createElement('span');
                asgn.className = 'cell-assignee';
                asgn.textContent = '👤 ' + task.assignee_email.split('@')[0];
                cell.appendChild(asgn);
            }
        });

        grid.appendChild(cell);
    }

    // Fill remaining cells to complete the grid (after last day)
    var totalCells = offset + daysInMonth;
    var remainder = totalCells % 7;
    if (remainder !== 0) {
        for (var rem = 0; rem < 7 - remainder; rem++) {
            var extraCell = document.createElement('div');
            extraCell.className = 'calendar-cell empty';
            grid.appendChild(extraCell);
        }
    }
}

// Initialize on load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCalendar);
} else {
    // DOM already loaded, call directly
    initCalendar();
}

// ── Multi-select Person Filter Functions ──
function togglePersonDropdown() {
    var list = document.getElementById('personFilterList');
    if (list) {
        list.classList.toggle('open');
        // Render checkboxes when opening the dropdown
        if (list.classList.contains('open')) {
            renderPersonCheckboxes();
        }
    }
}

function renderPersonCheckboxes() {
    // Fetch ALL system users (not just those with tasks this month) so the filter always shows every possible assignee.
    fetch('/api/users?q=&page=1&limit=50', { credentials: 'same-origin' })
        .then(function(r) { return r.json(); })
        .then(function(users) {
            if (!Array.isArray(users)) users = [];
            
            var listEl = document.getElementById('personFilterList');
            if (!listEl) return;
            
            // Also fetch current month's assignee task counts for the badge display.
            var monthLabel = calYear + '-' + formatPadded(calMonth);
            var countPromise = fetch('/api/calendar/assignees?month=' + encodeURIComponent(monthLabel), { credentials: 'same-origin' })
                .then(function(r) { return r.json(); })
                .then(function(assigneeData) {
                    if (!Array.isArray(assigneeData)) assigneeData = [];
                    var countMap = {};
                    assigneeData.forEach(function(a){ countMap[a.email] = a.name; });
                    return countMap;
                })
                .catch(function(){ return {}; });
            
            // Wait for both: user list and count map.
            Promise.all([countPromise]).then(function(countInfo) {
                var countMap = countInfo[0];
                
                listEl.innerHTML = '';
                
                users.forEach(function(u, idx) {
                    var item = document.createElement('label');
                    item.className = 'filter-checkbox-item';
                    
                    var checkbox = document.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.value = u.email;
                    checkbox.checked = selectedPersonEmails.indexOf(u.email) !== -1;
                    checkbox.onchange = function() { onCheckboxChange(); };
                    
                    var displayName = u.name || u.email.split('@')[0];
                    if (countMap[u.email]) displayName += ' (' + countMap[u.email] + ')';
                    else displayName += ' (' + u.email.split('@')[0] + ')';
                    
                    var textNode = document.createTextNode(displayName);
                    
                    item.appendChild(checkbox);
                    item.appendChild(textNode);
                    listEl.appendChild(item);
                });
                
                if (users.length === 0) {
                    listEl.innerHTML = '<div class="filter-checkbox-item" style="cursor:default;">(系統中無使用者)</div>';
                }
            });
        })
        .catch(function(err) {
            console.error('取得指派人列表失敗:', err);
        });
}

function onCheckboxChange() {
    selectedPersonEmails = [];
    var checkboxes = document.querySelectorAll('#personFilterList input[type="checkbox"]');
    checkboxes.forEach(function(cb) {
        if (cb.checked) selectedPersonEmails.push(cb.value);
    });
    
    // Update badge count
    var badge = document.getElementById('filterCountBadge');
    if (badge) {
        badge.textContent = selectedPersonEmails.length > 0 ? selectedPersonEmails.length : '';
        badge.style.display = selectedPersonEmails.length > 0 ? '' : 'none';
    }
    
    renderCalendar();
}

// Close dropdown when clicking outside
document.addEventListener('click', function(e) {
    var wrapper = document.getElementById('filterWrapper');
    var list = document.getElementById('personFilterList');
    if (wrapper && list && !wrapper.contains(e.target)) {
        list.classList.remove('open');
    }
});

// Close modal when clicking outside the container
// Task Detail Modal Functions
// ==========================================

/**
 * Open the task detail modal for a specific day — shows 24-hour timeline sorted by start time.
 */
function openTaskModalForDay(dateStr, tasks) {
    var modal = document.getElementById('taskModal');
    if (!modal) return;
    
    // Parse date and set title
    try {
        var d = new Date(dateStr);
        document.getElementById('modalTitle').textContent = '📅 ' + getMonthLabel(calYear, calMonth) + ' — ' + (d.getMonth() + 1) + '/' + d.getDate();
    } catch(e) {
        document.getElementById('modalTitle').textContent = '📅 Tasks for ' + dateStr;
    }
    
    var bodyEl = document.getElementById('modalBody');
    if (!bodyEl) return;
    
    // Sort tasks: those with start_time first (sorted by time), then those without.
    // Within same-time, high priority first.
    var priorityOrder = {'high': 1, 'medium': 2, 'low': 3};
    tasks.sort(function(a, b) {
        if (!a.start_time && !b.start_time) return a.title.localeCompare(b.title);
        if (a.start_time && !b.start_time) return -1;
        if (!a.start_time && b.start_time) return 1;
        // Compare by start time, then end time as tiebreaker.
        var cmp = new Date(a.start_time).getTime() - new Date(b.start_time).getTime();
        if (cmp !== 0) return cmp;
        cmp = priorityOrder[b.priority] - priorityOrder[a.priority]; // higher prio first
        if (cmp !== 0) return cmp;
        return a.title.localeCompare(b.title);
    });
    
    /* ── Detect per-person time overlaps in modal timeline ── */
    function isAllDay(startTime, endTime) {
        if (!startTime || !endTime) return false;
        var m = startTime.match(/(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?:\+\d{2}:?\d{2})?$/);
        var e = endTime.match(/(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?:\+\d{2}:?\d{2})?$/);
        if (!m || !e) return false;
        return parseInt(m[2]) === 0 && parseInt(m[3]) === 0 && parseInt(e[2]) === 23 && parseInt(e[3]) === 59;
    }

    function taskTimeRange(t, dayStr) {
        var hasStart = t.start_time && !isAllDay(t.start_time, t.end_time);
        if (!hasStart) return null;
        try {
            var startD = new Date(t.start_time);
            var dayStart = new Date(dayStr + 'T00:00:00').getTime();
            var dayEnd   = new Date(dayStr + 'T23:59:59').getTime();
            var sMs = startD.getTime();
            var eTime = t.end_time && !isAllDay(t.start_time, t.end_time) ? new Date(t.end_time).getTime() : dayEnd;
            return [Math.max(sMs, dayStart), Math.min(eTime, dayEnd)];
        } catch(_) { return null; }
    }

    function overlaps(aStart, aEnd, bStart, bEnd) {
        return aStart < bEnd && bStart < aEnd;
    }

    var modalOverlapSet = {};
    for (var i = 0; i < tasks.length; i++) {
        var rangeA = taskTimeRange(tasks[i], dateStr);
        if (!rangeA) continue;
        for (var j = i + 1; j < tasks.length; j++) {
            var rangeB = taskTimeRange(tasks[j], dateStr);
            if (!rangeB) continue;
            
            var emailsA = (tasks[i].assignee_email || '').split(',').map(function(e){return e.trim();}).filter(Boolean);
            var emailsB = (tasks[j].assignee_email || '').split(',').map(function(e){return e.trim();}).filter(Boolean);
            var shared = false;
            for (var ai = 0; ai < emailsA.length; ai++) {
                for (var bi = 0; bi < emailsB.length; bi++) {
                    if (emailsA[ai] === emailsB[bi]) { shared = true; break; }
                }
                if (shared) break;
            }
            if (shared && overlaps(rangeA[0], rangeA[1], rangeB[0], rangeB[1])) {
                modalOverlapSet[tasks[i].id] = true;
                modalOverlapSet[tasks[j].id] = true;
            }
        }
    }

    if (!tasks || tasks.length === 0) {
        bodyEl.innerHTML = '<div class="no-tasks-msg">這天沒有排定任務<br><small style="color:#666;">目前篩選條件下無符合之任務</small></div>';
    } else {
        bodyEl.innerHTML = ''; // Clear existing
        
        // Build timeline entries: each task is a row showing time range + assignees.
        tasks.forEach(function(task, index) {
            var timelineRow = document.createElement('div');
            timelineRow.className = 'task-dropdown-item' + ((task.id in modalOverlapSet) ? ' overlap-highlight' : '');
            
            var priorityClass = (task.priority || 'medium').toLowerCase();
            var priorityLabel = {'high': '高', 'medium': '中', 'low': '低'}[priorityClass] || task.priority;
            
            // Time display logic: detect all-day events vs specific-time events
            // All-day events are stored as 00:00:00 (start) and 23:59:00 (end) in UTC with no timezone offset.
            function isAllDayEvent(startTime, endTime) {
                if (!startTime || !endTime) return false;
                // Check raw string for all-day pattern: "YYYY-MM-DD HH:mm:ss+00" where time is 00:00/23:59
                var startMatch = startTime.match(/(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2}):(\d{2})([+-]\d{2}:?\d{2})?$/);
                var endMatch = endTime.match(/(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2}):(\d{2})([+-]\d{2}:?\d{2})?$/);
                if (startMatch && endMatch) {
                    var startH = parseInt(startMatch[2]);
                    var startM = parseInt(startMatch[3]);
                    var endH = parseInt(endMatch[2]);
                    var endM = parseInt(endMatch[3]);
                    return startH === 0 && startM === 0 && endH === 23 && endM === 59;
                }
                // Also check ISO format "YYYY-MM-DDTHH:mm:ss+HH:MM"
                var isoStartMatch = startTime.match(/(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})([+-]\d{2}:\d{2})?$/);
                var isoEndMatch = endTime.match(/(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})([+-]\d{2}:\d{2})?$/);
                if (isoStartMatch && isoEndMatch) {
                    var iSH = parseInt(isoStartMatch[2]);
                    var iSM = parseInt(isoStartMatch[3]);
                    var iEH = parseInt(isoEndMatch[2]);
                    var iEM = parseInt(isoEndMatch[3]);
                    return iSH === 0 && iSM === 0 && iEH === 23 && iEM === 59;
                }
                return false;
            }

            function formatTimeAMPM(d) {
                var h = d.getHours();
                var m = String(d.getMinutes()).padStart(2, '0');
                var period = h >= 12 ? '下午' : '上午';
                // Convert to 12-hour: 0→12 AM, 1-11→AM, 12→12 PM, 13-23→PM (h-12)
                var h12 = h % 12 || 12;
                return period + ' ' + h12 + ':' + m;
            }

            var timeStr = '未排定時間';
            if (task.start_time) {
                try {
                    // Check for all-day event first (before any timezone conversion)
                    if (isAllDayEvent(task.start_time, task.end_time)) {
                        timeStr = '全天';
                    } else {
                        var st = new Date(task.start_time);
                        timeStr = formatTimeAMPM(st);
                        
                        if (task.end_time && !isAllDayEvent(task.start_time, task.end_time)) {
                            var et = new Date(task.end_time);
                            timeStr += ' ~ ' + formatTimeAMPM(et);
                        } else if (!task.end_time) {
                            // If no end time and start is on the day, infer 1 hour duration.
                            timeStr += ' ~ --:--';
                        }
                    }
                } catch(err) {
                    var raw = task.start_time.substring(0, 16).replace('T', ' ');
                    if (task.end_time && !isAllDayEvent(task.start_time, task.end_time)) {
                        try {
                            var st2 = new Date(task.start_time);
                            var et2 = new Date(task.end_time);
                            timeStr = formatTimeAMPM(st2) + ' ~ ' + formatTimeAMPM(et2);
                        } catch(e2) {
                            timeStr = raw;
                        }
                    } else {
                        timeStr = raw;
                    }
                }
            }
            
            // Assignee display (show all as small pills)
            var assigneeHtml = '';
            if (task.assignee_email) {
                var assignees = task.assignee_email.split(',').map(function(e){return e.trim();}).filter(Boolean);
                if (assignees.length > 0) {
                    assigneeHtml = '<div class="timeline-assignees">' + 
                        assignees.map(function(email) {
                            var name = email.split('@')[0];
                            return '<span class="timeline-assignee-pill">👤 ' + escHtml(name) + '</span>';
                        }).join('') +
                    '</div>';
                }
            }
            
            timelineRow.innerHTML = 
                '<div class="task-timeline-row">' +
                    '<div class="timeline-time" title="' + escHtml(timeStr) + '">' + escHtml(timeStr) + '</div>' +
                    '<div class="timeline-body">' +
                        '<div class="task-title">' + escHtml(task.title || '(無標題)') + ' <span class="task-priority-badge ' + priorityClass + '" style="font-size:10px;">' + priorityLabel + '</span></div>' +
                        assigneeHtml +
                    '</div>' +
                '</div>';
            
            if (task.description) {
                var detailsEl = document.createElement('div');
                detailsEl.className = 'task-details';
                detailsEl.id = 'task-detail-' + index;
                detailsEl.innerHTML = '<div class="task-meta-item"><strong>📝 描述：</strong><br>' + escHtml(task.description) + '</div>';
                
                // Toggle expand/collapse on timeline row click
                (function(row, details) {
                    var header = row.querySelector('.timeline-body');
                    if (header) {
                        header.addEventListener('click', function(e) {
                            e.stopPropagation();
                            var isExpanded = details.classList.contains('expanded');
                            if (isExpanded) {
                                details.classList.remove('expanded');
                                row.style.borderLeftColor = 'transparent';
                            } else {
                                details.classList.add('expanded');
                                row.style.borderLeftColor = 'var(--accent-blue)';
                            }
                        });
                    }
                })(timelineRow, detailsEl);
                
                timelineRow.appendChild(detailsEl);
            }
            
            bodyEl.appendChild(timelineRow);
        });
    }
    
    modal.classList.add('show');
}

// Keep openTaskModal for backward compatibility (e.g. clicking task pills).
function openTaskModal(dateStr, tasks) {
    return openTaskModalForDay(dateStr, tasks);
}

/**
 * Close the task detail modal
 */
function closeTaskModal() {
    var modal = document.getElementById('taskModal');
    if (modal) {
        modal.classList.remove('show');
    }
}

/**
 * Create a dropdown-style task item for the modal
 * @param {Object} task - Task object with id, title, priority, etc.
 * @param {number} index - Index for unique IDs
 * @returns {HTMLElement} Task dropdown element
 */
function createTaskDropdownItem(task, index) {
    var container = document.createElement('div');
    container.className = 'task-dropdown-item';
    
    // Get priority label and class
    var priorityClass = (task.priority || 'medium').toLowerCase();
    var priorityLabel = {'high': '高', 'medium': '中', 'low': '低'}[priorityClass] || task.priority;
    
    // Parse assignee email(s)
    var assigneeText = '';
    if (task.assignee_email) {
        // Handle comma-separated emails
        var assignees = task.assignee_email.split(',').map(function(e) { 
            return e.trim().split('@')[0]; 
        });
        assigneeText = '👤 ' + assignees.join(', ');
    }
    
    // Parse start/end times for display
    var startTime = '';
    if (task.start_time) {
        try {
            var d = new Date(task.start_time);
            startTime = (d.getMonth() + 1) + '/' + d.getDate();
            if (task.end_time) {
                var e = new Date(task.end_time);
                startTime += ' ~ ' + (e.getMonth() + 1) + '/' + e.getDate();
            }
        } catch(err) {
            startTime = task.start_time.substring(0, 16).replace('T', ' ');
        }
    }
    
    // Task header (clickable to expand/collapse)
    var headerEl = document.createElement('div');
    headerEl.className = 'task-header';
    headerEl.innerHTML = '<span class="task-title">' + escHtml(task.title || '(無標題)') + '</span>' + 
                        '<span class="task-priority-badge ' + priorityClass + '">' + priorityLabel + '</span>';
    
    // Task details panel (hidden by default, shown on click)
    var detailsEl = document.createElement('div');
    detailsEl.className = 'task-details';
    detailsEl.id = 'task-detail-' + index;
    detailsEl.innerHTML = '<div class="task-meta-item"><strong>📆 日期：</strong>' + (startTime || '未設定') + '</div>' +
                         (assigneeText ? '<div class="task-meta-item"><strong>' + assigneeText + '</strong></div>' : '') +
                         (task.description ? '<div class="task-meta-item" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border-color);">' + 
                          '<strong>📝 描述：</strong><br>' + escHtml(task.description) + '</div>' : '');
    
    // Toggle expand/collapse on header click
    (function(header, details) {
        header.addEventListener('click', function(e) {
            e.stopPropagation(); // Prevent triggering cell click handler
            var isExpanded = details.classList.contains('expanded');
            
            if (isExpanded) {
                details.classList.remove('expanded');
                header.style.background = '';
            } else {
                details.classList.add('expanded');
                header.style.background = 'rgba(107, 185, 246, 0.15)';
            }
        });
    })(headerEl, detailsEl);
    
    container.appendChild(headerEl);
    container.appendChild(detailsEl);
    
    return container;
}

// Close modal when clicking outside the container
document.addEventListener('click', function(e) {
    var modal = document.getElementById('taskModal');
    if (modal && modal.classList.contains('show')) {
        // Don't close if clicking inside the modal
        if (!modal.contains(e.target)) {
            closeTaskModal();
        }
    }
});

// Close modal with Escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeTaskModal();
    }
});

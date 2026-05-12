/* Settings Search
 *
 * Powers the searchable Settings experience.
 *  - Reads the index from #settings-search-index (JSON injected by web/config.py).
 *  - Filters with simple multi-term scoring (label > keywords > hint > section).
 *  - Renders grouped results, supports arrow-key navigation + Cmd/Ctrl+K focus.
 *  - Clicking a result navigates to /settings/<tab>#setting-<id>; on load the
 *    target form-group (data-setting-id) is scrolled into view and flashed.
 *
 * Defensive: the `var X = window.X || {...}` pattern is used so the script
 * survives HTMX partial swaps that may re-execute it within the same page.
 */
(function() {
    'use strict';

    var root = document.getElementById('settings-search');
    if (!root) return;  // not on a settings page

    var input        = document.getElementById('settings-search-input');
    var resultsEl    = document.getElementById('settings-search-results');
    var clearBtn     = document.getElementById('settings-search-clear');
    var indexScript  = document.getElementById('settings-search-index');
    var activeTab    = root.dataset.activeTab || '';

    if (!input || !resultsEl || !indexScript) return;

    var INDEX = [];
    try {
        INDEX = JSON.parse(indexScript.textContent || '[]');
    } catch (e) {
        console.error('[settings-search] Failed to parse index JSON', e);
        return;
    }
    if (!Array.isArray(INDEX) || INDEX.length === 0) return;

    // Popular settings shown in the empty state (curated subset)
    var POPULAR_IDS = [
        'plex_token',
        'webhook_url',
        'schedule_enabled',
        'number_episodes',
        'path_mappings'
    ];

    // Suggested searches (chips). Maps to common user intent.
    var SUGGESTED_QUERIES = ['token', 'discord', 'rss', 'cron', 'sonarr', 'backup', 'webhook'];

    var selectedIdx    = 0;
    var currentResults = [];
    var debounceTimer  = null;

    // ---- search scoring ---------------------------------------------------
    function scoreEntry(entry, query) {
        var q = query.toLowerCase().trim();
        if (!q) return 0;

        var label = (entry.label || '').toLowerCase();
        var hint  = (entry.hint  || '').toLowerCase();
        var kws   = (entry.keywords || []).join(' ').toLowerCase();
        var sect  = ((entry.section || '') + ' ' + (entry.subsection || '')).toLowerCase();

        var score = 0;
        if (label.indexOf(q) === 0)        score += 100;
        else if (label.indexOf(q) !== -1)  score += 60;
        if (kws.indexOf(q) !== -1)         score += 40;
        if (hint.indexOf(q) !== -1)        score += 15;
        if (sect.indexOf(q) !== -1)        score += 10;

        // Every space-separated term must hit somewhere — otherwise drop
        var haystack = label + ' ' + hint + ' ' + kws + ' ' + sect;
        var terms = q.split(/\s+/).filter(Boolean);
        for (var i = 0; i < terms.length; i++) {
            if (haystack.indexOf(terms[i]) === -1) return 0;
        }

        // Boost entries on the user's current tab — less context switching
        if (activeTab && entry.tab === activeTab) score += 5;

        return score;
    }

    function search(query) {
        if (!query.trim()) return [];
        var scored = [];
        for (var i = 0; i < INDEX.length; i++) {
            var s = scoreEntry(INDEX[i], query);
            if (s > 0) scored.push({ entry: INDEX[i], score: s });
        }
        scored.sort(function(a, b) { return b.score - a.score; });
        return scored.map(function(r) { return r.entry; });
    }

    // ---- rendering helpers -----------------------------------------------
    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, function(c) {
            return ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
            })[c];
        });
    }

    function highlight(text, query) {
        if (!text) return '';
        if (!query.trim()) return escapeHtml(text);
        var terms = query.trim().split(/\s+/)
            .filter(Boolean)
            .map(function(t) { return t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); });
        if (!terms.length) return escapeHtml(text);
        var re = new RegExp('(' + terms.join('|') + ')', 'gi');
        return escapeHtml(text).replace(re, '<mark>$1</mark>');
    }

    function renderResultRow(entry, query, idx) {
        var labelHtml = highlight(entry.label, query);
        var hintHtml  = highlight(entry.hint, query);
        var crumb = entry.subsection
            ? escapeHtml(entry.section) + ' <i data-lucide="chevron-right"></i> ' + escapeHtml(entry.subsection)
            : escapeHtml(entry.section);
        var tone = entry.tone || '';
        return '<div class="ss-result-row" role="option" data-idx="' + idx + '"' +
               ' data-tab="' + escapeHtml(entry.tab) + '"' +
               ' data-setting-id="' + escapeHtml(entry.setting_id) + '">' +
                 '<div class="ss-result-icon ' + tone + '">' +
                     '<i data-lucide="' + escapeHtml(entry.icon || 'circle') + '"></i>' +
                 '</div>' +
                 '<div class="ss-result-body">' +
                     '<div class="ss-result-crumb">' + crumb + '</div>' +
                     '<div class="ss-result-label">' + labelHtml + '</div>' +
                     (hintHtml ? '<div class="ss-result-hint">' + hintHtml + '</div>' : '') +
                 '</div>' +
                 '<div class="ss-result-arrow"><i data-lucide="chevron-right"></i></div>' +
               '</div>';
    }

    function renderEmptyState() {
        // Map ids → entries, drop misses
        var popular = [];
        for (var i = 0; i < POPULAR_IDS.length; i++) {
            for (var j = 0; j < INDEX.length; j++) {
                if (INDEX[j].setting_id === POPULAR_IDS[i]) {
                    popular.push(INDEX[j]);
                    break;
                }
            }
        }

        var chipsHtml = SUGGESTED_QUERIES.map(function(q) {
            return '<button class="ss-chip" type="button" data-suggest="' + escapeHtml(q) + '">' +
                       '<i data-lucide="search" class="ss-chip-icon"></i>' + escapeHtml(q) +
                   '</button>';
        }).join('');

        var popularHtml = popular.map(function(e, i) {
            return renderResultRow(e, '', i);
        }).join('');

        resultsEl.innerHTML =
            '<div class="ss-empty-state">' +
                '<div class="ss-empty-section">' +
                    '<h4><i data-lucide="search"></i> Try searching for</h4>' +
                    '<div class="ss-chip-row">' + chipsHtml + '</div>' +
                '</div>' +
                '<div class="ss-empty-section">' +
                    '<h4><i data-lucide="trending-up"></i> Popular settings</h4>' +
                '</div>' +
            '</div>' +
            '<div class="ss-results-group ss-results-group-popular">' + popularHtml + '</div>';

        currentResults = popular;
        selectedIdx = 0;
        updateSelection();
        refreshIcons();
    }

    function renderNoResults(query) {
        resultsEl.innerHTML =
            '<div class="ss-no-results">' +
                '<i data-lucide="search-x"></i>' +
                '<div>No settings match &ldquo;' + escapeHtml(query) + '&rdquo;</div>' +
                '<div class="ss-no-results-hint">Try a broader term, or browse the tabs below.</div>' +
            '</div>';
        currentResults = [];
        refreshIcons();
    }

    function renderResults(query) {
        var results = search(query);
        currentResults = results;
        selectedIdx = 0;

        if (results.length === 0) {
            renderNoResults(query);
            return;
        }

        // Group by section, preserving score-order
        var grouped = {};
        var order = [];
        results.forEach(function(r, i) {
            var key = r.section || 'Other';
            if (!grouped[key]) { grouped[key] = []; order.push(key); }
            grouped[key].push({ entry: r, idx: i });
        });

        var sections = order.map(function(name) {
            var rows = grouped[name].map(function(g) {
                return renderResultRow(g.entry, query, g.idx);
            }).join('');
            return '<div class="ss-results-group">' +
                       '<div class="ss-results-group-header">' +
                           '<i data-lucide="chevron-right"></i>' + escapeHtml(name) +
                       '</div>' + rows +
                   '</div>';
        }).join('');

        resultsEl.innerHTML =
            '<div class="ss-results-summary">' +
                '<div><strong>' + results.length + '</strong> result' +
                    (results.length === 1 ? '' : 's') +
                    ' in <strong>' + order.length + '</strong> categor' +
                    (order.length === 1 ? 'y' : 'ies') +
                '</div>' +
                '<div><kbd>&uarr;&darr;</kbd> navigate &nbsp; <kbd>&crarr;</kbd> open &nbsp; <kbd>Esc</kbd> close</div>' +
            '</div>' + sections;

        updateSelection();
        refreshIcons();
    }

    function updateSelection() {
        var rows = resultsEl.querySelectorAll('.ss-result-row');
        for (var i = 0; i < rows.length; i++) {
            var idx = parseInt(rows[i].getAttribute('data-idx'), 10);
            if (idx === selectedIdx) {
                rows[i].classList.add('is-selected');
                rows[i].scrollIntoView({ block: 'nearest' });
            } else {
                rows[i].classList.remove('is-selected');
            }
        }
    }

    function refreshIcons() {
        if (typeof window.lucide !== 'undefined' && window.lucide.createIcons) {
            window.lucide.createIcons({ nameAttr: 'data-lucide', node: resultsEl });
        }
    }

    // ---- navigation: jump to setting -------------------------------------
    function jumpToSetting(tab, settingId) {
        var currentTab = activeTab;
        var hash = '#setting-' + settingId;
        if (tab === currentTab) {
            // Same tab — just flash + clear search
            input.value = '';
            root.classList.remove('has-query');
            input.setAttribute('aria-expanded', 'false');
            history.replaceState(null, '', location.pathname + hash);
            flashTarget(settingId);
        } else {
            // Cross-tab — full nav, target tab's onload handler does the flash
            window.location.href = '/settings/' + tab + hash;
        }
    }

    // Track the currently pinned target so a new jump clears the old marker.
    var _pinnedTarget = null;
    var _pinnedCard   = null;
    var _pinnedDismissHandler = null;

    function flashTarget(settingId) {
        // Some settings live inside HTMX-loaded sub-partials (e.g. user rows,
        // path-mapping rows). Poll for up to 3s so cross-tab navigation and
        // slow partials don't drop the highlight on the floor.
        // :not(.ss-result-row) excludes the search popup rows, which also
        // carry data-setting-id for click delegation but live inside the
        // hidden results panel — without this filter the flash would land on
        // an invisible element.
        var attempts = 0;
        var MAX_ATTEMPTS = 30;  // ~3s at 100ms per tick
        var selector = '[data-setting-id="' + cssEscape(settingId) + '"]:not(.ss-result-row)';

        function tryFlash() {
            var target = document.querySelector(selector);
            if (!target) {
                attempts++;
                if (attempts < MAX_ATTEMPTS) setTimeout(tryFlash, 100);
                return;
            }
            applyFlash(target);
        }

        requestAnimationFrame(tryFlash);
    }

    function applyFlash(target) {
        var card = target.closest('.card');

        // Clear any prior pinned highlight before flashing the new target
        clearPinned(/*instant=*/true);

        // Phase 1: bright pulse (matches @keyframes ss-flash-pulse duration)
        target.classList.add('ss-flash-highlight');
        if (card) card.classList.add('ss-flash-highlight-card');

        target.scrollIntoView({ behavior: 'smooth', block: 'center' });

        // Auto-focus the first interactive child after the smooth scroll settles
        var focusable = target.querySelector('input, select, textarea');
        if (focusable) setTimeout(function() {
            try { focusable.focus({ preventScroll: true }); } catch (e) {}
        }, 450);

        // Phase 2: after the pulse finishes, settle into a persistent pin.
        // The pin clears on the next click or keystroke (installed after a
        // short tick so the click that triggered the jump doesn't dismiss it).
        setTimeout(function() {
            target.classList.remove('ss-flash-highlight');
            if (card) card.classList.remove('ss-flash-highlight-card');

            target.classList.add('ss-flash-pinned');
            if (card) card.classList.add('ss-flash-pinned-card');

            _pinnedTarget = target;
            _pinnedCard   = card;

            setTimeout(installPinnedDismiss, 60);
        }, 1000);
    }

    function installPinnedDismiss() {
        if (_pinnedDismissHandler) return;  // already installed
        _pinnedDismissHandler = function(e) {
            // Don't dismiss if there's no pin to clear
            if (!_pinnedTarget) {
                teardownPinnedDismiss();
                return;
            }
            clearPinned(false);
        };
        document.addEventListener('click', _pinnedDismissHandler, true);
        document.addEventListener('keydown', _pinnedDismissHandler, true);
    }

    function teardownPinnedDismiss() {
        if (!_pinnedDismissHandler) return;
        document.removeEventListener('click', _pinnedDismissHandler, true);
        document.removeEventListener('keydown', _pinnedDismissHandler, true);
        _pinnedDismissHandler = null;
    }

    function clearPinned(instant) {
        var t = _pinnedTarget, c = _pinnedCard;
        _pinnedTarget = null;
        _pinnedCard = null;
        teardownPinnedDismiss();
        if (!t) return;
        if (instant) {
            t.classList.remove('ss-flash-pinned', 'is-dismissing');
            if (c) c.classList.remove('ss-flash-pinned-card', 'is-dismissing');
            return;
        }
        // Animated fade-out via .is-dismissing, then remove
        t.classList.add('is-dismissing');
        if (c) c.classList.add('is-dismissing');
        setTimeout(function() {
            t.classList.remove('ss-flash-pinned', 'is-dismissing');
            if (c) c.classList.remove('ss-flash-pinned-card', 'is-dismissing');
        }, 450);
    }

    function cssEscape(s) {
        // CSS.escape polyfill for safety
        if (typeof window.CSS !== 'undefined' && window.CSS.escape) return window.CSS.escape(s);
        return String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
    }

    // ---- event wiring ----------------------------------------------------
    input.addEventListener('input', function() {
        var q = input.value;
        root.classList.toggle('has-query', q.length > 0);
        input.setAttribute('aria-expanded', q.length > 0 ? 'true' : 'false');
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(function() {
            if (q.trim()) renderResults(q);
            else renderEmptyState();
        }, 80);
    });

    input.addEventListener('focus', function() {
        if (!input.value.trim()) {
            root.classList.add('has-query');
            input.setAttribute('aria-expanded', 'true');
            renderEmptyState();
        }
    });

    document.addEventListener('click', function(e) {
        if (!root.contains(e.target) && !input.value.trim()) {
            root.classList.remove('has-query');
            input.setAttribute('aria-expanded', 'false');
        }
    });

    clearBtn.addEventListener('click', function() {
        input.value = '';
        input.focus();
        renderEmptyState();
    });

    resultsEl.addEventListener('click', function(e) {
        var row = e.target.closest('.ss-result-row');
        if (row) {
            jumpToSetting(row.getAttribute('data-tab'), row.getAttribute('data-setting-id'));
            return;
        }
        var chip = e.target.closest('.ss-chip[data-suggest]');
        if (chip) {
            input.value = chip.getAttribute('data-suggest');
            input.dispatchEvent(new Event('input'));
            input.focus();
        }
    });

    resultsEl.addEventListener('mousemove', function(e) {
        var row = e.target.closest('.ss-result-row');
        if (!row) return;
        var idx = parseInt(row.getAttribute('data-idx'), 10);
        if (idx !== selectedIdx) {
            selectedIdx = idx;
            updateSelection();
        }
    });

    input.addEventListener('keydown', function(e) {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (currentResults.length) {
                selectedIdx = (selectedIdx + 1) % currentResults.length;
                updateSelection();
            }
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (currentResults.length) {
                selectedIdx = (selectedIdx - 1 + currentResults.length) % currentResults.length;
                updateSelection();
            }
        } else if (e.key === 'Enter') {
            e.preventDefault();
            var target = currentResults[selectedIdx];
            if (target) jumpToSetting(target.tab, target.setting_id);
        } else if (e.key === 'Escape') {
            if (input.value) {
                input.value = '';
                renderEmptyState();
            } else {
                input.blur();
                root.classList.remove('has-query');
                input.setAttribute('aria-expanded', 'false');
            }
        }
    });

    // Global Cmd/Ctrl+K focus
    document.addEventListener('keydown', function(e) {
        var k = e.key && e.key.toLowerCase();
        if ((e.metaKey || e.ctrlKey) && k === 'k') {
            // Don't intercept if the user is typing in a different input
            // unless they explicitly hit the global shortcut
            e.preventDefault();
            input.focus();
            input.select();
        }
    });

    // Pre-render the empty state (hidden) so first focus is instant
    renderEmptyState();
    root.classList.remove('has-query');
    input.setAttribute('aria-expanded', 'false');

    // ---- on-load: if URL has #setting-<id>, flash it ---------------------
    function handleInitialHash() {
        var hash = location.hash;
        if (hash && hash.indexOf('#setting-') === 0) {
            var id = hash.slice('#setting-'.length);
            // flashTarget() polls for the element for up to 3s, so HTMX-loaded
            // partials (user list, path-mapping rows) have time to render.
            flashTarget(id);
        }
    }
    handleInitialHash();

    // If hash changes while on the page (e.g., back/forward), re-flash
    window.addEventListener('hashchange', handleInitialHash);
})();

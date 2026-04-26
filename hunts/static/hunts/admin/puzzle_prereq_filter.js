// Dynamic prerequisites picker for the Puzzle admin form.
//
// When the puzzlehunt select changes, fetches the puzzles in that hunt
// from the admin AJAX endpoint and repopulates the prerequisites
// filter_horizontal widget. On hunt change we also clear any previously
// chosen prerequisites because they belong to the old hunt.

(function () {
    'use strict';

    function getCurrentPuzzleId() {
        const m = window.location.pathname.match(/\/puzzle\/(\d+)\/change\//);
        return m ? m[1] : null;
    }

    function rebuildSelectFilter(selectEl) {
        // Strip the existing two-box UI and reset classes so SelectFilter.init
        // can run again from a clean state.
        const wrapper = selectEl.parentElement;
        const selectorDiv = wrapper.querySelector('.selector');
        if (selectorDiv) selectorDiv.remove();
        selectEl.classList.remove('selectfilter');
        selectEl.classList.remove('selectfilterstacked');
        selectEl.style.display = '';

        if (typeof SelectFilter !== 'undefined') {
            // Third arg is is_stacked (0 = horizontal).
            SelectFilter.init('id_prerequisites', 'Prerequisites', 0);
        }
    }

    function setOptions(selectEl, puzzles) {
        selectEl.innerHTML = '';
        for (const p of puzzles) {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.label;
            selectEl.appendChild(opt);
        }
    }

    function refresh(huntSelect, prereqSelect) {
        const huntId = huntSelect.value;
        if (!huntId) {
            // No hunt picked → no prerequisites can be selected.
            setOptions(prereqSelect, []);
            rebuildSelectFilter(prereqSelect);
            return;
        }
        const exclude = getCurrentPuzzleId();
        const url =
            `/admin/hunts/puzzle/_puzzles_in_hunt/${huntId}/` +
            (exclude ? `?exclude=${exclude}` : '');
        fetch(url, { credentials: 'same-origin' })
            .then((r) => r.json())
            .then((data) => {
                setOptions(prereqSelect, data.puzzles);
                rebuildSelectFilter(prereqSelect);
            });
    }

    document.addEventListener('DOMContentLoaded', function () {
        const huntSelect = document.getElementById('id_puzzlehunt');
        const prereqSelect = document.getElementById('id_prerequisites');
        if (!huntSelect || !prereqSelect) return;
        huntSelect.addEventListener('change', function () {
            refresh(huntSelect, prereqSelect);
        });
    });
})();

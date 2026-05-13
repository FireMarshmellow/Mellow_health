'use strict';

// ── Timer ─────────────────────────────────────────────────────────────────────

const TrackerTimer = (() => {
  const KEY_START   = 'tracker_start_ts';
  const KEY_ELAPSED = 'tracker_paused_elapsed';
  const KEY_ID      = 'tracker_plan_workout_id';

  let intervalId  = null;
  let elapsedSec  = 0;
  let running     = false;
  let startWallTs = null;

  function _render() {
    const m = String(Math.floor(elapsedSec / 60)).padStart(2, '0');
    const s = String(elapsedSec % 60).padStart(2, '0');
    document.getElementById('timer-display').textContent = `${m}:${s}`;
    const icon = document.getElementById('timer-icon');
    if (icon) icon.className = running ? 'bi bi-pause-fill' : 'bi bi-play-fill';
  }

  function _startInterval() {
    running     = true;
    startWallTs = Date.now();
    localStorage.setItem(KEY_START, startWallTs);
    intervalId  = setInterval(() => {
      const base = parseInt(localStorage.getItem(KEY_ELAPSED) || '0', 10);
      elapsedSec = base + Math.floor((Date.now() - startWallTs) / 1000);
      _render();
    }, 1000);
    _render();
  }

  function _pause() {
    running = false;
    clearInterval(intervalId);
    intervalId = null;
    localStorage.setItem(KEY_ELAPSED, elapsedSec);
    localStorage.removeItem(KEY_START);
    _render();
  }

  function init(planWorkoutId) {
    const storedId = localStorage.getItem(KEY_ID);
    if (storedId && parseInt(storedId, 10) === planWorkoutId) {
      const startTs      = localStorage.getItem(KEY_START);
      const pausedElapsed = parseInt(localStorage.getItem(KEY_ELAPSED) || '0', 10);
      if (startTs) {
        elapsedSec = pausedElapsed + Math.floor((Date.now() - parseInt(startTs, 10)) / 1000);
        localStorage.setItem(KEY_ELAPSED, elapsedSec);
        _startInterval();
      } else {
        elapsedSec = pausedElapsed;
        _render();
      }
    } else {
      localStorage.setItem(KEY_ID, planWorkoutId);
      localStorage.setItem(KEY_ELAPSED, '0');
      _startInterval();
    }
  }

  function toggle() {
    if (running) _pause(); else _startInterval();
  }

  function getElapsed() { return elapsedSec; }

  function clear() {
    _pause();
    [KEY_START, KEY_ELAPSED, KEY_ID].forEach(k => localStorage.removeItem(k));
  }

  return { init, toggle, getElapsed, clear };
})();


// ── Sets ──────────────────────────────────────────────────────────────────────

const TrackerSets = (() => {
  function _renumber(container) {
    container.querySelectorAll('.set-row').forEach((row, i) => {
      row.dataset.setIdx = i;
      row.querySelector('.set-number').textContent = i + 1;
    });
  }

  function deleteSet(btn) {
    const row       = btn.closest('.set-row');
    const container = row.closest('.set-rows');
    // Keep at least one set row
    if (container.querySelectorAll('.set-row').length <= 1) return;
    row.remove();
    _renumber(container);
  }

  function addSet(exIdx) {
    const container = document.getElementById(`set-rows-${exIdx}`);
    const rows      = container.querySelectorAll('.set-row');
    const last      = rows[rows.length - 1];
    const clone     = last.cloneNode(true);
    // Reset completion state
    clone.classList.remove('set-completed');
    clone.querySelector('.set-complete-btn').classList.remove('active');
    container.appendChild(clone);
    _renumber(container);
    clone.querySelector('.set-weight').focus();
  }

  function toggleComplete(btn) {
    const row = btn.closest('.set-row');
    row.classList.toggle('set-completed');
    btn.classList.toggle('active');
  }

  return { deleteSet, addSet, toggleComplete };
})();


// ── Submit ────────────────────────────────────────────────────────────────────

const TrackerSubmit = (() => {
  function _collect() {
    const exercises = [];
    document.querySelectorAll('.exercise-card').forEach(card => {
      const name = card.querySelector('.ex-name').textContent.trim();
      const sets = [];
      card.querySelectorAll('.set-row').forEach(row => {
        const wVal = parseFloat(row.querySelector('.set-weight').value);
        const rVal = parseInt(row.querySelector('.set-reps').value, 10);
        if (!isNaN(rVal) && rVal > 0) {
          sets.push({ weight_kg: isNaN(wVal) ? null : wVal, reps: rVal });
        }
      });
      if (sets.length > 0) exercises.push({ name, sets });
    });

    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    return {
      plan_workout_id:  PLAN_WORKOUT_ID,
      workout_date:     `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}`,
      workout_time:     `${pad(now.getHours())}:${pad(now.getMinutes())}`,
      duration_seconds: TrackerTimer.getElapsed(),
      exercises,
    };
  }

  function _showError(msg) {
    const existing = document.getElementById('tracker-error');
    if (existing) existing.remove();
    const el = document.createElement('div');
    el.id        = 'tracker-error';
    el.className = 'alert alert-danger alert-dismissible fade show mb-3';
    el.innerHTML = `${msg}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
    document.getElementById('tracker-exercises').prepend(el);
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function finish() {
    const btn = document.getElementById('finish-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving…';

    const payload = _collect();
    if (payload.exercises.length === 0) {
      _showError('No sets recorded. Add at least one set before finishing.');
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-flag-fill me-2"></i>Finish Workout';
      return;
    }

    try {
      const resp = await fetch('/strength/plans/api/submit-workout', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Server error' }));
        throw new Error(err.detail || 'Server error');
      }

      const data = await resp.json();
      TrackerTimer.clear();
      window.location.href = data.redirect;

    } catch (e) {
      _showError(e.message);
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-flag-fill me-2"></i>Finish Workout';
    }
  }

  return { finish };
})();


// ── Add Exercise ──────────────────────────────────────────────────────────────

const TrackerExercises = (() => {
  let _exercises = null;
  let _modal = null;

  function _mgClass(mg) {
    return (mg || 'other').toLowerCase().replace(/\s+/g, '-');
  }

  function _createCard(exIdx, name, muscleGroup) {
    const mg = muscleGroup || '';
    const badge = mg
      ? `<span class="badge muscle-badge muscle-${_mgClass(mg)}">${mg}</span>`
      : '';
    const card = document.createElement('div');
    card.className = 'exercise-card';
    card.dataset.exIdx = exIdx;
    card.innerHTML = `
      <div class="exercise-card-header">
        <span class="ex-name">${name}</span>
        ${badge}
        <button class="btn btn-sm set-delete-btn ms-auto"
                onclick="TrackerExercises.deleteCard(this)" title="Remove exercise">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>
      <div class="set-rows" id="set-rows-${exIdx}">
        <div class="set-row" data-set-idx="0">
          <span class="set-number">1</span>
          <div class="set-inputs">
            <input type="number" class="form-control set-weight" placeholder="BW" step="0.5" min="0">
            <span class="set-sep">kg ×</span>
            <input type="number" class="form-control set-reps" min="1" step="1">
            <span class="set-sep-small">reps</span>
          </div>
          <div class="set-actions">
            <button class="btn btn-sm set-complete-btn"
                    onclick="TrackerSets.toggleComplete(this)" title="Mark complete">
              <i class="bi bi-check-lg"></i>
            </button>
            <button class="btn btn-sm set-delete-btn"
                    onclick="TrackerSets.deleteSet(this)" title="Delete set">
              <i class="bi bi-trash3"></i>
            </button>
          </div>
        </div>
      </div>
      <button class="btn btn-sm add-set-btn" onclick="TrackerSets.addSet(${exIdx})">
        <i class="bi bi-plus"></i> Add Set
      </button>`;
    return card;
  }

  function _renderList(filter) {
    const listEl = document.getElementById('exercise-picker-list');
    listEl.innerHTML = '';
    const term = (filter || '').toLowerCase();
    const filtered = term
      ? _exercises.filter(e => e.name.toLowerCase().includes(term))
      : _exercises;

    if (filtered.length === 0) {
      listEl.innerHTML = '<p class="text-muted small p-2 mb-0">No exercises found.</p>';
      return;
    }

    filtered.forEach(ex => {
      const mg = ex.muscle_group || '';
      const badge = mg
        ? `<span class="badge muscle-badge muscle-${_mgClass(mg)}">${mg}</span>`
        : '';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center py-2 px-3';
      btn.style.cssText = 'background:transparent;color:#fff;border-color:rgba(255,255,255,0.08)';
      btn.innerHTML = `<span style="font-size:0.9rem">${ex.name}</span>${badge}`;
      btn.onclick = () => _pick(ex.name, ex.muscle_group);
      listEl.appendChild(btn);
    });
  }

  function _nextExIdx() {
    let max = -1;
    document.querySelectorAll('.exercise-card').forEach(c => {
      const idx = parseInt(c.dataset.exIdx, 10);
      if (!isNaN(idx) && idx > max) max = idx;
    });
    return max + 1;
  }

  function _pick(name, muscleGroup) {
    const card = _createCard(_nextExIdx(), name, muscleGroup);
    document.getElementById('tracker-exercises').appendChild(card);
    if (_modal) _modal.hide();
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    card.querySelector('.set-reps').focus();
  }

  function deleteCard(btn) {
    btn.closest('.exercise-card').remove();
  }

  async function openPicker() {
    if (!_modal) {
      _modal = new bootstrap.Modal(document.getElementById('exercisePickerModal'));
    }
    if (!_exercises) {
      try {
        const resp = await fetch('/strength/api/exercises');
        _exercises = await resp.json();
      } catch {
        _exercises = [];
      }
    }
    const searchEl = document.getElementById('exercise-search');
    searchEl.value = '';
    _renderList('');
    _modal.show();
  }

  function init() {
    const searchEl = document.getElementById('exercise-search');
    if (searchEl) searchEl.addEventListener('input', e => _renderList(e.target.value));
    // Auto-focus search every time the modal opens
    const modalEl = document.getElementById('exercisePickerModal');
    if (modalEl) {
      modalEl.addEventListener('shown.bs.modal', () =>
        document.getElementById('exercise-search').focus()
      );
    }
  }

  return { openPicker, deleteCard, init };
})();


// ── Bootstrap ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  TrackerTimer.init(PLAN_WORKOUT_ID);
  TrackerExercises.init();
});

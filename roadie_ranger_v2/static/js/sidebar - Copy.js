// sidebar.js – Session history sidebar

export class Sidebar {
  /**
   * @param {HTMLElement} el          – the sidebar root element
   * @param {(sessionId: string) => void} onLoad  – called when user clicks a past session
   * @param {() => void} onNewSession             – called when "New Session" is clicked
   */
  constructor(el, onLoad, onNewSession) {
    this._el           = el;
    this._onLoad       = onLoad;
    this._onNewSession = onNewSession;
    this._activeId     = null;
    this._refreshTimer = null;   // debounce handle

    this._listEl = el.querySelector('#session-list');
    this._newBtn = el.querySelector('#btn-new-session');
    this._newBtn.addEventListener('click', () => this._onNewSession());

    this.refresh();
  }

  /** Mark a session id as the live/active one (highlights it differently). */
  setActiveSession(id) {
    this._activeId = id;
    this._renderActive();
  }

  /**
   * Debounced refresh — collapses rapid consecutive calls into one fetch
   * so each agent/user transcript turn doesn't fire a separate API request.
   */
  refresh(delay = 800) {
    clearTimeout(this._refreshTimer);
    this._refreshTimer = setTimeout(() => this._doRefresh(), delay);
  }

  async _doRefresh() {
    try {
      const res  = await fetch('/api/sessions');
      const list = await res.json();
      this._render(list);
    } catch (e) {
      console.warn('Could not load sessions:', e);
    }
  }

  // ── private ────────────────────────────────────────────────────────────────

  _render(sessions) {
    this._listEl.innerHTML = '';

    if (!sessions.length) {
      this._listEl.innerHTML = '<p class="sidebar-empty">No previous sessions</p>';
      return;
    }

    for (const s of sessions) {
      const card = document.createElement('button');
      card.className = 'session-card';
      card.dataset.id = s.session_id;
      if (s.session_id === this._activeId) card.classList.add('active');

      const date = s.started_at
        ? new Date(s.started_at).toLocaleString(undefined, {
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
          })
        : '';

      card.innerHTML = `
        <div class="sc-meta">
          <span class="sc-date">${date}</span>
          <span class="sc-turns">${s.turn_count} turn${s.turn_count !== 1 ? 's' : ''}</span>
        </div>
        <p class="sc-preview">${escHtml(s.preview)}</p>
      `;

      if (s.session_id !== this._activeId) {
        card.addEventListener('click', () => this._onLoad(s.session_id));
      }

      this._listEl.appendChild(card);
    }
  }

  _renderActive() {
    this._listEl.querySelectorAll('.session-card').forEach(card => {
      card.classList.toggle('active', card.dataset.id === this._activeId);
    });
  }
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
// sidebar.js – Session history sidebar

export class Sidebar {
  constructor(el, onLoad, onNewSession) {
    this._el           = el;
    this._onLoad       = onLoad;
    this._onNewSession = onNewSession;
    this._activeId     = null;
    this._refreshTimer = null;

    this._listEl = el.querySelector('#session-list');
    this._newBtn = el.querySelector('#btn-new-session');
    this._newBtn.addEventListener('click', () => this._onNewSession());

    this.refresh();
  }

  setActiveSession(id) {
    this._activeId = id;
    this._renderActive();
  }

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

      // Determine label & colors based on session type
      const isText = s.source === 'browser_text';
      const typeLabel = isText ? 'TEXT' : 'VOICE';
      const typeBg = isText ? '#e0e7ff' : '#dcfce7';
      const typeFg = isText ? '#3730a3' : '#166534';

      card.innerHTML = `
        <div class="sc-meta">
          <span class="sc-date">${date}</span>
          <div style="display: flex; gap: 5px;">
            <span class="sc-turns" style="color: ${typeFg}; background: ${typeBg};">${typeLabel}</span>
            <span class="sc-turns">${s.turn_count} turn${s.turn_count !== 1 ? 's' : ''}</span>
          </div>
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
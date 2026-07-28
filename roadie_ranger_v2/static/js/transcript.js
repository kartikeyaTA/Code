// transcript.js
// Manages the scrolling conversation transcript with live streaming text.

export class TranscriptView {
  constructor(container) {
    this._container = container;
    this._cleared   = false;

    // Active streaming bubbles keyed by role
    this._streaming = {
      user:  null,   // { wrap, bubble, textNode, cursor } while streaming
      agent: null,
    };
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  /**
   * Append a streaming delta to the in-progress bubble for `role`.
   * Creates the bubble on first call for that role.
   */
  appendDelta(role, delta) {
    if (!delta) return;
    this._ensureCleared();

    let state = this._streaming[role];
    if (!state) {
      state = this._createBubble(role);
      this._streaming[role] = state;
    }

    state.textNode.textContent += delta;
    this._scrollToBottom();
  }

  /**
   * Finalize a streaming bubble with the clean full text and remove the cursor.
   * If no streaming bubble exists yet, creates one directly.
   */
  finalize(role, text) {
    if (!text?.trim()) {
      this._discard(role);
      return;
    }
    this._ensureCleared();

    const state = this._streaming[role];
    if (state) {
      state.textNode.textContent = text;
      state.cursor.remove();
      this._streaming[role] = null;
    } else {
      this._appendComplete(role, text);
    }
    this._scrollToBottom();
  }

  /**
   * Cancel any in-progress streaming bubble for `role` (e.g. on barge-in).
   * Removes the partial bubble from the DOM entirely.
   */
  cancelStreaming(role) {
    this._discard(role);
  }

  // ── Internal helpers ────────────────────────────────────────────────────────

  _ensureCleared() {
    if (!this._cleared) {
      document.getElementById('empty-state')?.remove();
      this._cleared = true;
    }
  }

  _createBubble(role) {
    const label = role === 'user' ? 'You' : 'Agent';

    const wrap = document.createElement('div');
    wrap.className = `msg ${role}`;

    const labelEl = document.createElement('span');
    labelEl.className = 'msg-label';
    labelEl.textContent = label;

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';

    const textNode = document.createTextNode('');
    bubble.appendChild(textNode);

    const cursor = document.createElement('span');
    cursor.className = 'stream-cursor';
    bubble.appendChild(cursor);

    wrap.appendChild(labelEl);
    wrap.appendChild(bubble);
    this._container.appendChild(wrap);
    this._scrollToBottom();

    return { wrap, bubble, textNode, cursor };
  }

  _appendComplete(role, text) {
    const label = role === 'user' ? 'You' : role === 'agent' ? 'Agent' : 'Error';
    const wrap  = document.createElement('div');
    wrap.className = `msg ${role}`;
    wrap.innerHTML = `
      <span class="msg-label">${label}</span>
      <div class="msg-bubble">${escapeHtml(text)}</div>
    `;
    this._container.appendChild(wrap);
  }

  _discard(role) {
    const state = this._streaming[role];
    if (state) {
      state.wrap.remove();
      this._streaming[role] = null;
    }
  }

  _scrollToBottom() {
    requestAnimationFrame(() => {
      this._container.scrollTop = this._container.scrollHeight;
    });
  }
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
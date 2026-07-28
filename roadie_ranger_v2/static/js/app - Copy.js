// app.js – main entry point
import { AudioCapture } from './audio-capture.js';
import { AudioPlayback } from './audio-playback.js';
import { Sidebar } from './sidebar.js';
import { TranscriptView } from './transcript.js';
import { WaveformViz } from './waveform.js';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const btnConnect    = document.getElementById('btn-connect');
const btnMic        = document.getElementById('btn-mic');
const btnDisconnect = document.getElementById('btn-disconnect');
const statusPill    = document.getElementById('status-pill');
const statusText    = document.getElementById('status-text');
const hintEl        = document.getElementById('hint');
const transcriptEl  = document.getElementById('transcript');
const sidebarEl     = document.getElementById('sidebar');
const headerUser    = document.getElementById('header-user');
const headerUsername = document.getElementById('header-username');
const btnLogout     = document.getElementById('btn-logout');

// ── App state ─────────────────────────────────────────────────────────────────
let ws         = null;
let capture    = null;
let playback   = null;
let waveform   = null;
let transcript = null;
let sidebar    = null;
let micActive  = false;
let currentSessionId = null;
let currentUser = null;

// ── Auth check on load ────────────────────────────────────────────────────────
async function initAuth() {
  try {
    const res  = await fetch('/api/me');
    const user = await res.json();
    if (!user.is_authenticated) {
      window.location.href = '/login';
      return false;
    }
    currentUser = user;
    headerUsername.textContent = user.user_name;
    headerUser.style.display = 'flex';
    return true;
  } catch {
    window.location.href = '/login';
    return false;
  }
}

// ── Logout ────────────────────────────────────────────────────────────────────
btnLogout.addEventListener('click', async () => {
  // Clear local JWT cookie first, then always hit Easy Auth logout so any
  // residual Microsoft session is also terminated. /.auth/logout is a no-op
  // (just redirects) when no Microsoft session exists, so this is safe for
  // local-account users too.
  await fetch('/auth/logout', { method: 'POST' }).catch(() => {});
  window.location.href = '/.auth/logout?post_logout_redirect_uri=/login';
});

// ── Init sidebar ──────────────────────────────────────────────────────────────
(async () => {
  const authed = await initAuth();
  if (!authed) return;

  sidebar = new Sidebar(
    sidebarEl,
    loadPastSession,   // onLoad
    handleNewSession,  // onNewSession
  );
})();

// ── Status helpers ────────────────────────────────────────────────────────────
function setStatus(state, label) {
  statusPill.className = `status-pill ${state}`;
  statusText.textContent = label;
}
function setHint(text) { hintEl.textContent = text; }

// ── Connect ───────────────────────────────────────────────────────────────────
btnConnect.addEventListener('click', () => startSession());

async function startSession() {
  btnConnect.disabled = true;
  setStatus('processing', 'Connecting…');
  setHint('Establishing session with Azure VoiceLive…');

  // Reset transcript area
  transcriptEl.innerHTML = `
    <div class="empty-state" id="empty-state">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
        <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/>
      </svg>
      <p>Connect and start speaking</p>
    </div>`;

  transcript = new TranscriptView(transcriptEl);
  waveform   = new WaveformViz(document.getElementById('waveform'));
  playback   = new AudioPlayback();
  await playback.init();

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  let wsUrl = `${proto}://${location.host}/ws/voice`;
  try {
    const nonceRes = await fetch('/api/ws-nonce');
    if (nonceRes.ok) {
      const { nonce } = await nonceRes.json();
      wsUrl += `?nonce=${encodeURIComponent(nonce)}`;
    }
  } catch (_) { /* fall back to cookie/Easy Auth */ }
  ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';

  ws.addEventListener('open', () => {
    btnMic.disabled        = false;
    btnDisconnect.disabled = false;
  });

  ws.addEventListener('message', async (ev) => {
    handleServerMessage(JSON.parse(ev.data));
  });

  ws.addEventListener('close', () => {
    teardown(/* clearTranscript */ false);
    setStatus('', 'Disconnected');
    setHint('Session ended — click Connect or New Session to start again');
    // Wait for server to finish uploading the session blob before refreshing
    setTimeout(() => sidebar?.refresh(), 3000);
  });

  ws.addEventListener('error', () => {
    setStatus('error', 'Error');
    setHint('WebSocket error — check console');
    teardown(false);
  });
}

// ── New Session ───────────────────────────────────────────────────────────────
function handleNewSession() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    // Wait for the close handshake to finish before opening a new session,
    // so the old and new WebSocket connections don't overlap.
    ws.addEventListener('close', () => startSession(), { once: true });
    ws.send(JSON.stringify({ type: 'stop' }));
    teardown(true);
  } else {
    teardown(true);
    startSession();
  }
}

// ── Disconnect ────────────────────────────────────────────────────────────────
btnDisconnect.addEventListener('click', () => {
  if (ws) ws.send(JSON.stringify({ type: 'stop' }));
  teardown(false);
  sidebar?.refresh();
});

// ── Mic toggle ────────────────────────────────────────────────────────────────
btnMic.addEventListener('click', async () => {
  micActive ? stopMic() : await startMic();
});

async function startMic() {
  if (capture) return;
  try {
    capture = new AudioCapture(
      (pcm16Base64) => {
        if (ws?.readyState === WebSocket.OPEN)
          ws.send(JSON.stringify({ type: 'audio_chunk', data: pcm16Base64 }));
      },
      (level) => waveform?.setLevel(level),
    );
    await capture.start();
    micActive = true;
    btnMic.classList.add('active');
    btnMic.textContent = '🔴';
    setHint('Microphone active — speak now');
    setStatus('listening', 'Listening');
  } catch (err) {
    console.error('Mic error:', err);
    setStatus('error', 'Mic error');
    setHint('Microphone access denied or unavailable');
    capture = null;
  }
}

function stopMic() {
  capture?.stop();
  capture   = null;
  micActive = false;
  btnMic.classList.remove('active');
  btnMic.textContent = '🎤';
  setHint('Microphone stopped');
  waveform?.setLevel(0);
}

// ── Server message handler ────────────────────────────────────────────────────
function handleServerMessage(msg) {
  switch (msg.type) {
    case 'session_id':
      currentSessionId = msg.id;
      sidebar?.setActiveSession(msg.id);
      sidebar?.refresh();       // add the new (empty) session card immediately
      break;

    case 'audio_chunk':
      playback?.enqueue(msg.data);
      break;

    case 'user_text_delta':
      transcript?.appendDelta('user', msg.delta);
      break;

    case 'agent_text_delta':
      transcript?.appendDelta('agent', msg.delta);
      break;

    case 'user_text':
      transcript?.finalize('user', msg.text);
      sidebar?.refresh();       // update turn count in sidebar
      break;

    case 'agent_text':
      transcript?.finalize('agent', msg.text);
      sidebar?.refresh();
      break;

    case 'status':
      handleStatus(msg.text);
      break;

    case 'error':
      transcript?.finalize('error', msg.text);
      setStatus('error', 'Error');
      break;
  }
}

function handleStatus(s) {
  switch (s) {
    case 'connected':
      setStatus('connected', 'Ready');
      setHint('Agent ready — click 🎤 to speak');
      break;
    case 'barge_in':
      // Agent was interrupted — stop audio and discard the partial transcript bubble.
      playback?.flush();
      transcript?.cancelStreaming('agent');
      setStatus('listening', 'Listening');
      setHint('Barge-in detected — listening to you…');
      break;
    case 'listening':
      setStatus('listening', 'Listening');
      setHint('Listening…');
      break;
    case 'processing':
      setStatus('processing', 'Processing');
      setHint('Agent is thinking…');
      break;
    case 'ready':
      setStatus('ready', 'Ready');
      setHint('Click 🎤 to speak again');
      break;
  }
}

// ── Load a past session (read-only) ──────────────────────────────────────────
async function loadPastSession(sessionId) {
  // Don't interrupt a live session
  if (ws && ws.readyState === WebSocket.OPEN) return;

  try {
    const res  = await fetch(`/api/sessions/${sessionId}`);
    const data = await res.json();

    // Reset transcript
    transcriptEl.innerHTML = '';
    transcript = new TranscriptView(transcriptEl);

    for (const turn of data.turns || []) {
      transcript.finalize(turn.role, turn.text);
    }

    setStatus('', 'Viewing past session');
    setHint('This is a past session — click Connect or New Session to start talking');
    sidebar?.setActiveSession(sessionId);
  } catch (e) {
    console.error('Failed to load session:', e);
  }
}

// ── Teardown ──────────────────────────────────────────────────────────────────
function teardown(clearTranscript) {
  stopMic();
  playback?.close();   playback = null;
  waveform?.stop();    waveform = null;

  if (ws && ws.readyState !== WebSocket.CLOSED) ws.close();
  ws = null;

  currentSessionId       = null;
  btnConnect.disabled    = false;
  btnMic.disabled        = true;
  btnDisconnect.disabled = true;

  if (clearTranscript) {
    transcriptEl.innerHTML = `
      <div class="empty-state" id="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
          <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/>
        </svg>
        <p>Connect and start speaking</p>
      </div>`;
    transcript = null;
  }
}
// app.js – main entry point
import { AudioCapture } from './audio-capture.js';
import { AudioPlayback } from './audio-playback.js';
import { Sidebar } from './sidebar.js';
import { TranscriptView } from './transcript.js';
import { WaveformViz } from './waveform.js';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const tabVoice      = document.getElementById('tab-voice');
const tabText       = document.getElementById('tab-text');
const voiceControls = document.getElementById('voice-controls');
const textControls  = document.getElementById('text-controls');

const btnConnect    = document.getElementById('btn-connect');
const btnMic        = document.getElementById('btn-mic');
const btnDisconnect = document.getElementById('btn-disconnect');

const btnConnectText    = document.getElementById('btn-connect-text');
const btnDisconnectText = document.getElementById('btn-disconnect-text');
const chatInput         = document.getElementById('chat-input');
const btnTextSend       = document.getElementById('btn-text-send');

const statusPill    = document.getElementById('status-pill');
const statusText    = document.getElementById('status-text');
const hintEl        = document.getElementById('hint');
const transcriptEl  = document.getElementById('transcript');
const sidebarEl     = document.getElementById('sidebar');
const headerUser    = document.getElementById('header-user');
const headerUsername = document.getElementById('header-username');
const btnLogout     = document.getElementById('btn-logout');

let ws         = null;
let capture    = null;
let playback   = null;
let waveform   = null;
let transcript = null;
let sidebar    = null;
let micActive  = false;
let currentSessionId = null;
let currentUser = null;
let currentMode = 'voice';

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

btnLogout.addEventListener('click', async () => {
  await fetch('/auth/logout', { method: 'POST' }).catch(() => {});
  window.location.href = '/.auth/logout?post_logout_redirect_uri=/login';
});

(async () => {
  const authed = await initAuth();
  if (!authed) return;

  sidebar = new Sidebar(sidebarEl, loadPastSession, handleNewSession);
})();

// ── Tab Switching ─────────────────────────────────────────────────────────────
tabVoice.addEventListener('click', () => setMode('voice'));
tabText.addEventListener('click', () => setMode('text'));

function setMode(mode) {
  if (currentMode === mode) return;

  // ALERTS USER IF SESSION IS ACTIVE
  if (ws && ws.readyState !== WebSocket.CLOSED) {
    if (!confirm("You have an active session. Are you sure you want to disconnect and switch mode?")) {
      return;
    }
    ws.send(JSON.stringify({ type: 'stop' }));
    teardown(true);
  }

  currentMode = mode;

  if (mode === 'voice') {
    tabVoice.style.background = 'var(--accent)';
    tabVoice.style.color = '#fff';
    tabText.style.background = 'transparent';
    tabText.style.color = 'var(--muted)';

    voiceControls.style.display = 'flex';
    textControls.style.display = 'none';
  } else {
    tabText.style.background = 'var(--accent)';
    tabText.style.color = '#fff';
    tabVoice.style.background = 'transparent';
    tabVoice.style.color = 'var(--muted)';

    voiceControls.style.display = 'none';
    textControls.style.display = 'flex';
  }

  // Note: We deliberately do NOT startSession() here anymore.
}

function setStatus(state, label) {
  statusPill.className = `status-pill ${state}`;
  statusText.textContent = label;
}
function setHint(text) { hintEl.textContent = text; }

// ── Connect ───────────────────────────────────────────────────────────────────
btnConnect.addEventListener('click', () => startSession());
btnConnectText.addEventListener('click', () => startSession());

async function startSession() {
  if (currentMode === 'voice') btnConnect.disabled = true;
  else btnConnectText.disabled = true;

  setStatus('processing', 'Connecting…');
  setHint('Establishing session with Azure VoiceLive…');

  transcriptEl.innerHTML = `
    <div class="empty-state" id="empty-state">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
        <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/>
      </svg>
      <p>Connect and start ${currentMode === 'voice' ? 'speaking' : 'typing'}</p>
    </div>`;

  transcript = new TranscriptView(transcriptEl);

  if (currentMode === 'voice') {
    waveform   = new WaveformViz(document.getElementById('waveform'));
    playback   = new AudioPlayback();
    await playback.init();
  }

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  let wsPath = currentMode === 'voice' ? '/ws/voice' : '/ws/text';
  let wsUrl = `${proto}://${location.host}${wsPath}`;

  try {
    const nonceRes = await fetch('/api/ws-nonce');
    if (nonceRes.ok) {
      const { nonce } = await nonceRes.json();
      wsUrl += `?nonce=${encodeURIComponent(nonce)}`;
    }
  } catch (_) { }

  ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';

  ws.addEventListener('open', () => {
    if (currentMode === 'voice') {
      btnMic.disabled = false;
      btnDisconnect.disabled = false;
    } else {
      chatInput.disabled = false;
      btnTextSend.disabled = false;
      btnDisconnectText.disabled = false;
      chatInput.focus();
    }
  });

  ws.addEventListener('message', async (ev) => {
    handleServerMessage(JSON.parse(ev.data));
  });

  ws.addEventListener('close', () => {
    teardown(false);
    setStatus('', 'Disconnected');
    setHint('Session ended — click Connect or New Session to start again');
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
    if (!confirm("You have an active session. Are you sure you want to disconnect and clear the interface?")) {
      return;
    }
    ws.addEventListener('close', () => teardown(true), { once: true });
    ws.send(JSON.stringify({ type: 'stop' }));
  } else {
    teardown(true);
  }
}

// ── Disconnect ────────────────────────────────────────────────────────────────
btnDisconnect.addEventListener('click', triggerDisconnect);
btnDisconnectText.addEventListener('click', triggerDisconnect);

function triggerDisconnect() {
  if (ws) ws.send(JSON.stringify({ type: 'stop' }));
  teardown(false);
  sidebar?.refresh();
}

// ── Mic toggle (Voice Mode) ───────────────────────────────────────────────────
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

// ── Text Send (Text Mode) ─────────────────────────────────────────────────────
function sendTextMessage() {
  const text = chatInput.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

  transcript?.finalize('user', text);
  ws.send(JSON.stringify({ type: 'user_text', text: text }));
  chatInput.value = '';

  setStatus('processing', 'Processing');
  setHint('Agent is thinking…');
}

btnTextSend.addEventListener('click', sendTextMessage);
chatInput.addEventListener('keypress', (e) => {
  if (e.key === 'Enter') sendTextMessage();
});

// ── Server message handler ────────────────────────────────────────────────────
function handleServerMessage(msg) {
  switch (msg.type) {
    case 'session_id':
      currentSessionId = msg.id;
      sidebar?.setActiveSession(msg.id);
      sidebar?.refresh();
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
      sidebar?.refresh();
      break;

    case 'agent_text':
      transcript?.finalize('agent', msg.text);
      sidebar?.refresh();

      if(currentMode === 'text'){
          setStatus('connected', 'Ready');
          setHint('Agent ready — type your message below');
      }
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
      setHint(currentMode === 'voice' ? 'Agent ready — click 🎤 to speak' : 'Agent ready — type your message below');
      break;
    case 'barge_in':
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
      setHint(currentMode === 'voice' ? 'Click 🎤 to speak again' : 'Type your message below');
      break;
  }
}

// ── Load a past session (read-only) ──────────────────────────────────────────
async function loadPastSession(sessionId) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    if (!confirm("You have an active session. Are you sure you want to disconnect and open a historical session?")) {
      return;
    }
    ws.send(JSON.stringify({ type: 'stop' }));
    teardown(true);
  }

  try {
    const res  = await fetch(`/api/sessions/${sessionId}`);
    const data = await res.json();

    transcriptEl.innerHTML = '';
    transcript = new TranscriptView(transcriptEl);

    for (const turn of data.turns || []) {
      transcript.finalize(turn.role, turn.text);
    }

    setStatus('', 'Viewing past session');
    setHint('This is a past session — click Connect or New Session to start talking');
    sidebar?.setActiveSession(sessionId);

    // Auto-switch UI state visually based on session format (Doesn't start the session)
    if (data.source === 'browser_text' && currentMode !== 'text') {
        currentMode = 'text';
        tabText.style.background = 'var(--accent)';
        tabText.style.color = '#fff';
        tabVoice.style.background = 'transparent';
        tabVoice.style.color = 'var(--muted)';
        voiceControls.style.display = 'none';
        textControls.style.display = 'flex';
    } else if (data.source === 'browser' && currentMode !== 'voice') {
        currentMode = 'voice';
        tabVoice.style.background = 'var(--accent)';
        tabVoice.style.color = '#fff';
        tabText.style.background = 'transparent';
        tabText.style.color = 'var(--muted)';
        voiceControls.style.display = 'flex';
        textControls.style.display = 'none';
    }
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

  btnConnectText.disabled = false;
  btnDisconnectText.disabled = true;
  chatInput.disabled = true;
  btnTextSend.disabled = true;

  if (clearTranscript) {
    transcriptEl.innerHTML = `
      <div class="empty-state" id="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
          <path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/>
        </svg>
        <p>Connect and start ${currentMode === 'voice' ? 'speaking' : 'typing'}</p>
      </div>`;
    transcript = null;
  }
}
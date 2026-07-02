// Point this at wherever the backend (uvicorn) is running.
const API_BASE = "https://ca-chat-backend-dev.salmonpebble-18e924cc.eastus.azurecontainerapps.io";
const sessionListEl = document.getElementById("session-list");
const messagesEl = document.getElementById("messages");
const emptyStateEl = document.getElementById("empty-state");
const chatTitleEl = document.getElementById("chat-title");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const newSessionBtn = document.getElementById("new-session-btn");

let currentSessionId = null;

// ---------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------
function renderMessage(role, content) {
  emptyStateEl.style.display = "none";
  const el = document.createElement("div");
  el.className = `message ${role}`;
  el.textContent = content;
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return el;
}

function clearMessages() {
  messagesEl.innerHTML = "";
  messagesEl.appendChild(emptyStateEl);
  emptyStateEl.style.display = "block";
}

function renderSessionList(sessions) {
  sessionListEl.innerHTML = "";
  for (const s of sessions) {
    const item = document.createElement("div");
    item.className = "session-item" + (s.session_id === currentSessionId ? " active" : "");
    item.dataset.sessionId = s.session_id;

    const label = document.createElement("span");
    label.textContent = s.title || "New chat";
    item.appendChild(label);

    const delBtn = document.createElement("button");
    delBtn.className = "delete-btn";
    delBtn.textContent = "✕";
    delBtn.title = "Delete session";
    delBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s.session_id);
    });
    item.appendChild(delBtn);

    item.addEventListener("click", () => openSession(s.session_id));
    sessionListEl.appendChild(item);
  }
}

// ---------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------
async function fetchSessions() {
  const res = await fetch(`${API_BASE}/sessions`);
  if (!res.ok) throw new Error("Failed to load sessions");
  return res.json();
}

async function fetchSession(sessionId) {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}`);
  if (!res.ok) throw new Error("Failed to load session");
  return res.json();
}

async function createSession() {
  const res = await fetch(`${API_BASE}/sessions`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to create session");
  return res.json();
}

async function deleteSessionApi(sessionId) {
  await fetch(`${API_BASE}/sessions/${sessionId}`, { method: "DELETE" });
}

async function sendChatMessage(sessionId, userQuery) {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, user_query: userQuery }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Chat request failed");
  }
  return res.json();
}

// ---------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------
async function refreshSidebar() {
  const sessions = await fetchSessions();
  renderSessionList(sessions);
  return sessions;
}

async function openSession(sessionId) {
  currentSessionId = sessionId;
  clearMessages();

  const data = await fetchSession(sessionId);
  chatTitleEl.textContent = data.title || "New chat";

  if (data.messages && data.messages.length > 0) {
    emptyStateEl.style.display = "none";
    for (const m of data.messages) {
      renderMessage(m.role, m.content);
    }
  }

  await refreshSidebar();
}

async function handleNewSession() {
  const session = await createSession();
  await refreshSidebar();
  await openSession(session.session_id);
  chatInput.focus();
}

async function deleteSession(sessionId) {
  await deleteSessionApi(sessionId);
  if (currentSessionId === sessionId) {
    currentSessionId = null;
    clearMessages();
    chatTitleEl.textContent = "New chat";
  }
  await refreshSidebar();
}

async function handleSendMessage(event) {
  event.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;

  // Lazily create a session if none is selected yet.
  if (!currentSessionId) {
    const session = await createSession();
    currentSessionId = session.session_id;
    await refreshSidebar();
  }

  chatInput.value = "";
  chatInput.style.height = "auto";
  sendBtn.disabled = true;

  renderMessage("user", text);
  const pendingEl = renderMessage("assistant", "…");
  pendingEl.classList.add("pending");

  try {
    const result = await sendChatMessage(currentSessionId, text);
    pendingEl.textContent = result.reply;
    pendingEl.classList.remove("pending");
    await refreshSidebar(); // title may have updated
  } catch (err) {
    pendingEl.textContent = `Error: ${err.message}`;
    pendingEl.classList.remove("pending");
  } finally {
    sendBtn.disabled = false;
    chatInput.focus();
  }
}

// ---------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------
newSessionBtn.addEventListener("click", handleNewSession);
chatForm.addEventListener("submit", handleSendMessage);

chatInput.addEventListener("input", () => {
  chatInput.style.height = "auto";
  chatInput.style.height = `${Math.min(chatInput.scrollHeight, 160)}px`;
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

(async function init() {
  try {
    const sessions = await refreshSidebar();
    if (sessions.length > 0) {
      await openSession(sessions[0].session_id);
    }
  } catch (err) {
    console.error(err);
    emptyStateEl.textContent =
      "Could not reach the backend. Make sure it's running at " + API_BASE;
  }
})();

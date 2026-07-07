import os
import sys
import httpx
import streamlit as st

# ============================================================================
# 1. CONFIGURATION & STATE INITIALIZATION
# ============================================================================
# Matches your exact, verified internal backend endpoint anchor
API_BASE = os.getenv(
    "BACKEND_INTERNAL_URL",
    "https://ca-chat-backend-dev.internal.delightfulground-33da19a5.eastus.azurecontainerapps.io"
)

st.set_page_config(page_title="Foundry Chat", layout="wide")

# Sync local memory states matching app.js globals
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = None
if "chat_messages" not in st.session_state:
    st.session_state.messages = []

# Style elements to replicate your sidebar items and delete buttons cleanly
st.markdown("""
    <style>
    .stButton > button { border-radius: 6px; }
    div[data-testid="stHorizontalBlock"] { align-items: center; }
    </style>
""", unsafe_allow_html=True)

# ============================================================================
# 2. CORE BACKEND API OPERATIONS
# ============================================================================
def get_client():
    return httpx.Client(base_url=API_BASE.rstrip("/"), timeout=60.0)

def fetch_sessions():
    with httpx.Client() as client:
        res = client.get("/sessions")
        if res.status_code != 200: raise Exception("Failed to load sessions")
        return response_data if isinstance((data := res.json()), list) else data.get("sessions", [])

def fetch_session(session_id):
    with httpx.AsyncClient() as client: # Using async context block safely inside sync envelope
        res = httpx.get(f"{API_BASE}/sessions/{session_id}", timeout=60.0)
        if res.status_code != 200: raise Exception("Failed to load session details")
        return res.json()

def create_session():
    with httpx.Client() as client:
        res = client.post("/sessions")
        if res.status_code != 200: raise Exception("Failed to initiate secure session context")
        return res.json()

def delete_session_api(session_id):
    with httpx.Client() as client:
        client.delete(f"/sessions/{session_id}")

def send_chat_message(session_id, user_query):
    with httpx.Client() as client:
        payload = {"session_id": session_id, "user_query": user_query}
        res = client.post("/chat", json=payload)
        if res.status_code != 200:
            try: detail = res.json().get("detail", "Chat engine failure")
            except: detail = "Chat request failed"
            raise Exception(detail)
        return res.json()

# ============================================================================
# 3. SIDEBAR HISTORIES & ACTION MANAGERS
# ============================================================================
def open_session(session_id):
    st.session_state.current_session_id = session_id
    data = fetch_session(session_id)
    st.session_state.messages = data.get("messages", [])

def handle_delete(session_id):
    delete_session_api(session_id)
    if st.session_state.current_session_id == session_id:
        st.session_state.current_session_id = None
        st.session_state.messages = []
    st.rerun()

# --- SIDEBAR COMPONENT PANEL ---
with st.sidebar:
    st.subheader("Foundry Channels")
    
    # Replicates: const newSessionBtn = document.getElementById("new-session-btn");
    if st.button("+ New chat", type="primary", use_container_width=True):
        session_node = create_session()
        open_session(session_node["session_id"])
        st.rerun()
        
    st.write("")
    
    # Replicates: function renderSessionList(sessions)
    try:
        all_active_sessions = fetch_sessions()
        for session in all_active_sessions:
            sid = session["session_id"]
            title = session.get("title") or "New chat"
            
            # Form clean horizontal rows with an inline delete button next to each channel
            col_lbl, col_del = st.columns([0.85, 0.15])
            
            with col_lbl:
                is_active = (sid == st.session_state.current_session_id)
                btn_style = "primary" if is_active else "secondary"
                if st.button(f"💬 {title}", key=f"lbl_{sid}", type=btn_style, use_container_width=True):
                    open_session(sid)
                    st.rerun()
            
            with col_del:
                # Replicates the '✕' event handler mapping context
                if st.button("✕", key=f"del_{sid}", help="Delete this session", use_container_width=True):
                    handle_delete(sid)
    except Exception as network_err:
        st.error(f"Could not reach the backend engine setup at: {API_BASE}")

# ============================================================================
# 4. CHAT INTERFACE COMPONENT PANEL
# ============================================================================
# Resolve dynamic heading layout matching chatTitleEl.textContent
active_title = "New chat"
if st.session_state.current_session_id and 'all_active_sessions' in locals():
    for s in all_active_sessions:
        if s["session_id"] == st.session_state.current_session_id:
            active_title = s.get("title") or "New chat"
            break

st.header(active_title)

# Render empty context warning matching emptyStateEl layout
if not st.session_state.current_session_id:
    st.info("Start the conversation by typing a message below or selecting a history line.")
else:
    # Render all historical entries matching renderMessage(role, content)
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

# Replicates: chatForm.addEventListener("submit", handleSendMessage);
if user_prompt := st.chat_input("Type your message...", disabled=(False if st.session_state.current_session_id else False)):
    
    # 🎯 Lazily create a session if none is selected yet (Matches app.js logic exactly!)
    if not st.session_state.current_session_id:
        lazy_session = create_session()
        st.session_state.current_session_id = lazy_session["session_id"]
        st.session_state.messages = []

    # Display user input immediately
    with st.chat_message("user"):
        st.write(user_prompt)
    st.session_state.messages.append({"role": "user", "content": user_prompt})

    # Display agent response container block with a streaming processing wheel
    with st.chat_message("assistant"):
        with st.spinner("…"):
            try:
                result = send_chat_message(st.session_state.current_session_id, user_prompt)
                st.write(result["reply"])
                st.session_state.messages.append({"role": "assistant", "content": result["reply"]})
            except Exception as chat_err:
                st.error(f"Error: {chat_err}")
                
    # Refresh view layout to enforce state updates instantly
    st.rerun()
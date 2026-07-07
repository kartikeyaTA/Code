import os
import sys
import httpx
import streamlit as st

# ============================================================================
# 1. CONFIGURATION & STATE INITIALIZATION
# ============================================================================
API_BASE = os.getenv(
    "BACKEND_INTERNAL_URL",
    "https://ca-chat-backend-dev.internal.delightfulground-33da19a5.eastus.azurecontainerapps.io"
)

st.set_page_config(page_title="Foundry Chat", layout="wide")

# Sync local memory states matching app.js globals
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []

# Style elements to replicate your sidebar items and delete buttons cleanly
st.markdown("""
    <style>
    .stButton > button { border-radius: 6px; }
    div[data-testid="stHorizontalBlock"] { align-items: center; }
    </style>
""", unsafe_allow_html=True)

# ============================================================================
# 2. CORE BACKEND API OPERATIONS (FIXED CLIENT SCOPING)
# ============================================================================
def get_client():
    # Safely anchors all relative routes to your private backend endpoint
    return httpx.Client(base_url=API_BASE.rstrip("/"), timeout=60.0)

def fetch_sessions():
    with get_client() as client:
        res = client.get("/sessions")
        if res.status_code != 200: 
            raise Exception("Failed to load sessions")
        data = res.json()
        return data if isinstance(data, list) else data.get("sessions", [])

def fetch_session(session_id):
    with get_client() as client:
        res = client.get(f"/sessions/{session_id}")
        if res.status_code != 200: 
            raise Exception("Failed to load session details")
        return res.json()

def create_session():
    with get_client() as client:
        res = client.post("/sessions")
        if res.status_code != 200: 
            raise Exception("Failed to initiate secure session context")
        return res.json()

def delete_session_api(session_id):
    with get_client() as client:
        client.delete(f"/sessions/{session_id}")

def send_chat_message(session_id, user_query):
    with get_client() as client:
        payload = {"session_id": session_id, "user_query": user_query}
        res = client.post("/chat", json=payload)
        if res.status_code != 200:
            try: 
                detail = res.json().get("detail", "Chat engine failure")
            except: 
                detail = "Chat request failed"
            raise Exception(detail)
        return res.json()

# ============================================================================
# 3. SIDEBAR HISTORIES & ACTION MANAGERS
# ============================================================================
def open_session(session_id):
    st.session_state.current_session_id = session_id
    try:
        data = fetch_session(session_id)
        st.session_state.messages = data.get("messages", [])
    except Exception as e:
        st.error(f"Failed to open session: {e}")

def handle_delete(session_id):
    try:
        delete_session_api(session_id)
        if st.session_state.current_session_id == session_id:
            st.session_state.current_session_id = None
            st.session_state.messages = []
    except Exception as e:
        st.error(f"Failed to delete session: {e}")
    st.rerun()

# --- SIDEBAR COMPONENT PANEL ---
with st.sidebar:
    st.subheader("Foundry Channels")
    
    if st.button("+ New chat", type="primary", use_container_width=True):
        try:
            session_node = create_session()
            open_session(session_node["session_id"])
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Error creating session: {e}")
        
    st.write("")
    
    try:
        all_active_sessions = fetch_sessions()
        for session in all_active_sessions:
            sid = session["session_id"]
            title = session.get("title") or "New chat"
            
            col_lbl, col_del = st.columns([0.85, 0.15])
            
            with col_lbl:
                is_active = (sid == st.session_state.current_session_id)
                btn_style = "primary" if is_active else "secondary"
                if st.button(f"💬 {title}", key=f"lbl_{sid}", type=btn_style, use_container_width=True):
                    open_session(sid)
                    st.rerun()
            
            with col_del:
                if st.button("✕", key=f"del_{sid}", help="Delete this session", use_container_width=True):
                    handle_delete(sid)
    except Exception as network_err:
        st.error(f"Could not reach the backend engine setup at: {API_BASE}")

# ============================================================================
# 4. CHAT INTERFACE COMPONENT PANEL
# ============================================================================
active_title = "New chat"
if st.session_state.current_session_id and 'all_active_sessions' in locals():
    for s in all_active_sessions:
        if s["session_id"] == st.session_state.current_session_id:
            active_title = s.get("title") or "New chat"
            break

st.header(active_title)

if not st.session_state.current_session_id:
    st.info("Start the conversation by typing a message below or selecting a history line.")
else:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

if user_prompt := st.chat_input("Type your message..."):
    
    # Lazily create a session if none is selected yet.
    if not st.session_state.current_session_id:
        try:
            lazy_session = create_session()
            st.session_state.current_session_id = lazy_session["session_id"]
            st.session_state.messages = []
        except Exception as e:
            st.error(f"Failed to lazily create session: {e}")
            st.stop()

    with st.chat_message("user"):
        st.write(user_prompt)
    st.session_state.messages.append({"role": "user", "content": user_prompt})

    with st.chat_message("assistant"):
        with st.spinner("…"):
            try:
                result = send_chat_message(st.session_state.current_session_id, user_prompt)
                st.write(result["reply"])
                st.session_state.messages.append({"role": "assistant", "content": result["reply"]})
            except Exception as chat_err:
                st.error(f"Error: {chat_err}")
                
    st.rerun()
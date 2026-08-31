from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from agent_graph import graph

app = FastAPI()

@app.get("/")
async def get_ui():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

# Updated to accept session_id dynamically from the client
@app.websocket("/ws/voice/{session_id}")
async def voice_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    print(f"WebSocket connected for session: {session_id}")

    # Use the session_id to maintain memory context in LangGraph
    config = {"configurable": {"thread_id": session_id}}

    try:
        while True:
            data = await websocket.receive_text()
            print("RECEIVED:", data)

            async for event in graph.astream_events(
                {"messages": [("user", data)]},
                config=config,
                version="v2"
            ):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"].content
                    if chunk and isinstance(chunk, str):
                        await websocket.send_text(chunk)

    except WebSocketDisconnect:
        print(f"WebSocket disconnected for session: {session_id}")
    except Exception as e:
        print("ERROR:", str(e))
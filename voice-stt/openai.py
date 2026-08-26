"""
FastAPI backend for GPT Realtime + MCP (ServiceNow) voice assistant.

Responsibilities of this server:
  1. Serve the frontend static files.
  2. Accept a WebSocket connection from the browser (audio in / audio out,
     plus transcript + control events) at /ws.
  3. For each browser connection, open a SEPARATE server-to-server
     WebSocket connection to Azure OpenAI's Realtime API, authenticated
     with your Azure OpenAI API key (never sent to the browser).
  4. Send session.update once, attaching your ServiceNow MCP server as an
     "mcp" tool, with an authorization token that YOUR MCP server will
     validate (issuer check) and then exchange via OBO for a
     ServiceNow-scoped token. This backend does not talk to ServiceNow
     directly -- Azure's Realtime service calls your MCP server directly,
     and your MCP server does the OBO + ServiceNow call.
  5. Relay raw audio/events bidirectionally between browser <-> Azure.

Run with:
    uvicorn main:app --reload --port 3000
"""

import asyncio
import json
import os
from pathlib import Path

import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState
from websockets.exceptions import ConnectionClosed
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

load_dotenv()

# prithivee's tiger account
# AZURE_OPENAI_ENDPOINT = "https://openai-voice.openai.azure.com"
# AZURE_OPENAI_API_KEY = "CiYosxKN22Is37FKCHUQMlgadWqbJFVtwi9YcuWX4lxAG8uE01ruJQQJ99CHACHYHv6XJ3w3AAAAACOG2SG6"
# AZURE_OPENAI_DEPLOYMENT_NAME = "gpt-realtime-1.5"
# MCP_SERVER_URL = "https://apim-gateway-application-test-dev-txrh-mcp.azure-api.net/snow-mcp/mcp"
# MCP_SERVER_LABEL = os.environ.get("MCP_SERVER_LABEL", "servicenow")


#########################################################################################################################
# prithivee's personal account
AZURE_OPENAI_ENDPOINT = "https://voice-open-ai-txrh.services.ai.azure.com"
AZURE_OPENAI_DEPLOYMENT_NAME = "gpt-realtime-1.5"
MCP_SERVER_URL = "https://apim-gateway-application-test-dev-txrh-mcp.azure-api.net/snow-mcp/mcp"
MCP_SERVER_LABEL = os.environ.get("MCP_SERVER_LABEL", "servicenow")


credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(credential, "https://ai.azure.com/.default")


async def get_azure_openai_token() -> str:
    # get_bearer_token_provider's returned callable is sync/blocking
    # (it does a network call on cache miss), so run it off the event loop.
    return await asyncio.to_thread(token_provider)

#########################################################################################################################

# -----------------------------------------------------------------------
# The prompt: this is where you steer the model on WHEN and HOW to use the
# ServiceNow MCP tools. Edit this to match your org's tool names/behavior.
# -----------------------------------------------------------------------
SYSTEM_PROMPT = f"""
Role & Objective
Respond only in English
You are an advanced Customer Support Agent capable of intent classification, Retrieval-Augmented Generation (RAG), checking ticket history, and automated ticket creation / updation. For a single query, you have the capability to call multiple tools. You must process user inputs, maintain session state, enforce strict grounding, and strictly adhere to length constraints. At the end always say why u have/haven’t  created / updated a ticket.
You help users work with ServiceNow via the tools available to you on the "{MCP_SERVER_LABEL}" MCP server.

Session State Variables
You must maintain, evaluate, and update the following state tracking variables for each session:

messages: A historical list of user inputs and agent responses.
previous_tickets: A list containing the user's previously created tickets.



consecutive_not_found_count: Counter tracking how many times in a row the system failed to find the answer in the Knowledge Base (KB).

consecutive_not_satisfied_count: Counter tracking how many times in a row the user expressed dissatisfaction.

consecutive_queries_ignoring_yes_no: Counter tracking how many times in a row the user asked a new question instead of answering a mandatory "Yes/No" satisfaction check.

Step 1: Intent Classification
For every incoming user message, classify the user's intent into any of the following categories: In some cases there could be multiple intents.

GREETING: The user is saying hello or initiating conversational small talk.

QUERY: The user is asking a factual/informational question.

YES (Positive): The user is confirming, agreeing, or expressing satisfaction.

NO (Negative): The user is denying, disagreeing, or expressing dissatisfaction. If the user says no/ something negative along with a query, then consider the intent as query and follow the query path.

TICKET_REQUEST: The user explicitly requests to create a support ticket.

VIEW_TICKETS: The user explicitly asks to display or view their previous tickets.

GET_TICKET: The user asks information about any of their previously created tickets.

A ticket has to be created / updated for all intents other than Greeting,  view tickets, get ticket. Ticket creation/updation is mandatory when the intent is query .

Step 2: Conversation Logic & Routing Rules
Evaluate the user's intent alongside the current session state and apply these routing rules:

A. Greeting Handling
Condition: Intent is GREETING.

Action: Respond with a polite greeting. Clear any active dissatisfaction or fallback counters. End the cycle.

B. Viewing Previous Tickets
Condition: Intent is VIEW_TICKETS.

Action: Call the tool for viewing previous tickets for the user. If previous_tickets contains data, display the tickets using bullet points. If previous_tickets is empty, respond exactly with: "For that user, there are no tickets."

C. Standard Query & RAG Execution
Condition: Intent is QUERY.
Action: Execute the RAG process using the provided context.

If the answer is found and if a ticket was already created in the session with the similar topic of the query – Provide the answer (adhering to the Length Constraints below). Then, append the exact phrase: "Are you satisfied with the agent's response?" After this route to ticket updation. Do not skip ticket updation step because a ticket was already created, this is very important.

If the answer is found and if a ticket was not created in the session with the similar topic of the query – Provide the answer (adhering to the Length Constraints below). Then, append the exact phrase: "Are you satisfied with the agent's response?" After this route to ticket creation.
If the answer is NOT found: Respond exactly with: "I am not aware of this based on the provided information." and increment consecutive_not_found_count by 1. Do not ask if they are satisfied. Proceed to the KB Failure Evaluation below.

D. Evaluation of Knowledge Base (KB) Failures
If consecutive_not_found_count reaches 1: Ask the user to rephrase their question.
If consecutive_not_found_count reaches 2: Immediately route to Ticket Creation.

E. Satisfaction Checks (Handling YES/NO Responses)
If the previous agent message asked: "Are you satisfied with the agent's response?", evaluate the user's input as follows:

If intent is YES (Positive): Acknowledge their satisfaction. Close the ticket (do not update) and  Reset counters to 0. End the cycle.

If intent is NO (Negative) for the 1st time (consecutive_not_satisfied_count = 1): Ask the user to rephrase their question.

If intent is NO (Negative) for the 2nd time in a row (consecutive_not_satisfied_count = 2): Immediately route to Ticket Creation.

If the user ignores the Yes/No request and instead asks a new QUERY:

Increment consecutive_queries_ignoring_yes_no by 1.

If consecutive_queries_ignoring_yes_no reaches 3: Bypass the query and immediately route to Ticket Creation.

Otherwise, process the query using standard RAG rules.

F. Explicit Ticket Requests
Condition: Intent is TICKET_REQUEST, and the user has asked at least one informational question previously in this session.

Action: Immediately route to Ticket Creation.

G. Ticket Creation Execution
Condition: Triggered by any of the escalating rules above.

Action: Output the exact text: "Ticket created successfully" along with the created ticket number. During ticket creation, both the user input and the agent response has to be strictly logged in the created ticket as comments(no work notes), omit the part about  “are you satisfied with the agent response” in the ticket. Get this ticket information and append it to the previous_tickets list. Reset all tracking counters. End the cycle.

H. Ticket Updation Execution
Condition: Triggered by any of the escalating rules above.

Action: Output the exact text: "Ticket updated successfully" along with the updated ticket number. During ticket updation, both the user input and the agent response has to be strictly logged in the updated ticket as comments (no work notes), omit the part about  “are you satisfied with the agent response” in the ticket. Do not remove the existing ticket description, just append this information. Reset all tracking counters. End the cycle.


I. GET_TICKET
Condition: Triggered by providing the username and the ticket number.

Action: If the ticket number and username matches, display information about the particular ticket. Else reply that there is no ticket found with the provided ticket number for the user.

Step 3: RAG Constraints & Grounding Rules
When answering a QUERY using provided data context, you must strictly follow these constraints:

Answer ONLY based on the provided text context. Do NOT use external knowledge.

Do NOT make assumptions, extrapolate, or infer beyond the explicitly stated context. Provide reference for every response.

If the answer is not explicitly present in the context, you must respond exactly with: "I am not aware of this based on the provided information."

Length Constraint: For factual questions, answer in 1 or 2 lines. Under no circumstance should the response be more than 4 lines. Be concise, clear, and professional.
""".strip()





def build_azure_realtime_url() -> str:
    ws_endpoint = AZURE_OPENAI_ENDPOINT.rstrip("/").replace("https://", "wss://", 1)
    return f"{ws_endpoint}/openai/v1/realtime?model={AZURE_OPENAI_DEPLOYMENT_NAME}"


# -----------------------------------------------------------------------
# Per-connection: obtain the token to hand Azure for the MCP tool's
# "authorization" field. Azure will send this as a bearer token to your
# MCP server on every tool call for this session.
#
# Replace this with your real logic. Typical options:
#  (a) The browser authenticates to THIS backend first (e.g. via your own
#      login/session cookie or a token attached to the WS connect request).
#      You validate that, then forward a token whose issuer matches what
#      your MCP server's issuer check expects.
#  (b) You already have an Entra ID confidential-client / OBO flow in this
#      backend that produces a token for the signed-in user which your MCP
#      server's issuer check accepts (the MCP server then does its OWN
#      separate OBO exchange to get a ServiceNow-scoped token).
#
# Either way: THIS backend does not exchange for a ServiceNow token itself
# -- that exchange happens inside your MCP server, as you described.
# -----------------------------------------------------------------------
async def get_mcp_authorization_token_for_request(websocket: WebSocket) -> str:
    # Example (a): read a bearer token the browser attached when opening
    # the WebSocket, e.g. ws://host/ws?token=... Swap in your real
    # session/auth mechanism (cookie, header via subprotocol, etc.).
    token = websocket.query_params.get("token")
    if not token:
        raise ValueError(
            "No user token supplied on WebSocket connection. Reject the "
            "connection or fall back to a service-identity token, "
            "depending on your auth model."
        )

    # TODO: validate `token` here if this backend should also check it
    # (issuer/audience/expiry) before trusting it, e.g. with PyJWT against
    # your Entra ID tenant's JWKS endpoint.

    # Return the token you want Azure to send to your MCP server as
    # "Authorization: Bearer <this value>". Often this is simply the same
    # incoming user token, since your MCP server does its own issuer check
    # and its own OBO exchange against it.
    return token


# -----------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------
app = FastAPI()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.websocket("/ws")
async def websocket_endpoint(client_ws: WebSocket):
    await client_ws.accept()
    print("Browser client connected")

    try:
        # mcp_auth_token = await get_mcp_authorization_token_for_request(client_ws)
        mcp_auth_token = "mcp-auth-token"
    except ValueError as exc:
        print(f"Auth failure on client connection: {exc}")
        await client_ws.close(code=4001)
        return

    azure_url = build_azure_realtime_url()
    azure_token = await get_azure_openai_token()

    try:
        async with websockets.connect(
            azure_url,
            additional_headers={"Authorization": f"Bearer {azure_token}"},
            max_size=None,
        ) as azure_ws:
            print("Connected to Azure OpenAI Realtime API")

            session_update = {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "instructions": SYSTEM_PROMPT,
                    "output_modalities": ["audio"],
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "transcription": {"model": "whisper-1"},
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": 500,
                                "create_response": True,
                            },
                        },
                        "output": {
                            "voice": "alloy",
                            "format": {"type": "audio/pcm", "rate": 24000},
                        },
                    },
                    "tools": [
                        {
                            "type": "mcp",
                            "server_label": MCP_SERVER_LABEL,
                            "server_url": MCP_SERVER_URL,
                            "authorization": mcp_auth_token,
                            # "never" = tool calls execute immediately, no
                            # approval round-trip surfaced to the client.
                            # Change this if ServiceNow writes need a human
                            # approval step.
                            "require_approval": "never",
                        }
                    ],
                },
            }
            await azure_ws.send(json.dumps(session_update))

            session_configured = asyncio.Event()
            buffered_client_messages: list[str] = []

            async def relay_azure_to_client():
                async for raw in azure_ws:
                    if client_ws.client_state != WebSocketState.CONNECTED:
                        break
                    await client_ws.send_text(raw)

                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    evt_type = evt.get("type")
                    print("evt_type", evt_type)
                    if evt_type == "session.updated":
                        session_configured.set()
                        for queued in buffered_client_messages:
                            await azure_ws.send(queued)
                        buffered_client_messages.clear()
                    elif evt_type == "response.mcp_call.completed":
                        print("MCP call finished — requesting follow-up response")
                        await azure_ws.send(json.dumps({"type": "response.create"}))

                    elif evt_type == "response.mcp_call.failed":
                        print("MCP call FAILED — requesting follow-up response so model can explain")
                        await azure_ws.send(json.dumps({"type": "response.create"}))
                    elif evt_type == "error":
                        print("Azure Realtime error event:", evt.get("error"))
                    elif evt_type and "mcp" in evt_type:
                        # These event names are indicative of MCP tool-call
                        # activity. Log for visibility while building;
                        # confirm exact names against the Realtime API
                        # reference for your API version.
                        print("MCP-related event:", evt_type, str(evt)[:500])
                    elif evt_type == "response.done":
                        resp = evt.get("response", {})
                        print("RESPONSE STATUS:", resp.get("status"))
                        print("RESPONSE STATUS DETAILS:", json.dumps(resp.get("status_details"), indent=2))
                        print("RESPONSE OUTPUT:", json.dumps(resp.get("output"), indent=2))

                    elif evt_type == "response.output_item.done":
                        item = evt.get("item", {})
                        if item.get("type") == "mcp_call":
                            print("MCP CALL RESULT:", json.dumps(item, indent=2))
                    else:
                        print("EVENT:", evt_type, str(evt)[:300])

            async def relay_client_to_azure():
                try:
                    while True:
                        msg = await client_ws.receive_text()
                        if not session_configured.is_set():
                            buffered_client_messages.append(msg)
                            continue
                        await azure_ws.send(msg)
                except WebSocketDisconnect:
                    print("Browser client disconnected")

            azure_to_client_task = asyncio.create_task(relay_azure_to_client())
            client_to_azure_task = asyncio.create_task(relay_client_to_azure())

            _done, pending = await asyncio.wait(
                {azure_to_client_task, client_to_azure_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except (ConnectionClosed, OSError) as exc:
        print(f"Azure WS error: {exc}")
    finally:
        if client_ws.client_state == WebSocketState.CONNECTED:
            await client_ws.close()


# -----------------------------------------------------------------------
# Frontend static files
# -----------------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


# Serves app.js (and anything else) at the same paths the frontend expects.
# Registered AFTER the /ws route and / route so those take precedence.
app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="frontend")

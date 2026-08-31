import httpx
from typing import Annotated, TypedDict
from langchain_core.tools import tool
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

# --- Configuration ---
AZURE_OPENAI_KEY = "5BEMjoQafnbskwZmDEb4szFTS6THp4LQHbAQNhJpnvXLVvgGWN56JQQJ99CDACYeBjFXJ3w3AAABACOG2rkF"
AZURE_OPENAI_ENDPOINT = "https://txrhazureopenai.openai.azure.com/"
SEARCH_ENDPOINT = "https://txrhsearchservice.search.windows.net"
SEARCH_KEY = "NXdBvQDJI14QKJ6A3x5oLJNooiKWUWv7FHkiaX9zSYAzSeBx8IWc"
INDEX_NAME = "sharepoint-ai-search-index-index"
SNOW_URL = "https://dev296375.service-now.com"
SNOW_USER = "admin"
SNOW_PASS = "K!7im9NNkjY^"


# --- Define LangChain Tools ---
@tool
async def search_kb(query: str) -> str:
    """Searches the internal knowledge base using Azure AI Search to answer user queries."""
    url = f"{SEARCH_ENDPOINT}/indexes/{INDEX_NAME}/docs/search?api-version=2025-11-01-preview"
    headers = {"api-key": SEARCH_KEY, "Content-Type": "application/json"}
    payload = {"search": query, "top": 1}

    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code != 200:
            return "Error searching the knowledge base."
        data = r.json()

    docs = data.get("value", [])
    return "\n".join([d.get("chunk", "") for d in docs])


@tool
async def create_incident(short_description: str) -> str:
    """Creates a service request / incident ticket in ServiceNow for the user."""
    url = f"{SNOW_URL}/api/now/table/incident"
    payload = {"short_description": short_description}
    async with httpx.AsyncClient() as client:
        r = await client.post(url, auth=(SNOW_USER, SNOW_PASS), json=payload)

        if r.status_code in (200, 201):
            try:
                # Attempt to extract the generated INC number from ServiceNow response
                data = r.json()
                ticket_number = data.get("result", {}).get("number", "an unknown number")
                return f"Ticket successfully created. The ticket number is {ticket_number}."
            except Exception:
                return "Ticket successfully created, but I could not retrieve the exact ticket number."

        return "Failed to create the ticket. Please try again later."


# --- Setup Model ---
llm = AzureChatOpenAI(
    api_key=AZURE_OPENAI_KEY,
    api_version="2025-01-01-preview",
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    azure_deployment="gpt-4.1-mini",
    temperature=0.7,
    streaming=True
)

tools = [search_kb, create_incident]
llm_with_tools = llm.bind_tools(tools)

# Prompt enforcing your strict voice flow
SYSTEM_PROMPT = """You are a helpful Voice Helpdesk Agent. Follow this flow strictly:
1. Greet the user warmly if they say hello.
2. When asked a question, ALWAYS use the 'search_kb' tool to look up the answer. Provide the answer clearly.
3. ONLY after providing an answer retrieved from the knowledge base, you MUST append: "Are you satisfied with this answer?". Do NOT ask this if you are just asking for clarification, greeting the user, or acknowledging a ticket creation.
4. If the user is NOT satisfied with a knowledge base answer, ask them to provide more details OR offer to create a service request ticket.
5. If the user agrees to create a ticket or requests one directly, use the 'create_incident' tool to create it.

Important: Keep responses conversational, concise, and natural as they will be spoken aloud by a TTS engine. Do NOT use markdown formatting or long lists."""


# --- LangGraph Setup ---
class State(TypedDict):
    messages: Annotated[list, add_messages]


async def chatbot(state: State):
    # Prepend the system instructions before processing state history
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = await llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


graph_builder = StateGraph(State)
graph_builder.add_node("chatbot", chatbot)
tool_node = ToolNode(tools=tools)
graph_builder.add_node("tools", tool_node)

# Add conditional routing (if LLM calls tool -> go to tool, otherwise END)
graph_builder.add_conditional_edges("chatbot", tools_condition)
graph_builder.add_edge("tools", "chatbot")
graph_builder.add_edge(START, "chatbot")

# Compile graph with Memory to persist context over WebSocket lifecycle
memory = MemorySaver()
graph = graph_builder.compile(checkpointer=memory)
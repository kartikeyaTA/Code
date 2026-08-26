import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError, ServiceRequestError
from azure.ai.projects.models import PromptAgentDefinition, MCPTool  # 🎯 IMPORT FOR CREATION BLUEPRINT + MCP TOOL


# ============================================================================
# 1. PARAMETERS & CONFIGURATION
# ============================================================================
project_endpoint = "https://txrh-aif-roadierangerdev-6279-stosup-phmo-standard.services.ai.azure.com/api/projects/txrh-proj-RoadieRangerDev-6279-StoSup-pHmO-standard-default"
agent_name = "Text-Agent"
prompt_file_path = "prompt.md"
model_deployment = "txrh-apim-AISharedServicesGeneralProd-6279-StoSup/gpt-5.1"

# 🎯 MCP TOOL CONFIG -- points at your MCP server hosted on Azure Container Apps
mcp_server_label = os.environ.get("MCP_SERVER_LABEL", "mcptool")
mcp_server_url = 'https://txrh-apim-aisharedservicesgeneralprod-6279-stosup.azure-api.net/snow/mcp'  # confirm this is your real MCP endpoint path (often /mcp, not root "/")
mcp_project_connection_id = os.environ.get("MCP_PROJECT_CONNECTION_ID", "txrh-agent-tool-snow-mcp")  # name of a pre-created Custom Keys connection holding x-api-key
mcp_require_approval = os.environ.get("MCP_REQUIRE_APPROVAL", "never")  # "never" | "always"

if not mcp_project_connection_id:
    print("❌ MCP_PROJECT_CONNECTION_ID is required -- create the Custom Keys connection in Foundry first (see Bicep snippet), then set this.")
    sys.exit(1)

if not os.path.exists(prompt_file_path):
    print(f"📁 Local Error: '{prompt_file_path}' not found! Creating template file...")
    with open(prompt_file_path, "w", encoding="utf-8") as f:
        f.write("You are an expert AI agent running inside Microsoft Foundry.")

print(f"📖 Reading system instructions from '{prompt_file_path}'...")
with open(prompt_file_path, "r", encoding="utf-8") as file:
    new_instructions = file.read().strip()

print(f"🔎 Verifying MCP connection '{mcp_project_connection_id}' exists (create it in the Foundry portal first -- see console steps)...")


# ============================================================================
# 2. MCP TOOL HELPERS
# ============================================================================
def build_mcp_tool() -> MCPTool:
    """
    Builds the MCP tool definition pointing at the Container Apps-hosted MCP
    server. Auth is via project_connection_id (a pre-created Custom Keys
    connection), NOT raw headers -- passing the real API key inline as a
    `headers={...}` kwarg gets silently dropped for header names the service
    flags as sensitive (things like "Authorization" or anything with "key"
    in the name), and there is NO automatic OAuth passthrough despite what
    it might look like when the header-based version "worked" without error.
    """
    return MCPTool(
        server_label=mcp_server_label,
        server_url=mcp_server_url,
        require_approval=mcp_require_approval,
        project_connection_id=mcp_project_connection_id,
    )


def ensure_mcp_tool(existing_tools) -> list:
    """
    Ensures the MCP tool is present AND up to date in an agent's tool list.
    If a tool with the same server_label already exists, it gets REPLACED
    with a freshly-built one -- this reconciles drift (wrong URL, wrong
    connection, wrong require_approval) instead of silently preserving
    whatever was there before. Non-MCP tools and other MCP tools with
    different labels are left untouched.
    """
    tools = list(existing_tools or [])
    updated_tools = []
    found = False

    for t in tools:
        t_type = t.get("type") if isinstance(t, dict) else getattr(t, "type", None)
        t_label = t.get("server_label") if isinstance(t, dict) else getattr(t, "server_label", None)
        if t_type == "mcp" and t_label == mcp_server_label:
            print(f"🔄 MCP tool '{mcp_server_label}' found -- reconciling to current config (url/connection/approval), not just leaving it as-is.")
            updated_tools.append(build_mcp_tool())
            found = True
        else:
            updated_tools.append(t)

    if not found:
        print(f"🛠️ MCP tool '{mcp_server_label}' not found on agent -- creating & connecting it now (-> {mcp_server_url}).")
        updated_tools.append(build_mcp_tool())

    return updated_tools


# ============================================================================
# 3. SEED TRANSACTION CLIENT ENGINE
# ============================================================================
print("\n🚀 Initializing secure Foundry project client transaction...")
with AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True  # 🎯 Crucial to map preview agent layers safely
) as client:

    try:
        client.connections.get(mcp_project_connection_id)
        print(f" -> MCP connection '{mcp_project_connection_id}' found.")
    except ResourceNotFoundError:
        print(f"❌ MCP connection '{mcp_project_connection_id}' does not exist yet. Create it in the Foundry portal (Connected resources -> + New connection -> Custom keys), then re-run this script.")
        sys.exit(1)
    except ServiceRequestError as e:
        print(f"❌ Could not reach the Foundry endpoint at all -- this is a network/DNS issue, not a config issue.")
        print(f"   Check DNS resolution, VPN state, and general connectivity before re-running. Details: {e}")
        sys.exit(1)

    try:
        # Check Option A: Fetch and update existing configurations
        print(f"🔍 Searching for existing tracking configuration for '{agent_name}'...")
        existing_agent = client.agents.get(agent_name=agent_name)

        print(f" -> Found agent target context! Pushing new version layout...")
        current_definition = existing_agent.versions.latest.definition

        if isinstance(current_definition, dict):
            current_definition["instructions"] = new_instructions
            current_definition["model"] = model_deployment
            current_definition["tools"] = ensure_mcp_tool(current_definition.get("tools"))
        else:
            current_definition.instructions = new_instructions
            current_definition.model = model_deployment
            current_definition.tools = ensure_mcp_tool(getattr(current_definition, "tools", None))

        new_version = client.agents.create_version(
            agent_name=agent_name,
            definition=current_definition
        )
        print(f"\n🎯 UPDATE SUCCESS: Pushed version '{new_version.version}' to '{agent_name}'.")
        if new_version.version:
    # 🎯 This special print command creates an Azure DevOps variable named $(AgentVersion) dynamically
            print(f"##vso[task.setvariable variable=AgentVersion;]{new_version.version}")
            with open("version.txt", "w", encoding="utf-8") as f:
                f.write(str(new_version.version))
            print(f"🚀 Successfully exposed version '{new_version.version}' to the pipeline agent context.")
        else:
            print("❌ Failed to resolve a valid agent version string.")
            sys.exit(1)
    except ResourceNotFoundError:
        # Check Option B: Fallback and trigger creation engine safely via Agent Definition wrapper
        print(f"\n⚠️ Asset Context Not Found: '{agent_name}' does not exist.")
        print(f"🛠️ Instantiating creation factory layout using model '{model_deployment}'...")

        mcp_tool = build_mcp_tool()
        print(f"🔗 Attaching MCP tool '{mcp_server_label}' -> {mcp_server_url}")

        # 🎯 Correct method layout using native definition wrappers:
        new_agent_version = client.agents.create_version(
            agent_name=agent_name,
            definition=PromptAgentDefinition(
                model=model_deployment,
                instructions=new_instructions,
                tools=[mcp_tool],
            )
        )
        print(f"\n🎯 CREATION SUCCESS: Brand new agent created directly via code context.")
        print(f" -> Assigned Initial Tracking Version: {new_agent_version.version}")
        if new_agent_version.version:
    # 🎯 This special print command creates an Azure DevOps variable named $(AgentVersion) dynamically
            print(f"##vso[task.setvariable variable=AgentVersion;]{new_agent_version.version}")
            with open("version.txt", "w", encoding="utf-8") as f:
                f.write(str(new_agent_version.version))
            print(f"🚀 Successfully exposed version '{new_agent_version.version}' to the pipeline agent context.")
        else:
            print("❌ Failed to resolve a valid agent version string.")
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ Execution Exception encountered: {e}")
        sys.exit(1)

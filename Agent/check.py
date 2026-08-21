import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError, ServiceRequestError
from azure.ai.projects.models import PromptAgentDefinition, MCPTool

# ============================================================================
# CONFIGURATION
# ============================================================================
project_endpoint = 'https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project'
agent_name = "txrh-demoagent-2-copy1352324"
prompt_file_path = "prompt.txt"
model_deployment = "roadie-ranger-foundry-resource/gpt-5.4"

# mcp_server_label = os.environ.get("MCP_SERVER_LABEL", "mcptool")
# mcp_server_url = 'https://apim-gateway-application-test-dev-txrh-mcp.azure-api.net/mcp-snow/mcp'
# mcp_project_connection_id = os.environ.get("MCP_PROJECT_CONNECTION_ID", "mcp-servicenow-oauth-passthrough2")
# mcp_require_approval = os.environ.get("MCP_REQUIRE_APPROVAL", "never")

if not os.path.exists(prompt_file_path):
    print(f"❌ Local Error: '{prompt_file_path}' not found!")
    sys.exit(1)

with open(prompt_file_path, "r", encoding="utf-8") as file:
    new_instructions = file.read().strip()


# def build_mcp_tool() -> MCPTool:
#     return MCPTool(
#         server_label=mcp_server_label,
#         server_url=mcp_server_url,
#         require_approval=mcp_require_approval,
#         project_connection_id=mcp_project_connection_id,
#     )


# def ensure_mcp_tool(existing_tools) -> list:
#     tools = list(existing_tools or [])
#     updated_tools = []
#     found = False

#     for t in tools:
#         t_type = t.get("type") if isinstance(t, dict) else getattr(t, "type", None)
#         t_label = t.get("server_label") if isinstance(t, dict) else getattr(t, "server_label", None)
#         if t_type == "mcp" and t_label == mcp_server_label:
#             updated_tools.append(build_mcp_tool())
#             found = True
#         else:
#             updated_tools.append(t)

#     if not found:
#         updated_tools.append(build_mcp_tool())

    # return updated_tools


# ============================================================================
# EXECUTION ENGINE
# ============================================================================
with AIProjectClient(
    endpoint=project_endpoint,
    credential=DefaultAzureCredential(),
    allow_preview=True
) as client:

    active_release_version = None
    target_tools = []

    try:
        # 1. Fetch existing agent
        existing_agent = client.agents.get(agent_name=agent_name)
        
        # 2. Find highest published (non-draft) release version
        try:
            versions_list = list(client.agents.list_versions(agent_name=agent_name))
            published_vers = []
            for v in versions_list:
                v_num = getattr(v, "version", None) or (v.get("version") if isinstance(v, dict) else None)
                is_draft = getattr(v, "draft", False) or (v.get("draft") if isinstance(v, dict) else False)
                if v_num and str(v_num).isdigit() and not is_draft:
                    published_vers.append((int(v_num), str(v_num)))
            
            if published_vers:
                published_vers.sort(key=lambda x: x[0])
                active_release_version = published_vers[-1][1]
            elif versions_list:
                all_vers = []
                for v in versions_list:
                    v_num = getattr(v, "version", None) or (v.get("version") if isinstance(v, dict) else None)
                    if v_num and str(v_num).isdigit():
                        all_vers.append((int(v_num), str(v_num)))
                if all_vers:
                    all_vers.sort(key=lambda x: x[0])
                    active_release_version = all_vers[-1][1]
        except Exception as err:
            print(f"⚠️ Could not list versions: {err}")

        print(f"📌 ACTIVE RELEASE VERSION DETECTED: '{active_release_version or 'None'}'")

        # 3. Retrieve tools from active release version
        existing_tools = []
        if active_release_version:
            try:
                version_obj = client.agents.get_version(agent_name=agent_name, agent_version=active_release_version)
                defl = getattr(version_obj, "definition", None)
                if defl:
                    existing_tools = defl.get("tools", []) if isinstance(defl, dict) else getattr(defl, "tools", [])
            except Exception as e:
                print(f"⚠️ Could not fetch details for version {active_release_version}: {e}")

        # target_tools = ensure_mcp_tool(existing_tools)

    except ResourceNotFoundError:
        print(f"⚠️ Agent '{agent_name}' does not exist. Creating initial version...")
        # target_tools = [build_mcp_tool()]

    # 4. Create new version as an UNPUBLISHED DRAFT ("draft": True in request body)
    prompt_def = PromptAgentDefinition(
        model=model_deployment,
        instructions=new_instructions,
        tools=target_tools,
    )
    def_dict = prompt_def.as_dict() if hasattr(prompt_def, "as_dict") else prompt_def

    new_agent_version = client.agents.create_version(
        agent_name=agent_name,
        body={
            "definition": def_dict,
            "draft": True  # 👈 Prevents auto-promotion to @latest / active release
        }
    )

    new_version_str = str(getattr(new_agent_version, "version", None) or new_agent_version.get("version"))
    print(f"\n🎯 NEW UNPUBLISHED DRAFT CREATED: Version '{new_version_str}' generated for agent '{agent_name}'.")
    if active_release_version:
        print(f"🔒 Active Production Traffic remains locked at Version '{active_release_version}'.")
    # Convert draft into a published numeric release after evaluation passes
   
    # Output Pipeline Variables
    print(f"##vso[task.setvariable variable=AgentVersion;]{new_version_str}")
    with open("version.txt", "w", encoding="utf-8") as f:
        f.write(new_version_str)
    print(f"🚀 Saved draft version '{new_version_str}' to 'version.txt' and pipeline variable AgentVersion.")
from __future__ import annotations

import logging
import os
import sys

# Azure SDK Imports
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    PromptAgentDefinition,
    PromptAgentDefinitionTextOptions,
    TextResponseFormatJsonSchema,
    MCPTool,
)
from azure.core.exceptions import ResourceNotFoundError, ServiceRequestError

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("helpdesk_agent")


# ============================================================================
# 1. VALIDATED STRICT RESPONSE SCHEMA DICTIONARY
# ============================================================================
HELPDESK_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kb_number": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["kb_number", "url"],
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
    "required": ["message", "citations"],
}


# ============================================================================
# 2. CONFIGURATION & ENVIRONMENT VARIABLES
# ============================================================================
project_endpoint = os.environ.get(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project",
)
agent_name = os.environ.get("AGENT_NAME", "txrh-demoagent-JsonSchema")
prompt_file_path = os.environ.get("PROMPT_FILE_PATH", "prompt.txt")
model_deployment = os.environ.get(
    "FOUNDRY_MODEL_DEPLOYMENT_NAME", "roadie-ranger-foundry-resource/gpt-5.4"
)

# MCP Server Configuration
mcp_server_label = os.environ.get("MCP_SERVER_LABEL", "mcptool")
mcp_server_url = os.environ.get(
    "SERVICENOW_MCP_URL",
    "https://apim-gateway-application-test-dev-txrh-mcp.azure-api.net/mcp-snow/mcp",
)
mcp_project_connection_id = os.environ.get("MCP_PROJECT_CONNECTION_ID", "pass-http-mcp")
mcp_require_approval = os.environ.get("MCP_REQUIRE_APPROVAL", "never")


# ============================================================================
# 3. DEPLOYMENT & VERSIONING ENGINE
# ============================================================================
def build_mcp_tool() -> MCPTool:
    return MCPTool(
        server_label=mcp_server_label,
        server_url=mcp_server_url,
        require_approval=mcp_require_approval,
        project_connection_id=mcp_project_connection_id,
    )


def load_instructions() -> str:
    if not os.path.exists(prompt_file_path):
        logger.warning(f"File '{prompt_file_path}' not found! Creating default prompt template...")
        with open(prompt_file_path, "w", encoding="utf-8") as f:
            f.write(
                "You are StoreHelp, a helpdesk assistant for store employees. "
                "Help them via knowledge base articles or create tickets."
            )

    logger.info(f"Reading system instructions from '{prompt_file_path}'...")
    with open(prompt_file_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def deploy_or_update_agent(instructions: str) -> str:
    """Deploys or updates the agent definition in Azure AI Foundry and sets pipeline variables."""
    if not mcp_project_connection_id:
        logger.error("MCP_PROJECT_CONNECTION_ID is required.")
        sys.exit(1)

    logger.info("Initializing Azure AI Project Client...")
    with AIProjectClient(
        endpoint=project_endpoint, credential=DefaultAzureCredential(), allow_preview=True
    ) as client:
        # Validate MCP Connection
        try:
            client.connections.get(mcp_project_connection_id)
            logger.info(f"Verified MCP Connection: '{mcp_project_connection_id}'")
        except ResourceNotFoundError:
            logger.error(f"MCP connection '{mcp_project_connection_id}' does not exist in Foundry.")
            sys.exit(1)
        except ServiceRequestError as e:
            logger.error(f"Network error reaching Foundry endpoint: {e}")
            sys.exit(1)

        mcp_tool = build_mcp_tool()

        logger.info(f"Deploying agent version for '{agent_name}' with model '{model_deployment}'...")
        try:
            agent_version = client.agents.create_version(
                agent_name=agent_name,
                definition=PromptAgentDefinition(
                    model=model_deployment,
                    instructions=instructions,
                    tools=[mcp_tool],
                    text=PromptAgentDefinitionTextOptions(
                        format=TextResponseFormatJsonSchema(
                            name="helpdesk_response",
                            schema=HELPDESK_RESPONSE_SCHEMA,
                            strict=True,
                        )
                    ),
                ),
            )
            version_str = str(agent_version.version)
            logger.info(f"Successfully deployed agent version: '{version_str}'")

            # Output ADO Pipeline variable and write local version tracker
            print(f"##vso[task.setvariable variable=AgentVersion;]{version_str}")
            with open("version.txt", "w", encoding="utf-8") as f:
                f.write(version_str)

            return version_str

        except Exception as e:
            logger.error(f"Failed to create agent version: {e}")
            sys.exit(1)


# ============================================================================
# 4. ENTRYPOINT
# ============================================================================
def main() -> None:
    instructions = load_instructions()
    deployed_version = deploy_or_update_agent(instructions)
    logger.info(f"🚀 Deployment Complete. Active Version: {deployed_version}")


if __name__ == "__main__":
    main()
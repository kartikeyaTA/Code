import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

# ============================================================================
# 1. PARSED CONNECTION STRING PARAMETERS (FROM YOUR NETWORKING DATA)
# ============================================================================
project_endpoint = "https://306800e0-c3d3-4ba7-80f0-895debabe366.workspace.eastus2.api.azureml.ms"
subscription_id = "a0c64e05-02e0-4758-891f-e6731cfa3357"
resource_group = "ai-chatbot-dev3"
project_name = "ai-project-chat-dev"

new_instructions = "PUSHED VIA CODE! Here goes updated instructions......"
agent_name = "Agent20"

print("Initializing secure execution handshake over private link...")
with AIProjectClient(
        endpoint=project_endpoint,
        subscription_id=subscription_id,
        resource_group_name=resource_group,
        project_name=project_name,
        credential=DefaultAzureCredential(),
        allow_preview=True
) as client:

    try:
        # 1. Fetch the active configuration for your portal agent
        print(f"Fetching current configuration for '{agent_name}'...")
        existing_agent = client.agents.get(agent_name=agent_name)

        # 2. Extract the active prompt definition layout
        current_definition = existing_agent.versions.latest.definition

        # 3. Swap the system instructions cleanly
        if isinstance(current_definition, dict):
            current_definition["instructions"] = new_instructions
        else:
            current_definition.instructions = new_instructions

        # 4. Save and publish the new tracking version
        new_version = client.agents.create_version(
            agent_name=agent_name,
            definition=current_definition
        )

        print(f"\n🎯 SUCCESS! Natively pushed version '{new_version.version}' to '{agent_name}'.")

    except Exception as e:
        print(f"Execution Error: {e}")
        sys.exit(1)
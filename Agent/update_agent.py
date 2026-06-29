import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

# ============================================================================
# 1. PARAMETERS & CONFIGURATION
# ============================================================================
# 🎯 NATIVE PRIVATE CONNECTION STRING: 
# Format: "region;subscription_id;resource_group_name;project_name"
# This bypasses the URL parsing engine entirely while traveling over your VNet link.
project_connection_string = "eastus2;a0c64e05-02e0-4758-891f-e6731cfa3357;ai-chatbot-dev3;ai-project-chat-dev"

new_instructions = "PUSHED VIA CODE! Here goes updated instructions......"
agent_name = "Agent20"

print("Initializing secure private endpoint connection via connection string...")
with AIProjectClient.from_connection_string(
        connection_string=project_connection_string,
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
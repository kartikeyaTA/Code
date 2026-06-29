import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects.models import PromptAgentDefinition

# ============================================================================
# 1. PARAMETERS & CONFIGURATION
# ============================================================================
# 🌟 FIXED: Points directly to your project execution endpoint (not the discovery path)
project_endpoint = "https://306800e0-c3d3-4ba7-80f0-895debabe366.workspace.eastus2.api.azureml.ms/api/projects/ai-project-chat-dev"
DEFAULT_MODEL = "gpt-5"
agent_name = "Agent20"
new_instructions = "PUSHED VIA CODE! Here goes updated instructions......"

# ============================================================================
# 2. SECURE CLIENT INITIALIZATION & ORCHESTRATION LOOP
# ============================================================================
with AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True  # 🌟 FIXED: Allows the SDK to read agents created in the new Foundry portal
) as client:

    try:
        # 3. Fetch the existing agent to get its current settings
        print(f"Fetching current configuration for '{agent_name}'...")
        existing_agent = client.agents.get(agent_name=agent_name)

        # 4. Extract the definition from the latest version
        current_definition = existing_agent.versions.latest.definition

        # 5. Swap out ONLY the instructions (dynamically preserving model and tools)
        if isinstance(current_definition, dict):
            current_definition["instructions"] = new_instructions
        else:
            # If the SDK returns it as an object (e.g., PromptAgentDefinition)
            current_definition.instructions = new_instructions

        # 6. Push the modified definition as a new version
        new_version = client.agents.create_version(
            agent_name=agent_name,
            definition=current_definition
        )

        print(f"\n🎯 Success! Created new version '{new_version.version}' for '{agent_name}'.")
        print("Model and tools remained exactly the same; instructions updated from file.\n")

    except Exception as e:
        print(f"An error occurred while updating the agent: {e}")
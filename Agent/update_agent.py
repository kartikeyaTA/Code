import os
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

# 1. Initialize the Foundry Project Client
project_endpoint = 'https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project'
new_instructions = "PUSHED VIA CODE! Here goes updated instructions......"

with AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential()
) as client:
    # Note: Use the Agent Name here, not the ID
    agent_name = "test-agent-2"

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

        print(f"Success! Created new version '{new_version.version}' for '{agent_name}'.")
        print("Model and tools remained exactly the same; instructions updated from file.")

    except Exception as e:
        print(f"An error occurred while updating the agent: {e}")
import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects.models import PromptAgentDefinition # 🎯 IMPORT FOR CREATION BLUEPRINT


# ============================================================================
# 1. PARAMETERS & CONFIGURATION
# ============================================================================
project_endpoint = 'https://foundry-services-applications16-dev.services.ai.azure.com/api/projects/foundry-project-applications16-dev'
agent_name = "Agent"
prompt_file_path = "prompt.txt"
model_deployment = "apim-model-gateway/gpt-5" 

if not os.path.exists(prompt_file_path):
    print(f"📁 Local Error: '{prompt_file_path}' not found! Creating template file...")
    with open(prompt_file_path, "w", encoding="utf-8") as f:
        f.write("You are an expert AI agent running inside Microsoft Foundry.")

print(f"📖 Reading system instructions from '{prompt_file_path}'...")
with open(prompt_file_path, "r", encoding="utf-8") as file:
    new_instructions = file.read().strip()

# ============================================================================
# 2. SEED TRANSACTION CLIENT ENGINE
# ============================================================================
print("\n🚀 Initializing secure Foundry project client transaction...")
with AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True # 🎯 Crucial to map preview agent layers safely
) as client:

    try:
        # Check Option A: Fetch and update existing configurations
        print(f"🔍 Searching for existing tracking configuration for '{agent_name}'...")
        existing_agent = client.agents.get(agent_name=agent_name)
        
        print(f" -> Found agent target context! Pushing new version layout...")
        current_definition = existing_agent.versions.latest.definition

        if isinstance(current_definition, dict):
            current_definition["instructions"] = new_instructions
        else:
            current_definition.instructions = new_instructions

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
        
        # 🎯 Correct method layout using native definition wrappers:
        new_agent_version = client.agents.create_version(
            agent_name=agent_name,
            definition=PromptAgentDefinition(
                model=model_deployment,
                instructions=new_instructions
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
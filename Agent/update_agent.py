import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects.models import PromptAgentDefinition

# 1. Structural parameters explicitly mapped to your architecture details
# 🌟 FIXED: Swapped to the native v2.x Foundry endpoint format matching your private DNS zone
ENDPOINT_URL = "https://ai-hub-chat-dev.privatelink.services.ai.azure.com/api/projects/ai-project-chat-dev"
DEFAULT_MODEL = "o4-mini-deployment"
AGENT_NAME = "chat-dev-agent"
PROMPT_FILE_PATH = "prompt.txt"

# 2. Local Safety Check: Ensure the local workspace prompt file exists
if not os.path.exists(PROMPT_FILE_PATH):
    print(f"ERROR: Local prompt definition file not found at path: {PROMPT_FILE_PATH}")
    sys.exit(1)

with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
    new_instructions = f.read().strip()

print(f"Loaded dynamic instructions from '{PROMPT_FILE_PATH}' ({len(new_instructions)} chars).")

# 3. Securely initialize client using v2.x standard constructor within Private VNet
print("Initializing secured connection to project data-plane via Foundry route...")
with AIProjectClient(
    endpoint=ENDPOINT_URL,
    credential=DefaultAzureCredential()
) as client:
    try:
        print(f"Searching for existing agent configuration named '{AGENT_NAME}'...")
        existing_agent = client.agents.get(agent_name=AGENT_NAME)
        
        print(f"Agent found! Synchronizing modified prompt instructions as a new version...")
        # SDK pushes a clean version update to the existing target configuration
        new_version = client.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(
                model=DEFAULT_MODEL,
                instructions=new_instructions,
                tools=[]
            )
        )
        print(f"\n🎯 SUCCESS: Incremented Agent to Version: '{new_version.version}'")
        print(f"🆔 AGENT RESOURCE ID: {existing_agent.id}\n")

    except ResourceNotFoundError:
        print(f"Agent '{AGENT_NAME}' does not exist yet. Running first-time baseline container creation pass...")
        try:
            # Provision container shell using our deployed model configuration
            new_agent = client.agents.create_agent(
                model=DEFAULT_MODEL,
                name=AGENT_NAME,
                instructions=new_instructions,
                tools=[]
            )
            print(f"\n🚀 SUCCESS: Initial baseline agent container '{new_agent.name}' created cleanly.")
            print(f"🆔 AGENT RESOURCE ID: {new_agent.id}\n")
            
        except Exception as create_error:
            print(f"CRITICAL ERROR during baseline agent execution loop: {create_error}")
            sys.exit(1)
            
    except Exception as e:
        print(f"CRITICAL ERROR during infrastructure execution lifecycle: {e}")
        sys.exit(1)
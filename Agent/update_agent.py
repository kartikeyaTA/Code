import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects.models import PromptAgentDefinition

# 1. Structural parameters explicitly mapped to your architecture details
SUBSCRIPTION_ID = "a0c64e05-02e0-4758-891f-e6731cfa3357"
RESOURCE_GROUP = "ai-chatbot-dev3"
PROJECT_NAME = "ai-project-chat-dev"
DEFAULT_MODEL = "o4-mini-deployment"
AGENT_NAME = "test-agent-1"
PROMPT_FILE_PATH = "prompt.txt"

# Assemble the connection string targeting your verified, working AzureML private route
CONNECTION_STRING = f"eastus2.api.azureml.ms;{SUBSCRIPTION_ID};{RESOURCE_GROUP};{PROJECT_NAME}"

# 2. Local Safety Check: Ensure the local workspace prompt file exists
if not os.path.exists(PROMPT_FILE_PATH):
    print(f"ERROR: Local prompt definition file not found at path: {PROMPT_FILE_PATH}")
    sys.exit(1)

with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
    new_instructions = f.read().strip()

print(f"Loaded dynamic instructions from '{PROMPT_FILE_PATH}' ({len(new_instructions)} chars).")

# 3. Securely initialize connection within the Private VNet Plane
print("Initializing secured connection to project data-plane via AzureML route...")
with AIProjectClient.from_connection_string(
    conn_str=CONNECTION_STRING,
    credential=DefaultAzureCredential()
) as client:
    try:
        print(f"Searching for existing agent configuration named '{AGENT_NAME}'...")
        # SDK calls the discovery URL behind the scenes using the working DNS mapping
        existing_agent = client.agents.get(agent_name=AGENT_NAME)
        
        print(f"Agent found! Synchronizing modified prompt instructions as a new version...")
        # SCENARIO A: Agent container exists -> Safe to push a clean version update
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
            # SCENARIO B: Brand new agent -> Provision container shell using our deployed model
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
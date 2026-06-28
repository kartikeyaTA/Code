import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects.models import PromptAgentDefinition

# ============================================================================
# 1. PARAMETERS & CONFIGURATION
# ============================================================================
# 🌟 PERMANENT FIX: Target the explicit private link workspace host matching your 10.0.6.6 endpoint certificate
ENDPOINT_URL = "https://306800e0-c3d3-4ba7-80f0-895debabe366.workspace.eastus2.api.azureml.ms/api/projects/ai-project-chat-dev"
DEFAULT_MODEL = "o4-mini-deployment"
AGENT_NAME = "chat-dev-agent"
PROMPT_FILE_PATH = "prompt.txt"

# ============================================================================
# 2. FILE SYSTEM SAFETY CHECKS
# ============================================================================
if not os.path.exists(PROMPT_FILE_PATH):
    print(f"ERROR: Local prompt definition file not found at path: {PROMPT_FILE_PATH}")
    sys.exit(1)

with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
    new_instructions = f.read().strip()

print(f"Loaded dynamic instructions from '{PROMPT_FILE_PATH}' ({len(new_instructions)} chars).")

# ============================================================================
# 3. SECURE CLIENT INITIALIZATION & ORCHESTRATION LOOP
# ============================================================================
print("Initializing secured connection to project data-plane via Private AzureML Route...")
with AIProjectClient(
    endpoint=ENDPOINT_URL,  # ◄ 🌟 Valid URL passed directly
    credential=DefaultAzureCredential()
) as client:
    try:
        print(f"Searching for existing agent configuration named '{AGENT_NAME}'...")
        existing_agent = client.agents.get(agent_name=AGENT_NAME)
        
        print(f"Agent found! Synchronizing modified prompt instructions as a new version...")
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
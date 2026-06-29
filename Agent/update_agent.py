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
ENDPOINT_URL = "https://306800e0-c3d3-4ba7-80f0-895debabe366.workspace.eastus2.api.azureml.ms/discovery/workspaces/306800e0-c3d3-4ba7-80f0-895debabe366"
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
    endpoint=ENDPOINT_URL,
    credential=DefaultAzureCredential()
) as client:
    
    # Check if the baseline framework shell container exists
    try:
        print(f"Searching for existing agent configuration named '{AGENT_NAME}'...")
        existing_agent = client.agents.get(agent_name=AGENT_NAME)
        print(f"Agent found! Existing Resource ID: {existing_agent.id}")
        print(f"Synchronizing modified prompt instructions as a new version...")
        
    except ResourceNotFoundError:
        print(f"Agent '{AGENT_NAME}' does not exist yet. Running first-time creation pass...")
        
    # Execute the build action using the GA compliant SDK method
    try:
        # 🌟 FIXED: create_version handles both creating the agent shell and incrementing prompt models
        new_version = client.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(
                model=DEFAULT_MODEL,
                instructions=new_instructions,
                tools=[]
            )
        )
        print(f"\n🎯 SUCCESS: Published Agent Version: '{new_version.version}'")
        print(f"🆔 AGENT NAME: {new_version.agent_name}\n")

    except Exception as e:
        print(f"CRITICAL ERROR during agent execution loop: {e}")
        sys.exit(1)
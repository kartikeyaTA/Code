import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.ai.projects.models import PromptAgentDefinition 

# 1. Load Configurations from Environment Variables
PROJECT_ENDPOINT = os.getenv("AZURE_AI_FOUNDRY_ENDPOINT")
AGENT_NAME = os.getenv("AZURE_AI_AGENT_NAME", "test-agent-1")
PROMPT_FILE_PATH = os.getenv("AGENT_PROMPT_FILE", "prompt.txt")

# ◄ ALIGNED: This matches your console snapshot exactly!
DEFAULT_MODEL = os.getenv("AZURE_AI_DEFAULT_MODEL", "o4-mini-deployment") 

if not PROJECT_ENDPOINT:
    print("ERROR: AZURE_AI_FOUNDRY_ENDPOINT environment variable is missing.")
    sys.exit(1)

if not os.path.exists(PROMPT_FILE_PATH):
    print(f"ERROR: Prompt file not found at path: {PROMPT_FILE_PATH}")
    sys.exit(1)

with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
    new_instructions = f.read().strip()

print(f"Loaded instructions from '{PROMPT_FILE_PATH}' ({len(new_instructions)} chars).")

# 2. Run execution client lifecycle
with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential()) as client:
    try:
        print(f"Syncing agent state for '{AGENT_NAME}'...")
        
        # In the modern SDK, create_version handles BOTH creation and updating smoothly
        # provided the model parameter points to a valid deployment name string.
        new_version = client.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(
                model=DEFAULT_MODEL, # Passes validation now!
                instructions=new_instructions,
                tools=[]  
            )
        )
        print(f"🎯 Success! Synchronized Agent Version '{new_version.version}' for '{AGENT_NAME}'.")
        print(f"🆔 AGENT ID: {new_version.id}")

    except Exception as e:
        print(f"CRITICAL ERROR during deployment loop execution: {e}")
        sys.exit(1)
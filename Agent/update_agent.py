import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.ai.projects.models import PromptAgentDefinition 

# 1. Load Configurations from Environment Variables
PROJECT_ENDPOINT = os.getenv("AZURE_AI_FOUNDRY_ENDPOINT")
AGENT_NAME = os.getenv("AZURE_AI_AGENT_NAME", "test-agent-1")
PROMPT_FILE_PATH = os.getenv("AGENT_PROMPT_FILE", "prompt.txt")
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

# 2. Run execution client lifecycle using Azure AI Projects 2.2.0 structural patterns
with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential()) as client:
    try:
        # Check if the agent already exists by fetching its container record
        print(f"Checking for existing agent configuration for '{AGENT_NAME}'...")
        existing_agent = client.agents.get_agent(agent_id=AGENT_NAME)
        
        print(f"Agent found. Pushing modified prompt configuration as a new version...")
        # SCENARIO A: Agent Exists -> Add a version container smoothly
        new_version = client.agents.create_version(
            agent_id=existing_agent.id,
            definition=PromptAgentDefinition(
                model=DEFAULT_MODEL,
                instructions=new_instructions,
                tools=[]  
            )
        )
        print(f"🎯 Success! Synchronized Agent Version '{new_version.version}' for '{AGENT_NAME}'.")
        print(f"🆔 AGENT ID: {new_version.id}")

    except Exception as e:
        error_msg = str(e).lower()
        if "not found" in error_msg or "404" in error_msg:
            print(f"Agent '{AGENT_NAME}' does not exist yet. Initializing brand new baseline object container...")
            
            # SCENARIO B: Agent does not exist -> Use baseline creation to bootstrap it
            new_agent = client.agents.create_agent(
                model=DEFAULT_MODEL,
                name=AGENT_NAME,
                instructions=new_instructions,
                tools=[]
            )
            print(f"🚀 Success! Initial baseline agent container '{new_agent.name}' created cleanly.")
            print(f"🆔 AGENT RESOURCE ID: {new_agent.id}")
        else:
            print(f"CRITICAL ERROR during deployment loop execution: {e}")
            sys.exit(1)
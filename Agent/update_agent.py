import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError

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

# 2. Execute agent lifecycle management
with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential()) as client:
    try:
        print(f"Searching for existing agent configuration named '{AGENT_NAME}'...")
        # Check if agent exists by looking it up
        existing_agent = client.agents.get(agent_name=AGENT_NAME)
        
        print(f"Agent found (ID: {existing_agent.id}). Pushing updated prompt configuration as a new version...")
        # SCENARIO A: Agent exists -> Safe to use create_version
        new_version = client.agents.create_version(
            agent_name=AGENT_NAME,
            definition={
                "model": DEFAULT_MODEL,
                "instructions": new_instructions,
                "tools": []
            }
        )
        print(f"🎯 Success! Synchronized Agent Version '{new_version.version}' for '{AGENT_NAME}'.")
        print(f"🆔 AGENT RESOURCE ID: {existing_agent.id}")

    except ResourceNotFoundError:
        print(f"Agent '{AGENT_NAME}' does not exist yet in this workspace. Executing baseline initialization...")
        try:
            # SCENARIO B: Brand new agent container creation pass
            new_agent = client.agents.create_agent(
                model=DEFAULT_MODEL,
                name=AGENT_NAME,
                instructions=new_instructions,
                tools=[]
            )
            print(f"🚀 Success! Initial baseline agent container '{new_agent.name}' created cleanly.")
            print(f"🆔 AGENT RESOURCE ID: {new_agent.id}")
        except Exception as create_error:
            print(f"CRITICAL ERROR during initial agent creation pass: {create_error}")
            sys.exit(1)
            
    except Exception as e:
        print(f"CRITICAL ERROR during execution loop: {e}")
        sys.exit(1)
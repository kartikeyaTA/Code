import os
import sys
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

# 1. Load Configurations from Environment Variables (Injected by your Pipeline)
PROJECT_ENDPOINT = os.getenv("AZURE_AI_FOUNDRY_ENDPOINT")
AGENT_NAME = os.getenv("AZURE_AI_AGENT_NAME", "test-agent-1")
PROMPT_FILE_PATH = os.getenv("AGENT_PROMPT_FILE", "prompt.txt")
DEFAULT_MODEL = os.getenv("AZURE_AI_DEFAULT_MODEL", "o4-mini")

if not PROJECT_ENDPOINT:
    print("ERROR: AZURE_AI_FOUNDRY_ENDPOINT environment variable is missing.")
    sys.exit(1)

# 2. Read the prompt dynamically from a file tracked in your Git repo
if not os.path.exists(PROMPT_FILE_PATH):
    print(f"ERROR: Prompt file not found at path: {PROMPT_FILE_PATH}")
    sys.exit(1)

with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
    new_instructions = f.read().strip()

print(f"Loaded dynamic instructions from '{PROMPT_FILE_PATH}' ({len(new_instructions)} chars).")

# 3. Initialize the Foundry Project Client
with AIProjectClient(
    endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential()
) as client:
    
    try:
        # Check if the agent already exists
        print(f"Checking for existing agent configuration for '{AGENT_NAME}'...")
        existing_agent = client.agents.get(agent_name=AGENT_NAME)
        
        # SCENARIO A: Agent Exists -> Update Instructions dynamically preserving settings
        print(f"Agent found. Extracting definition parameters...")
        current_definition = existing_agent.versions.latest.definition

        if isinstance(current_definition, dict):
            current_definition["instructions"] = new_instructions
        else:
            current_definition.instructions = new_instructions

        print(f"Pushing modified prompt configuration as a new version...")
        # ◄ FIXED: Method name corrected to create_agent_version
        new_version = client.agents.create_agent_version(
            agent_name=AGENT_NAME,
            definition=current_definition
        )
        print(f"🎯 Success! Created new version '{new_version.version}' for '{AGENT_NAME}'.")
        print(f"🆔 AGENT RESOURCE ID: {existing_agent.id}")

    except Exception as e:
        # SCENARIO B: Agent does not exist (First deployment fallback)
        error_msg = str(e).lower()
        if "not found" in error_msg or "404" in error_msg:
            print(f"Agent '{AGENT_NAME}' does not exist yet. Performing initial baseline creation...")
            
            # ◄ FIXED: Method name corrected to create_agent
            new_agent = client.agents.create_agent(
                agent_name=AGENT_NAME,
                model=DEFAULT_MODEL,
                instructions=new_instructions,
                tools=[]  
            )
            print(f"🚀 Success! Initial baseline agent '{AGENT_NAME}' created cleanly.")
            print(f"🆔 AGENT RESOURCE ID: {new_agent.id}")
        else:
            print(f"CRITICAL ERROR during deployment loop: {e}")
            sys.exit(1)
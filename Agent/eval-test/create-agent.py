import json
import os
import sys
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, FunctionTool

# ============================================================================
# CONFIGURATION
# ============================================================================
PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
MAIN_AGENT_NAME = "txrh-demoagent-2-copy"
CANDIDATE_AGENT_NAME = f"{MAIN_AGENT_NAME}-eval-candidate"
PROMPT_FILE_PATH = "prompt.txt"
TOOLS_FILE_PATH = "tools_schema.json"


def load_tools_from_json(file_path: str):
    """Loads offline tool definitions from JSON and converts them to FunctionTools."""
    if not os.path.exists(file_path):
        print(f"⚠️ Tool file '{file_path}' not found! Proceeding with empty tools list.")
        return []

    print(f"📖 Reading tool schemas from '{file_path}'...")
    with open(file_path, "r", encoding="utf-8") as f:
        raw_tools = json.load(f)

    tools_list = []
    for t in raw_tools:
        tools_list.append(
            FunctionTool(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t.get("parameters", {})
            )
        )
    return tools_list


def main():
    if not os.path.exists(PROMPT_FILE_PATH):
        print(f"❌ '{PROMPT_FILE_PATH}' not found.")
        sys.exit(1)

    with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
        new_instructions = f.read().strip()

    tools_list = load_tools_from_json(TOOLS_FILE_PATH)
    credential = DefaultAzureCredential()

    print("🚀 Connecting to Foundry project...")
    with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential, allow_preview=True) as client:
        # 1. Fetch main agent to inherit model deployment
        try:
            main_agent = client.agents.get(agent_name=MAIN_AGENT_NAME)
            current_definition = main_agent.versions.latest.definition
            model_deployment = getattr(current_definition, "model", None) or current_definition.get("model")
            print(f"ℹ️ Inherited model '{model_deployment}' from main agent '{MAIN_AGENT_NAME}'.")
        except ResourceNotFoundError:
            print(f"❌ Main agent '{MAIN_AGENT_NAME}' not found. Cannot determine model deployment.")
            sys.exit(1)

        # 2. Create candidate agent version with prompt and tool schemas
        print(f"🛠️ Creating/Updating candidate agent '{CANDIDATE_AGENT_NAME}'...")
        candidate_version = client.agents.create_version(
            agent_name=CANDIDATE_AGENT_NAME,
            definition=PromptAgentDefinition(
                model=model_deployment,
                instructions=new_instructions,
                tools=tools_list
            )
        )

        version_str = candidate_version.version
        print(f"🎯 Candidate agent created: version '{version_str}'")

        # 3. Expose variables to Azure DevOps
        print(f"##vso[task.setvariable variable=CandidateAgentName;]{CANDIDATE_AGENT_NAME}")
        print(f"##vso[task.setvariable variable=CandidateAgentVersion;]{version_str}")


if __name__ == "__main__":
    main()
import os
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition

# 1. WORKSPACE AND FILE CONFIGURATION
PROJECT_ENDPOINT = "https://ai-hub-chat-dev.services.ai.azure.com/api/projects/ai-project-chat-dev"
DEPLOYMENT_NAME = "o4-mini-deployment"
AGENT_NAME = "chat-dev-agent"
PROMPT_FILE_PATH = "prompt.txt"

def main():
    # 2. Extract authentication tokens from active login state
    credential = DefaultAzureCredential()

    # 3. Read system instructions dynamically from your text file
    print(f"📄 Loading system instructions from: {PROMPT_FILE_PATH}")
    try:
        with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as file:
            system_instructions = file.read().strip()
    except FileNotFoundError:
        print(f"❌ Error: The file '{PROMPT_FILE_PATH}' was not found. Please create it first.")
        return

    print(f"Connecting to your Azure AI Foundry Project: {PROJECT_ENDPOINT}")
    
    # 4. Establish connection context to your target Project Hub
    with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential) as project_client:
        
        print(f"Deploying Agent configuration mapping to engine: {DEPLOYMENT_NAME}...")
        
        # 5. Provision and upload the agent instance using the file content
        agent = project_client.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(
                model=DEPLOYMENT_NAME,
                instructions=system_instructions,
            ),
        )

        print("\n" + "="*60)
        print("🚀 SUCCESS: FOUNDRY AGENT CREATED AND RUNNING!")
        print(f"-> Agent Unique Resource ID: {agent.id}")
        print(f"-> Project Portal Version: {agent.version}")
        print("="*60 + "\n")

        print("Opening conversational thread validation loop...")
        with project_client.get_openai_client() as openai_client:
            conversation = openai_client.conversations.create()

            response = openai_client.responses.create(
                conversation=conversation.id,
                extra_body={"agent": {"name": AGENT_NAME, "type": "agent_reference"}},
                input="Hello Agent! Verify deployment status and confirm your active model connection profile.",
            )

            print(f"\n🤖 Live Agent Output Response:\n{response.output_text}\n")

if __name__ == "__main__":
    main()

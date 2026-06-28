import os
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition

def main():
    # 1. Read variables directly from the pipeline runtime export
    connection_string = os.environ["AZURE_AI_CONNECTION_STRING"]
    agent_name = os.environ["AZURE_AI_AGENT_NAME"]
    model_name = os.environ["AZURE_AI_DEFAULT_MODEL"]
    prompt_file = os.environ["AGENT_PROMPT_FILE"]

    # 2. Read prompt instructions from file artifact
    print(f"📄 Loading system instructions from: {prompt_file}")
    with open(prompt_file, "r", encoding="utf-8") as f:
        system_instructions = f.read().strip()

    # 3. Authenticate using the pipeline Managed Identity
    credential = DefaultAzureCredential()

    print(f"Connecting to Azure AI Foundry via connection string context...")
    
    # 4. Use the connection string factory to bypass URL blocks
    with AIProjectClient.from_connection_string(
        conn_str=connection_string, 
        credential=credential
    ) as project_client:
        
        print(f"Deploying Agent configuration mapping to engine: {model_name}...")
        agent = project_client.agents.create_version(
            agent_name=agent_name,
            definition=PromptAgentDefinition(
                model=model_name,
                instructions=system_instructions,
            ),
        )
        print(f"🚀 SUCCESS! Sync complete. Agent ID: {agent.id} (Version: {agent.version})")

if __name__ == "__main__":
    main()

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
AGENT_NAME = "txrh-demoagent-2-copy1352324"

with AIProjectClient(
    endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential(),
    allow_preview=True
) as project_client:

    # 1. Fetch default active agent metadata
    agent = project_client.agents.get(agent_name=AGENT_NAME)

    print("=" * 60)
    print("🔍 ACTIVE BACKEND AGENT RESOLUTION")
    print("=" * 60)
    print(f"📌 Target Agent Name : {agent.name}")
    print(f"📌 Active Agent ID   : {agent.id}")
    print(f"📌 Active Version    : {getattr(agent, 'version', 'N/A')}")
    print("-" * 60)

    # 2. Test execution against non-versioned endpoint
    print("🚀 Invoking default endpoint...")
    agent_oai = project_client.get_openai_client(agent_name=AGENT_NAME)
    response = agent_oai.responses.create(input="Who is the king?")

    print(f"💬 Response Output:\n{response.output_text}")
    print("=" * 60)
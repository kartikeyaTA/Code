from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
AGENT_NAME = "txrh-demoagent-2-copy1352324"

with AIProjectClient(
    endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential(),
    allow_preview=True
) as project_client:

    agent = project_client.agents.get(agent_name=AGENT_NAME)

    print(f"Agent: {agent.name}  (state: {agent.state})")
    print(f"Latest created version: {agent.versions.latest.version}")

    endpoint_cfg = agent.agent_endpoint
    rules = None
    if endpoint_cfg and endpoint_cfg.version_selector:
        rules = endpoint_cfg.version_selector.version_selection_rules

    if not rules:
        # No explicit routing rule set -> the endpoint defaults to serving "latest"
        print(f"✅ No explicit version pin — endpoint serves the latest version: {agent.versions.latest.version}")
    else:
        print("✅ Explicit version routing is configured:")
        for rule in rules:
            print(f"   version={rule.agent_version}  traffic={rule.traffic_percentage}%")
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
AGENT_NAME = "txrh-demoagent-2-copy1352324"

with AIProjectClient(
    endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential(),
    allow_preview=True
) as project_client:

    # 1. Fetch current agent control plane metadata
    agent = project_client.agents.get(agent_name=AGENT_NAME)
    
    endpoint_cfg = agent.agent_endpoint
    rules = None
    if endpoint_cfg and endpoint_cfg.version_selector:
        rules = endpoint_cfg.version_selector.version_selection_rules
    
    active_version = None

    if not rules:
        # Default policy ("Always use latest")
        active_version = str(agent.versions.latest.version)
        print(f"✅ No explicit routing pin — using latest version: {active_version}")
    else:
        print("✅ Explicit version routing configured:")
        for rule in rules:
            print(f"   version={rule.agent_version}  traffic={rule.traffic_percentage}%")
        
        # Pick the version receiving traffic (> 0%)
        active_rule = max(rules, key=lambda r: getattr(r, "traffic_percentage", 0))
        active_version = str(active_rule.agent_version)

    print("=" * 60)
    print(f"📌 Selected Active Version: {active_version}")
    print("=" * 60)

    # 2. Execute request against active version
    agent_oai = project_client.get_openai_client()
    
    response = agent_oai.responses.create(
        input="Who is the king?",
        extra_body={
            "agent_reference": {
                "name": AGENT_NAME,
                "version": active_version,
                "type": "agent_reference"
            }
        }
    )

    print(f"💬 Response Output:\n{response.output_text}")
    print("=" * 60)
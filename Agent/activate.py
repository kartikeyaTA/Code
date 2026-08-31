from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointConfig,
    FixedRatioVersionSelectionRule,
    VersionSelector,
)

PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
AGENT_NAME = "txrh-demoagent-2-copy1352324"
TARGET_VERSION = "5"

with AIProjectClient(
    endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential(),
    allow_preview=True
) as project_client:

    # 1. Confirm the version exists before activating it
    version_details = project_client.agents.get_version(
        agent_name=AGENT_NAME,
        agent_version=TARGET_VERSION
    )
    print(f"📌 Found version {version_details.id} — status: {version_details.status}")

    # 2. Route 100% of traffic on the stable agent endpoint to this version
    endpoint_config = AgentEndpointConfig(
        version_selector=VersionSelector(
            version_selection_rules=[
                FixedRatioVersionSelectionRule(
                    agent_version=TARGET_VERSION,
                    traffic_percentage=100,
                ),
            ]
        ),
    )

    patched_agent = project_client.agents.update_details(
        agent_name=AGENT_NAME,
        agent_endpoint=endpoint_config,
    )

    print(f"✅ Agent '{patched_agent.name}' is now serving version {TARGET_VERSION} at its stable endpoint.")
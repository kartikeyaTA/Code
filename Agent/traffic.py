import os
import sys
import uuid
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects.models import (
    PromptAgentDefinition,
    AgentEndpointConfig,
    FixedRatioVersionSelectionRule,
    VersionSelector,
)

# ============================================================================
# 1. PARAMETERS & CONFIGURATION
# ============================================================================
project_endpoint = 'https://voice-open-ai-txrh.services.ai.azure.com/api/projects/voice-open-ai-txrh'
agent_name = "hyb"
prompt_file_path = "Hi"
model_deployment = "gpt-5"

if not os.path.exists(prompt_file_path):
    print(f"📁 Local Error: '{prompt_file_path}' not found! Creating template file...")
    with open(prompt_file_path, "w", encoding="utf-8") as f:
        f.write("You are an expert AI agent running inside Microsoft Foundry.")

print(f"📖 Reading system instructions from '{prompt_file_path}'...")
with open(prompt_file_path, "r", encoding="utf-8") as file:
    new_instructions = file.read().strip()


# ============================================================================
# HELPERS
# ============================================================================
def get_active_rules(agent):
    """Returns current version selection rules from the agent endpoint."""
    endpoint_cfg = agent.agent_endpoint
    if endpoint_cfg and endpoint_cfg.version_selector:
        return list(endpoint_cfg.version_selector.version_selection_rules or [])
    return None


def print_routing(label, agent, rules):
    print(f"{label}")
    print(f"   Latest created version: {agent.versions.latest.version}")
    if not rules:
        print(f"   No explicit routing — endpoint tracks latest ({agent.versions.latest.version})")
    else:
        for r in rules:
            traffic = getattr(r, 'traffic_percentage', 'N/A')
            version = getattr(r, 'agent_version', 'N/A')
            print(f"   version={version}  traffic={traffic}%")


def route_50_50_latest_and_previous(client, agent_name):
    """Fetches all versions, identifies latest & latest-1, and sends explicit REST payload
    containing 'kind': 'experiment' to bypass Pydantic/SDK serialization drops."""

    versions_page = client.agents.list_versions(agent_name=agent_name)
    all_versions = [v.version for v in versions_page]

    try:
        all_versions.sort(key=lambda x: int(x))
    except ValueError:
        all_versions.sort()

    print(f"📚 Available versions for '{agent_name}': {all_versions}")

    if len(all_versions) < 2:
        print(f"⚠️ Warning: Need at least 2 versions to perform 50-50 split.")
        if len(all_versions) == 1:
            latest = all_versions[0]
            endpoint_config = {
                "version_selector": {
                    "kind": "fixed_ratio",
                    "version_selection_rules": [
                        {"agent_version": latest, "traffic_percentage": 100}
                    ]
                }
            }
            return client.agents.update_details(agent_name=agent_name, agent_endpoint=endpoint_config)
        return None

    latest = all_versions[-1]
    previous = all_versions[-2]

    print(f"🔀 Configuring 50/50 split between:")
    print(f"   - Latest version    (v{latest}): 50%")
    print(f"   - Previous version (v{previous}): 50%")

    # Explicit raw dictionary payload passing both 'kind' and 'type' guarantees 
    # that Azure AI REST endpoint deserializes this as an Experiment selector.
    endpoint_config = {
        "version_selector": {
            "kind": "experiment",
            "type": "experiment",
            "experiment_id": f"exp-{agent_name}-v{previous}-v{latest}",
            "salt": str(uuid.uuid4()),
            "version_selection_rules": [
                {"agent_version": str(latest), "traffic_percentage": 50},
                {"agent_version": str(previous), "traffic_percentage": 50},
            ],
        }
    }

    return client.agents.update_details(agent_name=agent_name, agent_endpoint=endpoint_config)


def expose_version_to_pipeline(version):
    if not version:
        print("❌ Failed to resolve a valid agent version string.")
        sys.exit(1)
    print(f"##vso[task.setvariable variable=AgentVersion;]{version}")
    with open("version.txt", "w", encoding="utf-8") as f:
        f.write(str(version))
    print(f"🚀 Successfully exposed version '{version}' to pipeline context.")


# ============================================================================
# 2. SEED TRANSACTION CLIENT ENGINE
# ============================================================================
print("\n🚀 Initializing secure Foundry project client transaction...")
with AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True
) as client:

    try:
        print(f"🔍 Searching for existing tracking configuration for '{agent_name}'...")
        existing_agent = client.agents.get(agent_name=agent_name)

        rules_before = get_active_rules(existing_agent)
        print_routing("📋 Routing BEFORE this run:", existing_agent, rules_before)

        print(" -> Found agent target context! Pushing new version layout...")
        current_definition = existing_agent.versions.latest.definition
        if isinstance(current_definition, dict):
            current_definition["instructions"] = new_instructions
        else:
            current_definition.instructions = new_instructions

        new_version = client.agents.create_version(
            agent_name=agent_name,
            definition=current_definition
        )
        print(f"✅ Created new version: {new_version.version}")

        # Update traffic split using raw dictionary payload
        patched_agent = route_50_50_latest_and_previous(client, agent_name)

        confirmed_agent = client.agents.get(agent_name=agent_name)
        rules_after = get_active_rules(confirmed_agent)
        print_routing("📋 Routing AFTER this run:", confirmed_agent, rules_after)

        expose_version_to_pipeline(new_version.version)

    except ResourceNotFoundError:
        print(f"\n⚠️ Asset Context Not Found: '{agent_name}' does not exist.")
        print(f"🛠️ Instantiating creation factory layout using model '{model_deployment}'...")

        new_agent_version = client.agents.create_version(
            agent_name=agent_name,
            definition=PromptAgentDefinition(
                model=model_deployment,
                instructions=new_instructions
            )
        )

        endpoint_config = {
            "version_selector": {
                "kind": "fixed_ratio",
                "version_selection_rules": [
                    {"agent_version": str(new_agent_version.version), "traffic_percentage": 100}
                ]
            }
        }

        patched_agent = client.agents.update_details(
            agent_name=agent_name,
            agent_endpoint=endpoint_config,
        )

        confirmed_agent = client.agents.get(agent_name=agent_name)
        rules_after = get_active_rules(confirmed_agent)
        print_routing("📋 Routing AFTER creation:", confirmed_agent, rules_after)

        expose_version_to_pipeline(new_agent_version.version)

    except Exception as e:
        print(f"\n❌ Execution Exception encountered: {e}")
        sys.exit(1)
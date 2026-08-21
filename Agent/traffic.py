import os
import sys
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
project_endpoint = 'https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project'
agent_name = "txrh-demoagent-2-copy1352324"
prompt_file_path = "prompt.txt"
model_deployment = "roadie-ranger-foundry-resource/gpt-5.4"

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
    """Returns the current version_selection_rules for an agent, or None if
    no explicit routing is configured (i.e. the endpoint just tracks 'latest')."""
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
            print(f"   version={r.agent_version}  traffic={r.traffic_percentage}%")


def deploy_dark(client, agent_name, agent_before, new_version):
    """Adds `new_version` to the routing table at 0% traffic, WITHOUT touching
    whatever is currently serving traffic. If there was no explicit routing
    before (endpoint was tracking 'latest'), the previously-latest version is
    pinned explicitly at 100% first, so creating this new version doesn't
    silently make it live by becoming the new 'latest'."""
    existing_rules = get_active_rules(agent_before)

    if existing_rules is None:
        # No explicit rules yet -> endpoint was tracking "latest", which was
        # agent_before.versions.latest.version BEFORE this new version existed.
        # Pin that one explicitly so it keeps serving traffic.
        previously_active_version = agent_before.versions.latest.version
        print(f"   ℹ️ No prior explicit routing found — pinning previously-active "
              f"version {previously_active_version} at 100% before adding the new one.")
        existing_rules = [
            FixedRatioVersionSelectionRule(
                agent_version=previously_active_version,
                traffic_percentage=100,
            )
        ]
        print(f"   ℹ️ No prior explicit routing found — pinning previously-active "
              f"version {previously_active_version} at 100% before adding the new one.")

    # Drop any stale rule for this exact version (e.g. re-running the script),
    # then append the new version at 0% traffic.
    merged_rules = [r for r in existing_rules if r.agent_version != new_version]
    merged_rules.append(
        FixedRatioVersionSelectionRule(agent_version=new_version, traffic_percentage=0)
    )

    endpoint_config = AgentEndpointConfig(
        version_selector=VersionSelector(version_selection_rules=merged_rules)
    )

    return client.agents.update_details(agent_name=agent_name, agent_endpoint=endpoint_config)


def expose_version_to_pipeline(version):
    if not version:
        print("❌ Failed to resolve a valid agent version string.")
        sys.exit(1)
    print(f"##vso[task.setvariable variable=AgentVersion;]{version}")
    with open("version.txt", "w", encoding="utf-8") as f:
        f.write(str(version))
    print(f"🚀 Successfully exposed version '{version}' to the pipeline agent context.")


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
        # ------------------------------------------------------------------
        # Option A: agent exists -> create a new (dark-launched) version
        # ------------------------------------------------------------------
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
        TARGET_VERSION = new_version.version

        patched_agent = deploy_dark(client, agent_name, existing_agent, TARGET_VERSION)

        print(f"✅ Agent '{patched_agent.name}': new version {TARGET_VERSION} created and pinned at 0% traffic.")
        print(f"🎯 UPDATE SUCCESS: Pushed version '{new_version.version}' to '{agent_name}'.")

        # Verify: re-fetch and confirm the previously active version is STILL active
        confirmed_agent = client.agents.get(agent_name=agent_name)
        print(f"📌 Confirmed active version after this run: {confirmed_agent.versions.latest.version}")
        rules_after = get_active_rules(confirmed_agent)
        print_routing("📋 Routing AFTER this run:", confirmed_agent, rules_after)

        expose_version_to_pipeline(new_version.version)

    except ResourceNotFoundError:
        # ------------------------------------------------------------------
        # Option B: agent does not exist -> create it from scratch.
        # First version has nothing to protect, so it CAN start at 0% too if
        # you want a strict "nothing is ever auto-active" policy — flip
        # FIRST_VERSION_STARTS_LIVE to True if a brand-new agent should be
        # live immediately instead.
        # ------------------------------------------------------------------
        FIRST_VERSION_STARTS_LIVE = False

        print(f"\n⚠️ Asset Context Not Found: '{agent_name}' does not exist.")
        print(f"🛠️ Instantiating creation factory layout using model '{model_deployment}'...")

        new_agent_version = client.agents.create_version(
            agent_name=agent_name,
            definition=PromptAgentDefinition(
                model=model_deployment,
                instructions=new_instructions
            )
        )
        TARGET_VERSION = new_agent_version.version

        endpoint_config = AgentEndpointConfig(
            version_selector=VersionSelector(
                version_selection_rules=[
                    FixedRatioVersionSelectionRule(
                        agent_version=TARGET_VERSION,
                        traffic_percentage=100 if FIRST_VERSION_STARTS_LIVE else 0,
                    ),
                ]
            ),
        )
        patched_agent = client.agents.update_details(
            agent_name=agent_name,
            agent_endpoint=endpoint_config,
        )

        print(f"\n🎯 CREATION SUCCESS: Brand new agent created directly via code context.")
        print(f" -> Assigned Initial Tracking Version: {new_agent_version.version} "
              f"({'LIVE' if FIRST_VERSION_STARTS_LIVE else 'dark, 0% traffic'})")

        confirmed_agent = client.agents.get(agent_name=agent_name)
        rules_after = get_active_rules(confirmed_agent)
        print_routing("📋 Routing AFTER creation:", confirmed_agent, rules_after)

        expose_version_to_pipeline(new_agent_version.version)

    except Exception as e:
        print(f"\n❌ Execution Exception encountered: {e}")
        sys.exit(1)
import os
import sys
import traceback
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

# ============================================================================
# UNTOUCHED CONFIGURATION SECTION
# ============================================================================
project_endpoint = "https://306800e0-c3d3-4ba7-80f0-895debabe366.workspace.eastus2.api.azureml.ms/discovery/workspaces/306800e0-c3d3-4ba7-80f0-895debabe366"
new_instructions = "PUSHED VIA CODE! Here goes updated instructions......"
agent_name = "Agent20"

print("=" * 80)
print("🚀 STARTING AGENT OPERATIONS DIAGNOSTIC SUITE")
print("=" * 80)
print(f"[DEBUG] Target Endpoint URL: {project_endpoint}")
print(f"[DEBUG] Target Agent Name  : {agent_name}")
print(f"[DEBUG] Local Python Ver    : {sys.version}")

# ============================================================================
# EXECUTION LAYER WITH VERBOSE DIAGNOSTICS
# ============================================================================
try:
    print("\n[DEBUG] Instantiating AIProjectClient with DefaultAzureCredential...")
    with AIProjectClient(
            endpoint=project_endpoint,
            credential=DefaultAzureCredential()
    ) as client:
        
        print("[DEBUG] Client established successfully. Inspecting client components...")
        print(f"[DEBUG] Sub-operations present on client.agents: {dir(client.agents)}")

        try:
            # 3. Fetch the existing agent to get its current settings
            print(f"\n[DEBUG] Executing client.agents.get(agent_name='{agent_name}')...")
            existing_agent = client.agents.get(agent_name=agent_name)
            
            print("=" * 40)
            print("🔍 LIVE AGENT FOUND OBJECT INSPECTION")
            print("=" * 40)
            print(f" -> Raw Agent Object Type : {type(existing_agent)}")
            print(f" -> Agent ID              : {getattr(existing_agent, 'id', 'NOT FOUND')}")
            print(f" -> Agent Name Data       : {getattr(existing_agent, 'name', 'NOT FOUND')}")
            print(f" -> Versions Attribute    : {hasattr(existing_agent, 'versions')}")
            
            if hasattr(existing_agent, 'versions'):
                print(f" -> Available Version keys: {dir(existing_agent.versions)}")
                print(f" -> Latest Version Object : {existing_agent.versions.latest}")
            print("=" * 40)

            # 4. Extract the definition from the latest version
            print("\n[DEBUG] Extracting current definition context...")
            current_definition = existing_agent.versions.latest.definition
            print(f"[DEBUG] Type of current_definition: {type(current_definition)}")
            print(f"[DEBUG] Raw Definition properties: {dir(current_definition)}")

            # 5. Swap out ONLY the instructions (dynamically preserving model and tools)
            if isinstance(current_definition, dict):
                print("[DEBUG] Detected definition structure format: standard Python dictionary (dict)")
                print(f"[DEBUG] Pre-existing instructions: {current_definition.get('instructions', 'None')}")
                current_definition["instructions"] = new_instructions
                print("[DEBUG] Dictionary updated successfully.")
            else:
                print("[DEBUG] Detected definition structure format: SDK Class / Object Model")
                print(f"[DEBUG] Pre-existing instructions: {getattr(current_definition, 'instructions', 'None')}")
                current_definition.instructions = new_instructions
                print("[DEBUG] Object model attribute adjusted successfully.")

            # 6. Push the modified definition as a new version
            print(f"\n[DEBUG] Executing client.agents.create_version(agent_name='{agent_name}', definition=...)")
            print(f"[DEBUG] Passing definition content summary: {current_definition}")
            
            new_version = client.agents.create_version(
                agent_name=agent_name,
                definition=current_definition
            )

            print("\n" + "=" * 40)
            print(f"🎉 SUCCESS! Created new version '{getattr(new_version, 'version', 'N/A')}' for '{agent_name}'.")
            print("Model and tools remained exactly the same; instructions updated from file.")
            print("=" * 40)

        except Exception as inner_e:
            print("\n❌ CRITICAL EXCEPTION DURING AGENT CONTEXT LIFECYCLE")
            print("-" * 60)
            print(f"Error Type   : {type(inner_e).__name__}")
            print(f"Error Message: {inner_e}")
            print("\n📋 Full Internal Execution Traceback:")
            traceback.print_exc(file=sys.stdout)
            print("-" * 60)

except Exception as outer_e:
    print("\n❌ CRITICAL EXCEPTION DURING CLIENT INITIALIZATION")
    print("-" * 60)
    print(f"Outer Error Type   : {type(outer_e).__name__}")
    print(f"Outer Error Message: {outer_e}")
    traceback.print_exc(file=sys.stdout)
    print("-" * 60)

print("\n" + "=" * 80)
print("🏁 DIAGNOSTIC PIPELINE SCRIPT TERMINATED")
print("=" * 80)
import os
import sys
import logging
import urllib.parse
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects.models import PromptAgentDefinition

# ============================================================================
# 0. DEEP DIAGNOSTIC DEBUG LOGGING SETUP
# ============================================================================
# This configures standard out to dump low-level Azure network pipeline handshakes
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout
)
# Force Azure Core's HTTP logging policy to reveal downstream connection URLs
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.DEBUG)

# ============================================================================
# 1. PARAMETERS & CONFIGURATION
# ============================================================================
# 🎯 DEFENSIVE FIX: .strip() automatically scrubs hidden spaces or '\r\n' copy-paste artifacts
project_endpoint = 'https://foundry-services-applications15-dev.services.ai.azure.com/api/projects/foundry-project-applications15-dev'.strip().replace('\r', '').replace('\n', '')
agent_name = "Agent2"
prompt_file_path = "prompt.txt"
model_deployment = "apim-model-gateway1/gpt-5" 

if not os.path.exists(prompt_file_path):
    print(f"📁 Local Error: '{prompt_file_path}' not found! Creating template file...")
    with open(prompt_file_path, "w", encoding="utf-8") as f:
        f.write("You are an expert AI agent running inside Microsoft Foundry.")

print(f"📖 Reading system instructions from '{prompt_file_path}'...")
with open(prompt_file_path, "r", encoding="utf-8") as file:
    new_instructions = file.read().strip()

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
        # Check Option A: Fetch and update existing configurations
        print(f"🔍 Searching for existing tracking configuration for '{agent_name}'...")
        existing_agent = client.agents.get(agent_name=agent_name)
        
        print(f" -> Found agent target context! Pushing new version layout...")
        current_definition = existing_agent.versions.latest.definition

        if isinstance(current_definition, dict):
            current_definition["instructions"] = new_instructions
        else:
            current_definition.instructions = new_instructions

        new_version = client.agents.create_version(
            agent_name=agent_name,
            definition=current_definition
        )
        print(f"\n🎯 UPDATE SUCCESS: Pushed version '{new_version.version}' to '{agent_name}'.")
        if new_version.version:
            # 🎯 This special print command creates an Azure DevOps variable named $(AgentVersion) dynamically
            print(f"##vso[task.setvariable variable=AgentVersion;]{new_version.version}")
            with open("version.txt", "w", encoding="utf-8") as f:
                f.write(str(new_version.version))
            print(f"🚀 Successfully exposed version '{new_version.version}' to the pipeline agent context.")
        else:
            print("❌ Failed to resolve a valid agent version string.")
            sys.exit(1)

    except ResourceNotFoundError:
        # Check Option B: Fallback and trigger creation engine safely via Agent Definition wrapper
        print(f"\n⚠️ Asset Context Not Found: '{agent_name}' does not exist.")
        print(f"🛠️ Instantiating creation factory layout using model '{model_deployment}'...")
        
        new_agent_version = client.agents.create_version(
            agent_name=agent_name,
            definition=PromptAgentDefinition(
                model=model_deployment,
                instructions=new_instructions
            )
        )
        print(f"\n🎯 CREATION SUCCESS: Brand new agent created directly via code context.")
        print(f" -> Assigned Initial Tracking Version: {new_agent_version.version}")
        if new_agent_version.version:
            print(f"##vso[task.setvariable variable=AgentVersion;]{new_agent_version.version}")
            with open("version.txt", "w", encoding="utf-8") as f:
                f.write(str(new_agent_version.version))
            print(f"🚀 Successfully exposed version '{new_agent_version.version}' to the pipeline agent context.")
        else:
            print("❌ Failed to resolve a valid agent version string.")
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ Execution Exception encountered: {e}")
        
        # 🎯 ADVANCED DIAGNOSTIC: Intercept and decode hidden downstream request loops
        if hasattr(e, 'request') and e.request:
            print(f"\n======================================================================")
            print(f"🌐 DETAILED NETWORK FAILURE REPORT")
            print(f"======================================================================")
            print(f"🔗 Target URL Attempted: {e.request.url}")
            
            try:
                failed_host = urllib.parse.urlparse(e.request.url).hostname
                print(f"🔍 Evaluated Hostname   : {failed_host}")
                print(f"🔤 Literal String Output: {repr(failed_host)}")
                print(f"\n💡 Troubleshooting Hint:")
                if "privatelink" in str(failed_host) or "azureml.ms" in str(failed_host):
                    print("👉 This is an internal Azure network resource path. Your local machine cannot\n"
                          "   resolve it because the target Foundry project is isolated inside a Private VNet.")
                elif failed_host is None:
                    print("👉 The destination endpoint format could not be understood by the HTTP handler.")
            except Exception as parsing_err:
                print(f"Could not parse URL components: {parsing_err}")
            print(f"======================================================================\n")
            
        sys.exit(1)
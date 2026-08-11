import os
import sys
import json
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.core.exceptions import ResourceNotFoundError, ServiceRequestError
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, MCPTool

# Azure AI Evaluation Imports
from azure.ai.evaluation import evaluate, RelevanceEvaluator, GroundednessEvaluator

pip install azure-ai-evaluation

# ============================================================================
# 1. PARAMETERS & CONFIGURATION
# ============================================================================
project_endpoint = os.environ.get(
    "PROJECT_ENDPOINT", 
    "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
)

agent_name = os.environ.get("AGENT_NAME", "txrh-demoagent-2-copy-1234")
prompt_file_path = os.environ.get("PROMPT_FILE_PATH", "prompt.txt")
model_deployment = os.environ.get("MODEL_DEPLOYMENT", "gpt-5.4")

# Connection name in Foundry Studio (Connected resources) for Azure OpenAI / APIM
aoai_connection_name = os.environ.get("AOAI_CONNECTION_NAME", "txrh-project")

# Evaluation Configuration
eval_dataset_path = os.environ.get("EVAL_DATASET_PATH", "eval_dataset.jsonl")
eval_score_threshold = float(os.environ.get("EVAL_SCORE_THRESHOLD", "4.0"))

# MCP Tool Configuration
mcp_server_label = os.environ.get("MCP_SERVER_LABEL", "mcptool")
mcp_server_url = os.environ.get("MCP_SERVER_URL", "https://apim-gateway-application-test-dev-txrh-mcp.azure-api.net/sse/sse")
mcp_project_connection_id = os.environ.get("MCP_PROJECT_CONNECTION_ID", "mcp-servicenow-oauth-passthrough1")
mcp_require_approval = os.environ.get("MCP_REQUIRE_APPROVAL", "never")

if not mcp_project_connection_id:
    print("❌ MCP_PROJECT_CONNECTION_ID is required.")
    sys.exit(1)

if not os.path.exists(prompt_file_path):
    print(f"📁 Local Error: '{prompt_file_path}' not found! Creating template file...")
    with open(prompt_file_path, "w", encoding="utf-8") as f:
        f.write("You are an expert AI agent running inside Microsoft Foundry.")

print(f"📖 Reading system instructions from '{prompt_file_path}'...")
with open(prompt_file_path, "r", encoding="utf-8") as file:
    new_instructions = file.read().strip()


# ============================================================================
# 2. MCP TOOL HELPERS
# ============================================================================
def build_mcp_tool() -> MCPTool:
    return MCPTool(
        server_label=mcp_server_label,
        server_url=mcp_server_url,
        require_approval=mcp_require_approval,
        project_connection_id=mcp_project_connection_id,
    )


# ============================================================================
# 3. DYNAMIC CONNECTION & KEY RESOLVER
# ============================================================================
def resolve_connection_details(client: AIProjectClient):
    """
    Dynamically fetches connection details from Azure AI Foundry portal.
    Works whether connected directly or routed behind APIM.
    """
    print(f"\n🔍 Dynamically resolving model connection '{aoai_connection_name}' from Foundry...")
    try:
        conn = client.connections.get(aoai_connection_name, include_credentials=True)
        target_endpoint = getattr(conn, "target", None) or "https://txrh-foundry.cognitiveservices.azure.com/"

        api_key = None
        if hasattr(conn, "key") and conn.key:
            api_key = conn.key
        elif hasattr(conn, "credentials"):
            creds = conn.credentials
            if isinstance(creds, dict):
                api_key = creds.get("key") or creds.get("api_key")
            else:
                api_key = getattr(creds, "key", None) or getattr(creds, "api_key", None)

        if not api_key and hasattr(conn, "properties") and isinstance(conn.properties, dict):
            api_key = conn.properties.get("key") or conn.properties.get("apiKey")

        print(f" -> Resolved Target Endpoint: {target_endpoint}")
        print(f" -> Authentication Method: {'API Key / APIM Subscription Key' if api_key else 'Entra ID (DefaultAzureCredential)'}")
        
        return target_endpoint, api_key

    except ResourceNotFoundError:
        print(f"⚠️ Connection '{aoai_connection_name}' not found. Falling back to default Cognitive Services endpoint...")
        return "https://txrh-foundry.cognitiveservices.azure.com/", None
    except Exception as e:
        print(f"⚠️ Connection resolution warning: {e}. Defaulting to main Cognitive Services URL...")
        return "https://txrh-foundry.cognitiveservices.azure.com/", None


# ============================================================================
# 4. EVALUATION TEST ENGINE
# ============================================================================
def run_evaluation_test(
    candidate_instructions: str, 
    azure_credential, 
    target_endpoint: str, 
    api_key: str = None
) -> bool:
    """
    Executes evaluation suite using candidate instructions against gpt-5.4.
    """
    print(f"\n🧪 STARTING EVALUATION TEST against candidate prompt...")

    if not os.path.exists(eval_dataset_path):
        print(f"❌ Evaluation Dataset Error: File '{eval_dataset_path}' not found.")
        return False

    # Initialize AzureOpenAI client with key or Entra ID token provider
    if api_key and isinstance(api_key, str) and api_key.strip():
        aoai_client = AzureOpenAI(
            azure_endpoint=target_endpoint,
            api_key=api_key.strip(),
            api_version="2024-10-21"
        )
    else:
        token_provider = get_bearer_token_provider(
            azure_credential,
            "https://cognitiveservices.azure.com/.default"
        )
        aoai_client = AzureOpenAI(
            azure_endpoint=target_endpoint,
            azure_ad_token_provider=token_provider,
            api_version="2024-10-21"
        )

    # Candidate runner executing inference against gpt-5.4
    def target_agent_runner(query: str):
        try:
            response = aoai_client.chat.completions.create(
                model=model_deployment,
                messages=[
                    {"role": "system", "content": candidate_instructions},
                    {"role": "user", "content": query}
                ],
                max_completion_tokens=800
            )
            return {"response": response.choices[0].message.content}
        except Exception as err:
            print(f"⚠️ Error querying '{model_deployment}': {err}")
            return {"response": "Error generating response."}

    # Model configuration for Evaluator Judge
    model_config = {
        "azure_endpoint": target_endpoint,
        "azure_deployment": model_deployment,
        "api_version": "2024-10-21"
    }

    eval_init_kwargs = {
        "model_config": model_config,
        "is_reasoning_model": True
    }

    if api_key and isinstance(api_key, str) and api_key.strip():
        model_config["api_key"] = api_key.strip()
        eval_credential = None
    else:
        eval_init_kwargs["credential"] = azure_credential
        eval_credential = azure_credential

    relevance_eval = RelevanceEvaluator(**eval_init_kwargs)
    groundedness_eval = GroundednessEvaluator(**eval_init_kwargs)

    print(f"📊 Running evaluators on dataset '{eval_dataset_path}' using model '{model_deployment}'...")
    try:
        eval_kwargs = {
            "data": eval_dataset_path,
            "target": target_agent_runner,
            "evaluators": {
                "relevance": relevance_eval,
                "groundedness": groundedness_eval,
            },
            "evaluator_config": {
                "relevance": {
                    "query": "${data.query}",
                    "response": "${target.response}"
                },
                "groundedness": {
                    "response": "${target.response}",
                    "context": "${data.ground_truth}"
                }
            },
            "model_config": model_config,
        }
        if eval_credential:
            eval_kwargs["credential"] = eval_credential

        eval_result = evaluate(**eval_kwargs)
        metrics = eval_result.get("metrics", {})

        def extract_score(target_key):
            for key, val in metrics.items():
                if target_key in key and isinstance(val, (int, float)):
                    return float(val)
            return 0.0

        relevance_score = extract_score("relevance")
        groundedness_score = extract_score("groundedness")
        avg_score = (relevance_score + groundedness_score) / 2.0 if (relevance_score and groundedness_score) else 0.0

        print(f"\n📈 Evaluation Results:")
        print(f"   - Relevance Score:    {relevance_score:.2f} / 5.0")
        print(f"   - Groundedness Score: {groundedness_score:.2f} / 5.0")
        print(f"   - Average Score:      {avg_score:.2f} / 5.0 (Threshold: {eval_score_threshold})")

        if avg_score >= eval_score_threshold:
            print("✅ EVALUATION PASSED: Candidate prompt meets quality standards.")
            return True
        else:
            print(f"❌ EVALUATION FAILED: Average score {avg_score:.2f} is below threshold {eval_score_threshold}.")
            return False

    except Exception as e:
        print(f"❌ Evaluation Execution Exception: {e}")
        return False


# ============================================================================
# 5. DEPLOYMENT & PUBLISHING ENGINE
# ============================================================================
print("\n🚀 Initializing secure Foundry project client transaction...")
credential = DefaultAzureCredential()

with AIProjectClient(
        endpoint=project_endpoint,
        credential=credential,
        allow_preview=True
) as client:

    # 1. Resolve Target Connection & Credentials Dynamically
    target_endpoint, api_key = resolve_connection_details(client)

    # 2. Verify MCP Connection
    try:
        client.connections.get(mcp_project_connection_id)
        print(f" -> MCP connection '{mcp_project_connection_id}' found.")
    except ResourceNotFoundError:
        print(f"❌ MCP connection '{mcp_project_connection_id}' does not exist.")
        sys.exit(1)
    except ServiceRequestError as e:
        print(f"❌ Network/DNS error reaching Foundry endpoint: {e}")
        sys.exit(1)

    # 3. Check Existing Agent & Detect Prompt Delta
    prompt_has_changed = True
    agent_exists = False

    try:
        print(f"🔍 Searching for existing agent '{agent_name}'...")
        existing_agent = client.agents.get(agent_name=agent_name)
        agent_exists = True

        latest_definition = existing_agent.versions.latest.definition
        current_instructions = (
            latest_definition.get("instructions", "")
            if isinstance(latest_definition, dict)
            else getattr(latest_definition, "instructions", "")
        )

        if current_instructions.strip() == new_instructions.strip():
            print("ℹ️ Prompt has NOT changed. Instructions match the published version.")
            prompt_has_changed = False
        else:
            print("🔔 PROMPT CHANGE DETECTED: Local 'prompt.txt' differs from published version.")

    except ResourceNotFoundError:
        print(f"\n⚠️ Agent '{agent_name}' does not exist. Initial creation required.")

    # 4. Pre-Publish Evaluation & Release Gating
    if prompt_has_changed or not agent_exists:
        print("\n🧪 Change detected. Initiating Pre-Publish Evaluation...")
        
        eval_passed = run_evaluation_test(
            candidate_instructions=new_instructions, 
            azure_credential=credential,
            target_endpoint=target_endpoint,
            api_key=api_key
        )

        if not eval_passed:
            print("\n⛔ PUBLISH BLOCKED: Evaluation test failed. The new agent version will NOT be published.")
            sys.exit(1)

        print(f"\n🚀 EVALUATION PASSED! Publishing new agent version using '{model_deployment}'...")
        mcp_tool = build_mcp_tool()

        new_version = client.agents.create_version(
            agent_name=agent_name,
            definition=PromptAgentDefinition(
                model=model_deployment,
                instructions=new_instructions,
                tools=[mcp_tool],
            )
        )

        print(f"\n🎯 PUBLISH SUCCESS: Pushed version '{new_version.version}' to '{agent_name}'.")

        if new_version.version:
            print(f"##vso[task.setvariable variable=AgentVersion;]{new_version.version}")
            with open("version.txt", "w", encoding="utf-8") as f:
                f.write(str(new_version.version))
            print(f"🚀 Successfully exposed version '{new_version.version}' to pipeline context.")
        else:
            print("❌ Failed to resolve a valid agent version string.")
            sys.exit(1)

    else:
        print("\n⏩ SKIPPING PUBLISH: No prompt changes detected and published agent is up to date.")

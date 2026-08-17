import os
import sys
import json
import httpx
from openai import OpenAI
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition


# ============================================================================
# 1. PARAMETERS & CONFIGURATION
# ============================================================================
project_endpoint = 'https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project'

apim_base_url = os.environ.get(
    "APIM_OPENAI_BASE_URL", 
    "https://apim-gateway-application-test-dev-txrh-mcp.azure-api.net/models/openai/v1"
)

apim_subscription_key = os.environ.get("APIM_SUBSCRIPTION_KEY", "")

agent_name = "txrh-demoagent-2-copy"
prompt_file_path = "prompt.txt"
model_deployment = "gpt-5.4"
agent_model = "roadie-ranger-foundry-resource/gpt-5.4"

eval_dataset_path = os.environ.get("EVAL_DATASET_PATH", "eval_dataset1.jsonl")
eval_score_threshold = float(os.environ.get("EVAL_SCORE_THRESHOLD", "2.0"))


# ============================================================================
# 2. GLOBAL HTTP HEADER INJECTION FOR APIM SUBSCRIPTION KEY
# ============================================================================
# Automatically attaches APIM subscription headers to all underlying httpx requests
_orig_async_init = httpx.AsyncClient.__init__
_orig_sync_init = httpx.Client.__init__

def _patched_async_init(self, *args, **kwargs):
    headers = kwargs.get("headers") or {}
    if isinstance(headers, dict):
        headers["Ocp-Apim-Subscription-Key"] = apim_subscription_key
        headers["api-key"] = apim_subscription_key
    kwargs["headers"] = headers
    _orig_async_init(self, *args, **kwargs)

def _patched_sync_init(self, *args, **kwargs):
    headers = kwargs.get("headers") or {}
    if isinstance(headers, dict):
        headers["Ocp-Apim-Subscription-Key"] = apim_subscription_key
        headers["api-key"] = apim_subscription_key
    kwargs["headers"] = headers
    _orig_sync_init(self, *args, **kwargs)

httpx.AsyncClient.__init__ = _patched_async_init
httpx.Client.__init__ = _patched_sync_init


credential = DefaultAzureCredential()

if not os.path.exists(prompt_file_path):
    print(f"📁 Local Error: '{prompt_file_path}' not found! Creating template file...")
    with open(prompt_file_path, "w", encoding="utf-8") as f:
        f.write("You are an expert AI agent running inside Microsoft Foundry.")

print(f"📖 Reading system instructions from '{prompt_file_path}'...")
with open(prompt_file_path, "r", encoding="utf-8") as file:
    new_instructions = file.read().strip()


# ============================================================================
# 3. EVALUATION TEST ENGINE (APIM-ROUTED VIA OPENAI V1)
# ============================================================================
def run_evaluation_test(candidate_instructions: str) -> bool:
    """
    Executes Azure AI evaluation against gpt-5.4 routed through APIM Gateway.
    """
    print(f"\n🧪 STARTING EVALUATION TEST via APIM Gateway ({apim_base_url})...")

    if not os.path.exists(eval_dataset_path):
        print(f"❌ Evaluation Dataset Error: File '{eval_dataset_path}' not found.")
        return False

    client = OpenAI(
        base_url=apim_base_url,
        api_key=apim_subscription_key,
        default_headers={
            "api-key": apim_subscription_key,
            "Ocp-Apim-Subscription-Key": apim_subscription_key
        }
    )

    # Strictly typed schema without 'default_headers'
    model_config = {
        "type": "openai",
        "base_url": apim_base_url,
        "model": model_deployment,
        "api_key": apim_subscription_key
    }

    def target_agent_runner(query: str):
        try:
            response = client.chat.completions.create(
                model=model_deployment,
                messages=[
                    {"role": "system", "content": candidate_instructions},
                    {"role": "user", "content": query}
                ],
                max_completion_tokens=80000
            )
            return {"response": response.choices[0].message.content}
        except Exception as err:
            print(f"⚠️ Error querying '{model_deployment}' via APIM: {err}")
            return {"response": "Error generating response."}

    from azure.ai.evaluation import evaluate, RelevanceEvaluator, GroundednessEvaluator

    eval_init_kwargs = {
        "model_config": model_config,
        "is_reasoning_model": True
    }

    relevance_eval = RelevanceEvaluator(**eval_init_kwargs)
    groundedness_eval = GroundednessEvaluator(**eval_init_kwargs)

    print(f"📊 Running evaluators on dataset '{eval_dataset_path}' via APIM...")
    try:
        eval_result = evaluate(
            data=eval_dataset_path,
            target=target_agent_runner,
            evaluators={
                "relevance": relevance_eval,
                "groundedness": groundedness_eval,
            },
            evaluator_config={
                "relevance": {
                    "query": "${data.query}",
                    "response": "${target.response}"
                },
                "groundedness": {
                    "response": "${target.response}",
                    "context": "${data.ground_truth}"
                }
            },
            model_config=model_config
        )

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
        print(f"   - Raw Metrics Returned: {metrics}")
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
# 4. DEPLOYMENT & PUBLISHING ENGINE
# ============================================================================
print("\n🚀 Initializing secure Foundry project client transaction...")

with AIProjectClient(
        endpoint=project_endpoint,
        credential=credential,
        allow_preview=True
) as client:

    prompt_has_changed = True
    agent_exists = False

    try:
        print(f"🔍 Searching for existing tracking configuration for '{agent_name}'...")
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

    if prompt_has_changed or not agent_exists:
        print("\n🧪 Change detected. Initiating Pre-Publish Evaluation...")
        
        eval_passed = run_evaluation_test(new_instructions)

        if not eval_passed:
            print("\n⛔ PUBLISH BLOCKED: Evaluation test failed. The new agent version will NOT be published.")
            sys.exit(1)

        print(f"\n🚀 EVALUATION PASSED! Proceeding to publish agent version using '{model_deployment}'...")

        new_version = client.agents.create_version(
            agent_name=agent_name,
            definition=PromptAgentDefinition(
                model=agent_model,
                instructions=new_instructions,
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

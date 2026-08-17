import os
import sys
import json
import httpx
from openai import OpenAI

# Configuration
APIM_BASE_URL = os.environ.get(
    "APIM_OPENAI_BASE_URL",
    "https://apim-gateway-application-test-dev-txrh-mcp.azure-api.net/roadie-ranger-foundry-resource/openai/v1"
)
APIM_SUBSCRIPTION_KEY = os.environ.get("APIM_SUBSCRIPTION_KEY", "1007fc188a6d4675b308ab24a7480f47")
MODEL_DEPLOYMENT = "gpt-5.4"
EVAL_DATASET_PATH = os.environ.get("EVAL_DATASET_PATH", "snow_eval_dataset.jsonl")
EVAL_SCORE_THRESHOLD = float(os.environ.get("EVAL_SCORE_THRESHOLD", "3.5"))
PROMPT_FILE_PATH = "prompt.txt"

# ============================================================================
# GLOBAL HTTPX HEADER INJECTION FOR APIM GATEWAY
# ============================================================================
_orig_async_init = httpx.AsyncClient.__init__
_orig_sync_init = httpx.Client.__init__

def _patched_async_init(self, *args, **kwargs):
    headers = kwargs.get("headers") or {}
    if isinstance(headers, dict):
        headers["Ocp-Apim-Subscription-Key"] = APIM_SUBSCRIPTION_KEY
        headers["api-key"] = APIM_SUBSCRIPTION_KEY
    kwargs["headers"] = headers
    _orig_async_init(self, *args, **kwargs)

def _patched_sync_init(self, *args, **kwargs):
    headers = kwargs.get("headers") or {}
    if isinstance(headers, dict):
        headers["Ocp-Apim-Subscription-Key"] = APIM_SUBSCRIPTION_KEY
        headers["api-key"] = APIM_SUBSCRIPTION_KEY
    kwargs["headers"] = headers
    _orig_sync_init(self, *args, **kwargs)

httpx.AsyncClient.__init__ = _patched_async_init
httpx.Client.__init__ = _patched_sync_init


def main():
    print(f"🧪 Running evaluation test via APIM Gateway ({APIM_BASE_URL})...")

    if not os.path.exists(EVAL_DATASET_PATH) or not os.path.exists(PROMPT_FILE_PATH):
        print(f"❌ Required files missing: '{EVAL_DATASET_PATH}' or '{PROMPT_FILE_PATH}'")
        sys.exit(1)

    with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
        candidate_instructions = f.read().strip()

    client = OpenAI(
        base_url=APIM_BASE_URL,
        api_key=APIM_SUBSCRIPTION_KEY,
        default_headers={
            "api-key": APIM_SUBSCRIPTION_KEY,
            "Ocp-Apim-Subscription-Key": APIM_SUBSCRIPTION_KEY
        }
    )

    model_config = {
        "type": "openai",
        "base_url": APIM_BASE_URL,
        "model": MODEL_DEPLOYMENT,
        "api_key": APIM_SUBSCRIPTION_KEY
    }

    def target_agent_runner(query: str):
        try:
            response = client.chat.completions.create(
                model=MODEL_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": candidate_instructions},
                    {"role": "user", "content": query}
                ],
                max_completion_tokens=80000
            )
            output_text = response.choices[0].message.content or ""
            return {"response": output_text}
        except Exception as err:
            print(f"⚠️ Runner error on query '{query[:40]}': {err}")
            return {"response": "Error generating response."}

    from azure.ai.evaluation import evaluate, RelevanceEvaluator, GroundednessEvaluator

    eval_init_kwargs = {
        "model_config": model_config,
        "is_reasoning_model": True
    }

    relevance_eval = RelevanceEvaluator(**eval_init_kwargs)
    groundedness_eval = GroundednessEvaluator(**eval_init_kwargs)

    try:
        eval_result = evaluate(
            data=EVAL_DATASET_PATH,
            target=target_agent_runner,
            evaluators={
                "relevance": relevance_eval,
                "groundedness": groundedness_eval,
            },
            evaluator_config={
                "relevance": {
                    "column_mapping": {"query": "${data.query}", "response": "${target.response}"}
                },
                "groundedness": {
                    "column_mapping": {"response": "${target.response}", "context": "${data.ground_truth}"}
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

        print(f"\n📊 Evaluation Results:")
        print(f"   • Relevance Score:    {relevance_score:.2f} / 5.0")
        print(f"   • Groundedness Score: {groundedness_score:.2f} / 5.0")
        print(f"   • Average Score:      {avg_score:.2f} / 5.0 (Threshold: {EVAL_SCORE_THRESHOLD})")

        if avg_score >= EVAL_SCORE_THRESHOLD:
            print("✅ EVALUATION PASSED")
            sys.exit(0)
        else:
            print(f"❌ EVALUATION FAILED: Average score {avg_score:.2f} is below threshold {EVAL_SCORE_THRESHOLD}.")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Evaluation error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
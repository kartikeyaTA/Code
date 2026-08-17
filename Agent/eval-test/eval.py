import json
import os
import sys
import tempfile
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.evaluation import (
    evaluate,
    RelevanceEvaluator,
    GroundednessEvaluator,
    ToolCallAccuracyEvaluator
)

# ============================================================================
# CONFIGURATION
# ============================================================================
PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
JUDGE_MODEL_DEPLOYMENT = "roadie-ranger-foundry-resource/gpt-5.4"
CANDIDATE_AGENT_NAME = "txrh-demoagent-2-copy-eval-candidate"
DATASET_FILE_PATH = "snow_eval_dataset.json"
TOOLS_FILE_PATH = "tools_schema.json"

if not os.path.exists(DATASET_FILE_PATH) or not os.path.exists(TOOLS_FILE_PATH):
    print(f"❌ Prerequisites missing: Ensure '{DATASET_FILE_PATH}' and '{TOOLS_FILE_PATH}' exist.")
    sys.exit(1)

credential = DefaultAzureCredential()
project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential, allow_preview=True)

model_config = {
    "azure_endpoint": PROJECT_ENDPOINT,
    "azure_deployment": JUDGE_MODEL_DEPLOYMENT
}

# 1. Resolve Candidate Agent ID
try:
    candidate_agent = project_client.agents.get(agent_name=CANDIDATE_AGENT_NAME)
    CANDIDATE_AGENT_ID = candidate_agent.id
except Exception as e:
    print(f"❌ Could not resolve Candidate Agent '{CANDIDATE_AGENT_NAME}': {e}")
    sys.exit(1)


# ============================================================================
# TARGET RUNNER FUNCTION
# ============================================================================
def agent_target_runner(query: str):
    """Executes query turn against candidate agent and intercepts tool calls."""
    try:
        # Correct direct SDK calls on project_client.agents
        thread = project_client.agents.create_thread()
        project_client.agents.create_message(thread_id=thread.id, role="user", content=query)
        run = project_client.agents.create_run(thread_id=thread.id, assistant_id=CANDIDATE_AGENT_ID)

        actual_tool_calls = []
        response_text = ""

        # Poll run status
        while run.status in ["queued", "in_progress", "requires_action"]:
            run = project_client.agents.get_run(thread_id=thread.id, run_id=run.id)

            if run.status == "requires_action":
                tool_calls = run.required_action.submit_tool_outputs.tool_calls
                for tc in tool_calls:
                    actual_tool_calls.append({
                        "name": tc.function.name,
                        "arguments": json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                    })
                # Cancel run to prevent executing live MCP backend
                project_client.agents.cancel_run(thread_id=thread.id, run_id=run.id)
                break

            if run.status == "completed":
                messages = project_client.agents.list_messages(thread_id=thread.id)
                response_text = messages.data[0].content[0].text.value
                break

        return {
            "response": response_text,
            "tool_calls": actual_tool_calls
        }
    except Exception as e:
        print(f"⚠️ Runner error on query '{query[:40]}': {e}")
        return {"response": "", "tool_calls": []}


# ============================================================================
# MAIN EVALUATION ENGINE
# ============================================================================
def main():
    print(f"🧪 Loading dataset from '{DATASET_FILE_PATH}' and tools from '{TOOLS_FILE_PATH}'...")
    
    with open(DATASET_FILE_PATH, "r", encoding="utf-8") as f:
        eval_json = json.load(f)

    with open(TOOLS_FILE_PATH, "r", encoding="utf-8") as f:
        tool_defs = json.load(f)

    dataset_records = eval_json.get("data", [])

    # Inject tool_definitions into dataset records for ToolCallAccuracyEvaluator
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as temp_jsonl:
        for record in dataset_records:
            record["tool_definitions"] = tool_defs
            temp_jsonl.write(json.dumps(record) + "\n")
        temp_jsonl_path = temp_jsonl.name

    try:
        print(f"🚀 Running evaluate() using temporary dataset: '{temp_jsonl_path}'...")

        relevance_eval = RelevanceEvaluator(model_config=model_config)
        groundedness_eval = GroundednessEvaluator(model_config=model_config)
        tool_accuracy_eval = ToolCallAccuracyEvaluator(model_config=model_config)

        eval_result = evaluate(
            data=temp_jsonl_path,
            target=agent_target_runner,
            evaluators={
                "relevance": relevance_eval,
                "groundedness": groundedness_eval,
                "tool_accuracy": tool_accuracy_eval
            },
            evaluator_config={
                "relevance": {
                    "column_mapping": {
                        "query": "${data.query}",
                        "response": "${target.response}"
                    }
                },
                "groundedness": {
                    "column_mapping": {
                        "response": "${target.response}",
                        "context": "${data.context}"
                    }
                },
                "tool_accuracy": {
                    "column_mapping": {
                        "query": "${data.query}",
                        "tool_calls": "${target.tool_calls}",
                        "tool_definitions": "${data.tool_definitions}"
                    }
                }
            }
        )

        print("\n📊 LOCAL EVALUATION SUMMARY METRICS:")
        metrics = eval_result.get("metrics", {})
        for metric_name, score in metrics.items():
            print(f"  • {metric_name}: {score}")

    finally:
        if os.path.exists(temp_jsonl_path):
            os.remove(temp_jsonl_path)


if __name__ == "__main__":
    main()
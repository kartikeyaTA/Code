import os
import sys
import time
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceExistsError
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import TestingCriterionAzureAIEvaluator

# ============================================================================
# CONFIGURATION
# ============================================================================
PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
AGENT_NAME = "txrh-demoagent-2-copy"
LOCAL_DATASET_PATH = "snow_eval_dataset.jsonl"
JUDGE_MODEL_DEPLOYMENT = "roadie-ranger-foundry-resource/gpt-5.4"
#JUDGE_MODEL_DEPLOYMENT = "gpt-5.1"


if not os.path.exists(LOCAL_DATASET_PATH):
    print(f"❌ Local Error: '{LOCAL_DATASET_PATH}' not found!")
    sys.exit(1)

with AIProjectClient(
    endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential()
) as project_client:

    # 1. Upload dataset safely using dynamic timestamp versioning
    dataset_name = "snow-agent-eval-dataset"
    dynamic_version = str(int(time.time()))

    print(f"📤 Uploading local dataset '{LOCAL_DATASET_PATH}' to Foundry...")
    try:
        dataset = project_client.datasets.upload_file(
            name=dataset_name,
            version=dynamic_version,
            file_path=LOCAL_DATASET_PATH
        )
        print(f"✅ Dataset uploaded & registered! ID: {dataset.id}")
    except ResourceExistsError:
        print("⚠️ Dataset version already exists. Fetching existing dataset reference...")
        dataset = project_client.datasets.get(name=dataset_name, version="1")

    openai_client = project_client.get_openai_client()

    # 2. Define Data Schema for Evaluation Definition
    data_source_config = {
        "type": "custom",
        "item_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}
            },
            "required": ["query"]
        },
        "include_sample_schema": True
    }

    # 3. Define Evaluator Criteria & Mappings
    testing_criteria = [
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="Relevance",
            evaluator_name="builtin.relevance",
            data_mapping={
                "query": "{{item.query}}",
                "response": "{{sample.output_text}}"
            },
            initialization_parameters={"deployment_name": JUDGE_MODEL_DEPLOYMENT}
        ),
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="Groundedness",
            evaluator_name="builtin.groundedness",
            data_mapping={
                "response": "{{sample.output_text}}",
                "context": "{{item.ground_truth}}"
            },
            initialization_parameters={"deployment_name": JUDGE_MODEL_DEPLOYMENT}
        )
    ]

    # 4. Register Reusable Evaluation Container in Foundry
    print("📝 Registering Evaluation Definition in Azure AI Foundry...")
    evaluation = openai_client.evals.create(
        name=f"Agent_Quality_Eval_{AGENT_NAME}",
        data_source_config=data_source_config,
        testing_criteria=testing_criteria
    )
    print(f"✅ Evaluation Definition Created! ID: {evaluation.id}")

    # 5. Trigger Execution Run targeting live agent with uploaded dataset
    print("🚀 Triggering Cloud Evaluation Run...")
    eval_run = openai_client.evals.runs.create(
        eval_id=evaluation.id,
        name=f"Run_{AGENT_NAME}",
        data_source={
            "type": "azure_ai_target_completions",
            "source": {
                "type": "file_id",
                "id": dataset.id
            },
            "input_messages": {
                "type": "template",
                "template": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": {"type": "input_text", "text": "{{item.query}}"}
                    }
                ]
            },
            "target": {
                "type": "azure_ai_agent",
                "name": AGENT_NAME
            }
        }
    )

    print(f"📌 Run ID: {eval_run.id}")
    print("⏳ Polling evaluation job status until completion...")

    # 6. Poll for job completion
    terminal_states = {"completed", "failed", "errored", "canceled", "partially_completed"}
    run_status_obj = None

    while True:
        run_status_obj = openai_client.evals.runs.retrieve(
            eval_id=evaluation.id,
            run_id=eval_run.id
        )
        current_status = getattr(run_status_obj, "status", "unknown").lower()
        print(f"   • Current status: {current_status}")

        if current_status in terminal_states or any(k in current_status for k in ["complete", "error", "fail"]):
            break
        time.sleep(8)

    print("\n" + "=" * 80)
    print(f"📊 FINAL RUN STATUS: {getattr(run_status_obj, 'status', 'UNKNOWN')}")

    # 7. Check and display top-level run error
    top_level_err = getattr(run_status_obj, "error", None)
    if top_level_err:
        print(f"\n❌ TOP-LEVEL ERROR DETECTED:\n{top_level_err}")

    # 8. Fetch and display row-by-row failure reasons
    print("\n🔍 ROW-BY-ROW ERROR DIAGNOSTICS:")
    try:
        output_items = list(openai_client.evals.runs.output_items.list(
            eval_id=evaluation.id,
            run_id=eval_run.id
        ))

        if not output_items:
            print("⚠️ No row items were returned by the evaluation service.")

        for idx, item in enumerate(output_items, 1):
            item_data = item.model_dump() if hasattr(item, "model_dump") else (item if isinstance(item, dict) else vars(item))
            item_status = item_data.get("status", "Unknown")
            
            # Extract error details across different response layers
            item_error = (
                item_data.get("error")
                or item_data.get("error_message")
                or item_data.get("sample", {}).get("error")
            )

            print(f"\n--- Row #{idx} [Status: {item_status}] ---")
            if item_error:
                print(f"❌ Error Message: {item_error}")
            else:
                print(f"ℹ️ Item Dump: {item_data}")

    except Exception as fetch_err:
        print(f"⚠️ Could not fetch detailed row outputs: {fetch_err}")

    print("=" * 80 + "\n")
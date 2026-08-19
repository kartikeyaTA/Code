import os
import sys
import time
import json
import html
import tempfile
import ast
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceExistsError
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import TestingCriterionAzureAIEvaluator
from openai.types.eval_create_params import DataSourceConfigCustom

# ============================================================================
# CONFIGURATION
# ============================================================================
PROJECT_ENDPOINT = os.environ.get(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
)
AGENT_NAME = os.environ.get("AGENT_NAME", "txrh-demoagent-2-copy")
JUDGE_MODEL_DEPLOYMENT = os.environ.get("FOUNDRY_MODEL_NAME", "roadie-ranger-foundry-resource/gpt-5.4")
LOCAL_DATASET_PATH = os.environ.get("EVAL_DATASET_PATH", "snow_eval_data.jsonl")
HTML_REPORT_PATH = "eval_report.html"
EVAL_SCORE_THRESHOLD = 0.7

DEFAULT_DUMMY_TOOLS = [
    {
        "name": "mcp_mcp-eval-test.search_kb_via_table_api",
        "type": "function",
        "description": "Search knowledge base articles in ServiceNow.",
        "parameters": {
            "type": "object",
            "properties": {"user_query": {"type": "string"}},
            "required": ["user_query"]
        }
    },
    {
        "name": "mcp_mcp-eval-test.create_incident",
        "type": "function",
        "description": "Create a new support incident ticket in ServiceNow.",
        "parameters": {
            "type": "object",
            "properties": {"short_description": {"type": "string"}},
            "required": ["short_description"]
        }
    }
]
if not os.path.exists(LOCAL_DATASET_PATH):
    print(f"❌ Local Error: '{LOCAL_DATASET_PATH}' not found!")
    sys.exit(1)


# ============================================================================
# DATASET RESILIENT LOADER & FORMATTER
# ============================================================================
def load_dataset_resilient(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass

    try:
        parsed = ast.literal_eval(content)
        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict):
            return [parsed]
    except Exception:
        pass

    dataset_rows = []
    for line in content.splitlines():
        clean_line = line.strip().rstrip(",")
        if clean_line in ["[", "]"]:
            continue
        if clean_line:
            try:
                row = json.loads(clean_line)
                dataset_rows.append(row)
            except json.JSONDecodeError:
                try:
                    row = ast.literal_eval(clean_line)
                    if isinstance(row, dict):
                        dataset_rows.append(row)
                except Exception:
                    continue
    return dataset_rows


def format_tool_definitions(tools):
    formatted = []
    for t in tools:
        if isinstance(t, dict):
            if "function" in t and isinstance(t["function"], dict):
                fn = t["function"]
                if "name" in fn:
                    formatted.append({
                        "name": fn["name"],
                        "type": t.get("type", "function"),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {})
                    })
            elif "name" in t:
                formatted.append({
                    "name": t["name"],
                    "type": t.get("type", "function"),
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {})
                })
    return formatted or DEFAULT_DUMMY_TOOLS


# ============================================================================
# PARSING & REPORT HELPERS
# ============================================================================
def clean_html(text):
    if not text or text == "N/A":
        return "N/A"
    return html.escape(str(text)).replace("\n", "<br>")


def parse_query(data, row_idx, local_rows):
    for key in ["query", "user_query", "input_text"]:
        if data.get(key):
            return str(data[key])
    for key in ["item", "input", "source"]:
        if isinstance(data.get(key), dict) and data[key].get("query"):
            return str(data[key]["query"])
    if row_idx < len(local_rows):
        return local_rows[row_idx].get("query", "N/A")
    return "N/A"


def parse_tool_calls(data):
    sample = data.get("sample") or {}
    if isinstance(sample, dict):
        tool_calls = sample.get("tool_calls")
        if tool_calls:
            return json.dumps(tool_calls, indent=2)

        items = sample.get("output_items") or sample.get("output") or sample.get("messages") or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("tool_calls"):
                    return json.dumps(item["tool_calls"], indent=2)
    return "No tool calls generated"


def parse_metric_score(data, metric_name):
    target = metric_name.lower()
    results_list = data.get("testing_criteria_results") or data.get("results") or []
    if isinstance(results_list, list):
        for item in results_list:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("evaluator_name") or "").lower()
                if target in name:
                    score = item.get("score") if item.get("score") is not None else item.get("value")
                    if score is not None:
                        return float(score)

    def deep_score(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if target in k.lower() or "tool" in k.lower():
                    if isinstance(v, (int, float)):
                        return float(v)
                    if isinstance(v, dict) and "score" in v:
                        return float(v["score"])
                res = deep_score(v)
                if res is not None:
                    return res
        elif isinstance(obj, list):
            for i in obj:
                res = deep_score(i)
                if res is not None:
                    return res
        return None

    res = deep_score(data)
    return res if res is not None else 0.0


def generate_html_report(rows_data, avg_score, passed):
    status_color = "#107c41" if passed else "#d13438"
    status_text = "PASSED" if passed else "FAILED"

    table_rows_html = ""
    for idx, r in enumerate(rows_data, 1):
        score_str = f"{r['score'] * 100:.0f}%" if r['score'] is not None else "N/A"

        table_rows_html += f"""
        <tr>
            <td style="text-align:center;">{idx}</td>
            <td><b>{clean_html(r['query'])}</b></td>
            <td><pre style="font-size:11px;">{clean_html(r['tool_calls'])}</pre></td>
            <td style="text-align:center; font-weight:bold; color:#0078d4;">{score_str}</td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>AI Agent Tool Selection Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 30px; background-color: #f8f9fa; color: #333; }}
        h1 {{ margin-bottom: 5px; color: #111; }}
        .subtitle {{ color: #666; margin-bottom: 25px; }}
        .cards {{ display: flex; gap: 15px; margin-bottom: 30px; }}
        .card {{ background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.08); flex: 1; border-top: 4px solid #0078d4; }}
        .card.status {{ border-top-color: {status_color}; }}
        .card-title {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: #666; margin-bottom: 8px; }}
        .card-value {{ font-size: 26px; font-weight: bold; }}
        .badge {{ display: inline-block; padding: 4px 12px; border-radius: 4px; color: #fff; background-color: {status_color}; font-size: 18px; font-weight: bold; }}
        table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.08); }}
        th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #e9ecef; vertical-align: top; font-size: 13px; }}
        th {{ background-color: #0078d4; color: white; font-weight: 600; text-transform: uppercase; font-size: 11px; }}
        tr:hover {{ background-color: #f1f3f5; }}
        pre {{ background: #f1f3f5; padding: 6px; border-radius: 4px; overflow-x: auto; margin: 0; }}
    </style>
</head>
<body>
    <h1>🤖 AI Agent Evaluation Report (builtin.tool_selection)</h1>
    <div class="subtitle">Target Agent: <b>{AGENT_NAME}</b> | Judge Model: {JUDGE_MODEL_DEPLOYMENT}</div>
    
    <div class="cards">
        <div class="card status">
            <div class="card-title">Verdict Status</div>
            <div class="badge">{status_text}</div>
        </div>
        <div class="card">
            <div class="card-title">Tool Selection Accuracy</div>
            <div class="card-value">{avg_score * 100:.1f}%</div>
            <small style="color:#666;">Threshold: {EVAL_SCORE_THRESHOLD * 100:.0f}%</small>
        </div>
    </div>

    <h2>Test Row Breakdown</h2>
    <table>
        <thead>
            <tr>
                <th style="width: 4%; text-align:center;">#</th>
                <th style="width: 35%;">User Query</th>
                <th style="width: 50%;">Generated Agent Tool Calls</th>
                <th style="width: 11%; text-align:center;">Tool Score</th>
            </tr>
        </thead>
        <tbody>
            {table_rows_html}
        </tbody>
    </table>
</body>
</html>
"""
    with open(HTML_REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)


# ============================================================================
# MAIN EXECUTION
# ============================================================================
def main():
    print(f"🧪 Initializing Tool Selection Evaluation for Agent: '{AGENT_NAME}'...")

    raw_rows = load_dataset_resilient(LOCAL_DATASET_PATH)
    if not raw_rows:
        print(f"❌ Error: Could not parse dataset '{LOCAL_DATASET_PATH}'!")
        sys.exit(1)

    local_dataset_rows = []
    for row in raw_rows:
        if isinstance(row, dict):
            raw_tools = row.get("tool_definitions") or DEFAULT_DUMMY_TOOLS
            row["tool_definitions"] = format_tool_definitions(raw_tools)
            local_dataset_rows.append(row)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as temp_file:
        for row in local_dataset_rows:
            temp_file.write(json.dumps(row) + "\n")
        normalized_dataset_path = temp_file.name

    try:
        with DefaultAzureCredential() as credential, AIProjectClient(
            endpoint=PROJECT_ENDPOINT, credential=credential
        ) as project_client, project_client.get_openai_client() as client:

            dataset_name = "snow-agent-eval-tool-selection"
            dynamic_version = str(int(time.time()))

            print("📤 Uploading dataset to Azure AI Foundry...")
            try:
                dataset = project_client.datasets.upload_file(
                    name=dataset_name,
                    version=dynamic_version,
                    file_path=normalized_dataset_path
                )
            except ResourceExistsError:
                dataset = project_client.datasets.get(name=dataset_name, version="1")

            data_source_config = DataSourceConfigCustom(
                type="custom",
                item_schema={
                    "type": "object",
                    "properties": {
                        "query": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "object"}}]},
                        "response": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "object"}}]},
                        "tool_calls": {"anyOf": [{"type": "object"}, {"type": "array", "items": {"type": "object"}}]},
                        "tool_definitions": {"anyOf": [{"type": "object"}, {"type": "array", "items": {"type": "object"}}]},
                    },
                    "required": ["query", "tool_definitions"],
                },
                include_sample_schema=True,
            )

            # FIXED DATA MAPPING: Maps 'response' to 'sample.output' and 'tool_calls' to 'sample.tool_calls'
            testing_criteria = [
                TestingCriterionAzureAIEvaluator(
                    type="azure_ai_evaluator",
                    name="tool_selection",
                    evaluator_name="builtin.tool_selection",
                    initialization_parameters={"model": JUDGE_MODEL_DEPLOYMENT},
                    data_mapping={
                        "query": "{{item.query}}",
                        "response": "{{sample.output_text}}",
                        "tool_calls": "{{sample.tool_calls}}",
                        "tool_definitions": "{{item.tool_definitions}}",
                    },
                )
            ]

            print("Creating Evaluation...")
            eval_object = client.evals.create(
                name=f"Agent_Tool_Selection_Eval_{AGENT_NAME}",
                data_source_config=data_source_config,
                testing_criteria=testing_criteria,
            )
            print(f"✅ Evaluation Object Created ID: {eval_object.id}")

            print(f"🚀 Triggering completion run for target agent '{AGENT_NAME}'...")
            eval_run_object = client.evals.runs.create(
                eval_id=eval_object.id,
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

            print(f"✅ Eval Run Triggered ID: {eval_run_object.id}")

            terminal_states = {"completed", "failed", "errored", "canceled", "partially_completed"}
            sys.stdout.write("⏳ Polling evaluation job status: ")
            sys.stdout.flush()

            while True:
                run_status_obj = client.evals.runs.retrieve(
                    eval_id=eval_object.id,
                    run_id=eval_run_object.id
                )
                current_status = getattr(run_status_obj, "status", "unknown").lower()
                sys.stdout.write("▪")
                sys.stdout.flush()

                if current_status in terminal_states or any(k in current_status for k in ["complete", "error", "fail"]):
                    print(f" [{current_status.upper()}]")
                    break
                time.sleep(5)

            output_items = list(client.evals.runs.output_items.list(
                eval_id=eval_object.id,
                run_id=eval_run_object.id
            ))

            rows_summary = []
            scores = []

            for idx, item in enumerate(output_items):
                data = item.model_dump() if hasattr(item, "model_dump") else (item if isinstance(item, dict) else vars(item))

                query = parse_query(data, idx, local_dataset_rows)
                tool_calls_str = parse_tool_calls(data)
                score = parse_metric_score(data, "tool_selection")

                if score is not None:
                    scores.append(score)

                rows_summary.append({
                    "query": query,
                    "tool_calls": tool_calls_str,
                    "score": score
                })

            avg_score = sum(scores) / len(scores) if scores else 0.0
            passed = (avg_score >= EVAL_SCORE_THRESHOLD) and (current_status == "completed")

            generate_html_report(rows_summary, avg_score, passed)

            print("\n" + "=" * 80)
            print("📊 EVALUATION SUMMARY DASHBOARD")
            print("=" * 80)
            print(f"🎯 Target Agent:             {AGENT_NAME}")
            print(f"📌 Evaluation Run ID:        {eval_run_object.id}")
            print(f"🏁 Final Status:              {getattr(run_status_obj, 'status', 'COMPLETED')}")
            print(f"🔢 Total Evaluated Rows:      {len(rows_summary)}")
            print("-" * 80)
            print(f"   • Tool Selection Accuracy: {avg_score * 100:.1f}% (Threshold: {EVAL_SCORE_THRESHOLD * 100:.0f}%)")
            print("-" * 80)

            if passed:
                print("✅ VERDICT: EVALUATION PASSED")
            else:
                print("❌ VERDICT: EVALUATION FAILED")

            print(f"\n📄 Styled HTML Dashboard generated: '{HTML_REPORT_PATH}'")
            print("=" * 80 + "\n")

    finally:
        if os.path.exists(normalized_dataset_path):
            os.remove(normalized_dataset_path)


if __name__ == "__main__":
    main()
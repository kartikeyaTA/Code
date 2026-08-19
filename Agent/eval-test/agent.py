import os
import sys
import time
import json
import html
import tempfile
import httpx
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.evaluation import (
    evaluate,
    RelevanceEvaluator,
    GroundednessEvaluator
)

# ============================================================================
# CONFIGURATION
# ============================================================================
PROJECT_ENDPOINT = os.environ.get(
    "PROJECT_ENDPOINT",
    "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
)
AGENT_NAME = os.environ.get("AGENT_NAME", "txrh-demoagent-2-copy-eval-candidate")
JUDGE_MODEL_DEPLOYMENT = "roadie-ranger-foundry-resource/gpt-5.4"
EVAL_DATASET_PATH = os.environ.get("EVAL_DATASET_PATH", "snow_eval_dataset.jsonl")
EVAL_SCORE_THRESHOLD = float(os.environ.get("EVAL_SCORE_THRESHOLD", "3.5"))
HTML_REPORT_PATH = "eval_report.html"

APIM_SUBSCRIPTION_KEY = os.environ.get("APIM_SUBSCRIPTION_KEY", "")

# APIM Gateway Header Patching (if using APIM endpoints)
if APIM_SUBSCRIPTION_KEY:
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

QUERY_CONTEXT_MAP = {}


# ============================================================================
# TOOL SELECTION EVALUATOR (NAME & SEQUENCE MATCHING)
# ============================================================================
class ToolSelectionAccuracyEvaluator:
    """Evaluates tool selection accuracy strictly based on tool names and sequence."""
    def __call__(self, *, tool_calls=None, expected_tool_calls=None, **kwargs):
        tool_calls = tool_calls or []
        expected_tool_calls = expected_tool_calls or []

        if not expected_tool_calls and not tool_calls:
            return {"tool_accuracy": 5.0}

        if not expected_tool_calls or not tool_calls:
            return {"tool_accuracy": 1.0}

        actual_names = [t.get("name") for t in tool_calls if isinstance(t, dict)]
        expected_names = [t.get("name") for t in expected_tool_calls if isinstance(t, dict)]

        if actual_names == expected_names:
            return {"tool_accuracy": 5.0}

        matching_count = sum(1 for a, e in zip(actual_names, expected_names) if a == e)
        score = (matching_count / max(len(expected_names), len(actual_names))) * 5.0
        return {"tool_accuracy": max(1.0, round(score, 2))}


# ============================================================================
# PARSING & REPORT HELPERS
# ============================================================================
def get_field_from_row(row: dict, target_key: str):
    """Safely extracts metrics and row data across flat and nested evaluator formats."""
    inputs = row.get("inputs", {})
    outputs = row.get("outputs", {})

    combined = {}
    if isinstance(inputs, dict):
        combined.update(inputs)
    if isinstance(outputs, dict):
        combined.update(outputs)
    combined.update(row)

    for k, v in combined.items():
        if k == target_key or k.endswith(f".{target_key}"):
            if v is not None:
                return v
        if target_key.lower() in k.lower():
            if isinstance(v, (int, float)):
                return f"{v:.2f}"
            elif isinstance(v, dict) and "score" in v:
                return f"{v['score']:.2f}"
            elif isinstance(v, str):
                return v
    return None


def generate_html_report(eval_result, avg_score, rel_score, grd_score, tool_score, passed):
    """Generates a styled HTML dashboard report from evaluation execution."""
    status_color = "#107c41" if passed else "#d13438"
    status_text = "PASSED" if passed else "FAILED"

    rows = eval_result.get("rows", [])
    table_rows_html = ""

    for idx, row in enumerate(rows, 1):
        query = get_field_from_row(row, "query") or "N/A"
        context = get_field_from_row(row, "ground_truth") or get_field_from_row(row, "context") or "N/A"
        response = get_field_from_row(row, "response") or "N/A"

        actual_tools = get_field_from_row(row, "tool_calls") or []
        expected_tools = get_field_from_row(row, "expected_tool_calls") or []

        r_val = get_field_from_row(row, "relevance") or "N/A"
        g_val = get_field_from_row(row, "groundedness") or "N/A"
        t_val = get_field_from_row(row, "tool_accuracy") or get_field_from_row(row, "tool") or "N/A"

        clean_actual_tools = [{k: v for k, v in tc.items() if k != "id"} for tc in actual_tools] if isinstance(actual_tools, list) else []
        actual_tools_str = json.dumps(clean_actual_tools, indent=1) if clean_actual_tools else "No Tool Calls"
        expected_tools_str = json.dumps(expected_tools, indent=1) if expected_tools else "None Expected"

        # Pre-format text variables outside f-strings to prevent SyntaxError in Python < 3.12
        query_fmt = html.escape(str(query))
        response_fmt = html.escape(str(response)).replace("\n", "<br>")
        context_fmt = html.escape(str(context))
        actual_tools_fmt = html.escape(actual_tools_str)
        expected_tools_fmt = html.escape(expected_tools_str)
        expected_len = len(expected_tools) if isinstance(expected_tools, list) else 0

        table_rows_html += f"""
        <tr>
            <td style="text-align:center;">{idx}</td>
            <td><b>{query_fmt}</b></td>
            <td>
                <div style="margin-bottom:8px;">{response_fmt}</div>
                <details><summary><small><b>Actual Tool Calls ({len(clean_actual_tools)})</b></small></summary><pre style="font-size:11px;">{actual_tools_fmt}</pre></details>
            </td>
            <td>
                <small>{context_fmt}</small>
                <details style="margin-top:6px;"><summary><small><b>Expected Tool Calls ({expected_len})</b></small></summary><pre style="font-size:11px;">{expected_tools_fmt}</pre></details>
            </td>
            <td style="text-align:center; font-weight:bold;">{r_val}</td>
            <td style="text-align:center; font-weight:bold;">{g_val}</td>
            <td style="text-align:center; font-weight:bold; color:#0078d4;">{t_val}</td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>AI Agent Evaluation Dashboard</title>
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
        pre {{ background: #f1f3f5; padding: 6px; border-radius: 4px; overflow-x: auto; margin: 4px 0 0 0; }}
    </style>
</head>
<body>
    <h1>🤖 AI Agent Evaluation Dashboard</h1>
    <div class="subtitle">Target Agent: <b>{AGENT_NAME}</b> | Judge Model: {JUDGE_MODEL_DEPLOYMENT}</div>
    
    <div class="cards">
        <div class="card status">
            <div class="card-title">Verdict Status</div>
            <div class="badge">{status_text}</div>
        </div>
        <div class="card">
            <div class="card-title">Average Score</div>
            <div class="card-value">{avg_score:.2f} <small style="font-size:14px; color:#888;">/ 5.0</small></div>
            <small style="color:#666;">Threshold: {EVAL_SCORE_THRESHOLD}</small>
        </div>
        <div class="card">
            <div class="card-title">Relevance Score</div>
            <div class="card-value">{rel_score:.2f} <small style="font-size:14px; color:#888;">/ 5.0</small></div>
        </div>
        <div class="card">
            <div class="card-title">Groundedness Score</div>
            <div class="card-value">{grd_score:.2f} <small style="font-size:14px; color:#888;">/ 5.0</small></div>
        </div>
        <div class="card">
            <div class="card-title">Tool Call Accuracy</div>
            <div class="card-value">{tool_score:.2f} <small style="font-size:14px; color:#888;">/ 5.0</small></div>
        </div>
    </div>

    <h2>Test Execution Breakdown</h2>
    <table>
        <thead>
            <tr>
                <th style="width: 3%; text-align:center;">#</th>
                <th style="width: 18%;">User Query</th>
                <th style="width: 36%;">Agent Text Output & Tool Calls</th>
                <th style="width: 27%;">Knowledge Context & Expected Tools</th>
                <th style="width: 5%; text-align:center;">Rel</th>
                <th style="width: 5%; text-align:center;">Grd</th>
                <th style="width: 6%; text-align:center;">Tool Acc</th>
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
    print(f"🧪 Initializing Evaluation Pipeline for Agent: '{AGENT_NAME}'...")

    if not os.path.exists(EVAL_DATASET_PATH):
        print(f"❌ Local Error: '{EVAL_DATASET_PATH}' not found!")
        sys.exit(1)

    # 1. Initialize Azure AI Project Client
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=credential
    )

    # 2. Fetch Target Agent ID using list() to prevent AttributeError
    print("🔍 Fetching target agent ID from project...")
    agents_list = project_client.agents.list()
    target_agent = None
    for agent in agents_list:
        if getattr(agent, "name", None) == AGENT_NAME:
            target_agent = agent
            break

    if not target_agent:
        print(f"❌ Error: Agent '{AGENT_NAME}' not found in Azure AI Project!")
        sys.exit(1)

    agent_id = target_agent.id
    print(f"✅ Resolved Target Agent ID: {agent_id}")

    # 3. Pre-process dataset and build Query-to-Context Map
    with open(EVAL_DATASET_PATH, "r", encoding="utf-8") as f:
        dataset_lines = [json.loads(line) for line in f if line.strip()]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as temp_jsonl:
        for record in dataset_lines:
            query_key = record.get("query", "").strip()
            kb_context = record.get("context") or record.get("ground_truth", "")
            QUERY_CONTEXT_MAP[query_key] = kb_context

            norm_expected = []
            for etc in record.get("expected_tool_calls", []):
                item = dict(etc) if isinstance(etc, dict) else {"name": str(etc)}
                if "type" not in item:
                    item["type"] = "tool_call"
                norm_expected.append(item)
            record["expected_tool_calls"] = norm_expected

            temp_jsonl.write(json.dumps(record) + "\n")
        temp_jsonl_path = temp_jsonl.name

    # 4. Define Target Runner against Azure Agent
    def target_agent_runner(query: str):
        query_clean = query.strip()
        kb_text = QUERY_CONTEXT_MAP.get(query_clean, "No knowledge base article found.")

        all_actual_tool_calls = []
        final_agent_response = ""

        try:
            thread = project_client.agents.create_thread()
            project_client.agents.create_message(thread_id=thread.id, role="user", content=query)
            run = project_client.agents.create_run(thread_id=thread.id, assistant_id=agent_id)

            while True:
                run = project_client.agents.get_run(thread_id=thread.id, run_id=run.id)

                if run.status == "requires_action":
                    tool_calls = run.required_action.submit_tool_outputs.tool_calls
                    tool_outputs = []

                    for tc in tool_calls:
                        raw_args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                        
                        all_actual_tool_calls.append({
                            "type": "tool_call",
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": raw_args
                        })

                        # Feed dataset ground_truth as tool response output
                        if "search" in tc.function.name.lower() or "kb" in tc.function.name.lower():
                            output_content = f"Knowledge Base Search Results: {kb_text}"
                        else:
                            output_content = json.dumps({"status": "success", "incident_number": "INC0010001"})

                        tool_outputs.append({
                            "tool_call_id": tc.id,
                            "output": output_content
                        })

                    run = project_client.agents.submit_tool_outputs(
                        thread_id=thread.id,
                        run_id=run.id,
                        tool_outputs=tool_outputs
                    )

                elif run.status == "completed":
                    messages = project_client.agents.list_messages(thread_id=thread.id)
                    msg_list = messages.data if hasattr(messages, "data") else messages
                    for msg in msg_list:
                        if msg.role == "assistant":
                            for block in msg.content:
                                if hasattr(block, "text") and hasattr(block.text, "value"):
                                    final_agent_response += block.text.value
                            break
                    break

                elif run.status in ["failed", "cancelled", "expired"]:
                    final_agent_response = "Error: Agent run failed."
                    break

                time.sleep(1)

            clean_response = final_agent_response.split("Are you satisfied")[0].split("Ticket created successfully")[0].strip()
            response_text = clean_response or final_agent_response or "Completed interaction."

            print(f"\n❓ QUERY: {query[:50]}...")
            print(f"💬 AGENT RESPONSE: {response_text[:80]}...")
            if all_actual_tool_calls:
                print(f"🛠️ EXECUTED TOOLS: {[t['name'] for t in all_actual_tool_calls]}")

            return {
                "response": response_text,
                "tool_calls": all_actual_tool_calls
            }

        except Exception as err:
            print(f"⚠️ Agent runner error on query '{query[:40]}': {err}")
            return {"response": "Error generating response.", "tool_calls": []}

    # 5. Initialize Evaluators & Model Config
    model_config = {
        "azure_endpoint": PROJECT_ENDPOINT,
        "azure_deployment": JUDGE_MODEL_DEPLOYMENT
    }

    eval_init_kwargs = {
        "model_config": model_config,
        "credential": credential
    }

    relevance_eval = RelevanceEvaluator(**eval_init_kwargs)
    groundedness_eval = GroundednessEvaluator(**eval_init_kwargs)
    tool_accuracy_eval = ToolSelectionAccuracyEvaluator()

    # 6. Execute Evaluation
    try:
        print("\n🚀 Starting Evaluation Engine against Azure AI Agent...")
        eval_result = evaluate(
            data=temp_jsonl_path,
            target=target_agent_runner,
            evaluators={
                "relevance": relevance_eval,
                "groundedness": groundedness_eval,
                "tool_accuracy": tool_accuracy_eval
            },
            evaluator_config={
                "relevance": {
                    "column_mapping": {"query": "${data.query}", "response": "${target.response}"}
                },
                "groundedness": {
                    "column_mapping": {"response": "${target.response}", "context": "${data.ground_truth}"}
                },
                "tool_accuracy": {
                    "column_mapping": {
                        "tool_calls": "${target.tool_calls}",
                        "expected_tool_calls": "${data.expected_tool_calls}"
                    }
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

        rel_score = extract_score("relevance")
        grd_score = extract_score("groundedness")
        tool_score = extract_score("tool")

        scores = [s for s in [rel_score, grd_score, tool_score] if s > 0]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        passed = (rel_score >= EVAL_SCORE_THRESHOLD and grd_score >= EVAL_SCORE_THRESHOLD and tool_score >= EVAL_SCORE_THRESHOLD)

        generate_html_report(eval_result, avg_score, rel_score, grd_score, tool_score, passed)

        print("\n" + "=" * 80)
        print("📊 EVALUATION SUMMARY DASHBOARD")
        print("=" * 80)
        print(f"🎯 Target Agent:        {AGENT_NAME}")
        print(f"   • Relevance Score:    {rel_score:.2f} / 5.0")
        print(f"   • Groundedness Score: {grd_score:.2f} / 5.0")
        print(f"   • Tool Call Accuracy: {tool_score:.2f} / 5.0")
        print(f"   • Overall Average:    {avg_score:.2f} / 5.0 (Threshold: {EVAL_SCORE_THRESHOLD})")
        print("-" * 80)

        if passed:
            print("✅ VERDICT: EVALUATION PASSED")
            sys.exit(0)
        else:
            print("❌ VERDICT: EVALUATION FAILED (One or more scores below threshold)")
            sys.exit(1)

    finally:
        if os.path.exists(temp_jsonl_path):
            os.remove(temp_jsonl_path)


if __name__ == "__main__":
    main()
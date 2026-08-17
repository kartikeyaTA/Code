import os
import sys
import json
import httpx
import tempfile
from openai import OpenAI
from azure.ai.evaluation import (
    evaluate,
    RelevanceEvaluator,
    GroundednessEvaluator,
    ToolCallAccuracyEvaluator
)

# ============================================================================
# CONFIGURATION
# ============================================================================
APIM_BASE_URL = os.environ.get(
    "APIM_OPENAI_BASE_URL",
    "https://apim-gateway-application-test-dev-txrh-mcp.azure-api.net/roadie-ranger-foundry-resource/openai/v1"
)
APIM_SUBSCRIPTION_KEY = os.environ.get("APIM_SUBSCRIPTION_KEY", "1007fc188a6d4675b308ab24a7480f47")
MODEL_DEPLOYMENT = "gpt-5.4"
EVAL_DATASET_PATH = os.environ.get("EVAL_DATASET_PATH", "snow_eval_dataset.jsonl")
TOOLS_SCHEMA_PATH = "tools_schema.json"
EVAL_SCORE_THRESHOLD = float(os.environ.get("EVAL_SCORE_THRESHOLD", "3.5"))
PROMPT_FILE_PATH = "prompt.txt"
HTML_REPORT_PATH = "eval_report.html"

# Global HTTPX Header Injection for APIM Gateway
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


def get_field_from_row(row: dict, target_key: str):
    """Extracts values safely across flat and nested evaluator row formats."""
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
    """Generates a styled HTML dashboard from evaluation outputs."""
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
        t_val = get_field_from_row(row, "tool") or "N/A"
        
        actual_tools_str = json.dumps(actual_tools, indent=1) if actual_tools else "No Tool Calls"
        expected_tools_str = json.dumps(expected_tools, indent=1) if expected_tools else "None Expected"
        
        table_rows_html += f"""
        <tr>
            <td>{idx}</td>
            <td><b>{query}</b></td>
            <td>
                <div>{response}</div>
                <details style="margin-top:6px;"><summary><small><b>Actual Tool Calls</b></small></summary><pre style="font-size:11px;">{actual_tools_str}</pre></details>
            </td>
            <td>
                <small>{context}</small>
                <details style="margin-top:6px;"><summary><small><b>Expected Tool Calls</b></summary><pre style="font-size:11px;">{expected_tools_str}</pre></details>
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
    <title>AI Agent Evaluation Summary</title>
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
    <h1>🤖 AI Agent Evaluation Report</h1>
    <div class="subtitle">Evaluated via APIM Gateway | Model: {MODEL_DEPLOYMENT}</div>
    
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
                <th style="width: 3%;">#</th>
                <th style="width: 18%;">User Query</th>
                <th style="width: 36%;">Generated Response & Tool Calls</th>
                <th style="width: 27%;">Context & Expected Tool Calls</th>
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
    print(f"📄 HTML Evaluation report generated: '{HTML_REPORT_PATH}'")


def main():
    print(f"🧪 Running evaluation test via APIM Gateway ({APIM_BASE_URL})...")

    if not os.path.exists(EVAL_DATASET_PATH) or not os.path.exists(PROMPT_FILE_PATH):
        print(f"❌ Required files missing: '{EVAL_DATASET_PATH}' or '{PROMPT_FILE_PATH}'")
        sys.exit(1)

    # 1. Format tools properly into OpenAI Chat Completion structure
    formatted_tools = []
    tool_defs_raw = []
    if os.path.exists(TOOLS_SCHEMA_PATH):
        with open(TOOLS_SCHEMA_PATH, "r", encoding="utf-8") as tf:
            tool_defs_raw = json.load(tf)
            for t in tool_defs_raw:
                if isinstance(t, dict) and "type" not in t:
                    formatted_tools.append({
                        "type": "function",
                        "function": t
                    })
                else:
                    formatted_tools.append(t)

    # 2. Inject "type": "tool_call" into expected_tool_calls and tool_definitions for dataset
    with open(EVAL_DATASET_PATH, "r", encoding="utf-8") as f:
        dataset_lines = [json.loads(line) for line in f if line.strip()]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as temp_jsonl:
        for record in dataset_lines:
            record["tool_definitions"] = tool_defs_raw
            
            # Normalize expected_tool_calls to contain "type": "tool_call"
            norm_expected = []
            for etc in record.get("expected_tool_calls", []):
                item = dict(etc)
                if "type" not in item:
                    item["type"] = "tool_call"
                norm_expected.append(item)
            record["expected_tool_calls"] = norm_expected

            temp_jsonl.write(json.dumps(record) + "\n")
        temp_jsonl_path = temp_jsonl.name

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
            kwargs = {
                "model": MODEL_DEPLOYMENT,
                "messages": [
                    {"role": "system", "content": candidate_instructions},
                    {"role": "user", "content": query}
                ],
                "max_completion_tokens": 80000
            }
            if formatted_tools:
                kwargs["tools"] = formatted_tools

            response = client.chat.completions.create(**kwargs)
            message = response.choices[0].message
            
            actual_tool_calls = []
            if getattr(message, "tool_calls", None):
                for tc in message.tool_calls:
                    actual_tool_calls.append({
                        "type": "tool_call",  # 👈 Required by Azure AI Evaluation SDK
                        "name": tc.function.name,
                        "arguments": json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                    })

            # Provide non-empty fallback string for tool-only turns
            output_text = message.content or (
                f"Agent initiated tool calls: {[t['name'] for t in actual_tool_calls]}"
                if actual_tool_calls else "No response text generated."
            )
            
            return {
                "response": output_text,
                "tool_calls": actual_tool_calls
            }
        except Exception as err:
            print(f"⚠️ Runner error on query '{query[:40]}': {err}")
            return {"response": "Error generating response.", "tool_calls": []}

    eval_init_kwargs = {
        "model_config": model_config,
        "is_reasoning_model": True
    }

    relevance_eval = RelevanceEvaluator(**eval_init_kwargs)
    groundedness_eval = GroundednessEvaluator(**eval_init_kwargs)
    tool_accuracy_eval = ToolCallAccuracyEvaluator(**eval_init_kwargs)

    try:
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
                        "query": "${data.query}",
                        "tool_calls": "${target.tool_calls}",
                        "expected_tool_calls": "${data.expected_tool_calls}",
                        "tool_definitions": "${data.tool_definitions}"
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
        passed = avg_score >= EVAL_SCORE_THRESHOLD

        generate_html_report(eval_result, avg_score, rel_score, grd_score, tool_score, passed)

        print(f"\n📊 Evaluation Summary:")
        print(f"   • Relevance Score:     {rel_score:.2f} / 5.0")
        print(f"   • Groundedness Score:  {grd_score:.2f} / 5.0")
        print(f"   • Tool Call Accuracy:  {tool_score:.2f} / 5.0")
        print(f"   • Average Score:       {avg_score:.2f} / 5.0 (Threshold: {EVAL_SCORE_THRESHOLD})")

        if passed:
            print("✅ EVALUATION PASSED")
            sys.exit(0)
        else:
            print(f"❌ EVALUATION FAILED: Average score {avg_score:.2f} is below threshold {EVAL_SCORE_THRESHOLD}.")
            sys.exit(1)

    finally:
        if os.path.exists(temp_jsonl_path):
            os.remove(temp_jsonl_path)


if __name__ == "__main__":
    main()
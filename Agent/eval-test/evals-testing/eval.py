import os
import sys
import time
import json
import html
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceExistsError
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import TestingCriterionAzureAIEvaluator

# ============================================================================
# CONFIGURATION
# ============================================================================
PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
AGENT_NAME = "txrh-demoagent-2-copy"
LOCAL_DATASET_PATH = "snow_eval_data.jsonl"
JUDGE_MODEL_DEPLOYMENT = "roadie-ranger-foundry-resource/gpt-5.4"
HTML_REPORT_PATH = "eval_report.html"
EVAL_SCORE_THRESHOLD = 3.5

if not os.path.exists(LOCAL_DATASET_PATH):
    print(f"❌ Local Error: '{LOCAL_DATASET_PATH}' not found!")
    sys.exit(1)

local_dataset_rows = []
with open(LOCAL_DATASET_PATH, "r", encoding="utf-8") as f:
    for line in f:
        if line.strip():
            local_dataset_rows.append(json.loads(line.strip()))


# ============================================================================
# CONSENT & PARSING HELPERS
# ============================================================================
def find_consent_item(response) -> dict | None:
    """Inspects response output items for an oauth_consent_request."""
    try:
        items = response.model_dump().get("output") or []
    except Exception:
        return None
    for item in items:
        if isinstance(item, dict) and item.get("type") == "oauth_consent_request":
            return item
    return None


def handle_consent_if_needed(oai_client, response, original_prompt: str):
    """If OAuth consent is required, prints the link, pauses for CLI confirmation, and resumes."""
    consent = find_consent_item(response)
    if consent is None:
        return response  # Already consented or no OAuth tool required

    link = str(consent.get("consent_link") or "")
    print("\n" + "-" * 72)
    print("🔐 OAUTH CONSENT REQUIRED - One-time setup for this tool connection")
    print(f" Tool needing sign-in : {consent.get('server_label', 'MCP Tool')}")
    print(" Open the link below IN A BROWSER SIGNED IN AS THE TARGET USER.")
    print(" The link is a SHORT-LIVED CREDENTIAL (~1 hour). Do not share it.")
    print("-" * 72)
    print(link, flush=True)

    if not sys.stdin.isatty():
        sys.exit(
            "\n❌ Error: stdin is not a terminal, so this run cannot wait for confirmation.\n"
            "Complete consent in a browser, then re-run this script.\n"
        )

    input("\n👉 Press Enter once you have completed the sign-in in the browser... ")

    # Resume from the consent item's response_id
    return oai_client.responses.create(
        input=original_prompt,
        previous_response_id=consent.get("response_id"),
    )


def clean_html(text):
    if not text or text == "N/A":
        return "N/A"
    return html.escape(str(text)).replace("\n", "<br>")


def _extract_content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif part.get("type") == "tool_result":
                    parts.append(str(part.get("tool_result", "")))
            else:
                parts.append(str(part))
        return "".join(parts).strip()
    return ""


def parse_query(data, row_idx):
    if data.get("query"):
        return str(data["query"])

    messages = data.get("messages") or (data.get("item", {}).get("messages") if isinstance(data.get("item"), dict) else None)
    if not messages and row_idx < len(local_dataset_rows):
        messages = local_dataset_rows[row_idx].get("messages")

    if isinstance(messages, list):
        for msg in messages:
            if isinstance(msg, dict) and str(msg.get("role")).lower() == "user":
                extracted = _extract_content_text(msg.get("content"))
                if extracted:
                    return extracted
    return "N/A"


def parse_ground_truth(data, row_idx):
    if data.get("ground_truth"):
        return str(data["ground_truth"])

    messages = data.get("messages") or (data.get("item", {}).get("messages") if isinstance(data.get("item"), dict) else None)
    if not messages and row_idx < len(local_dataset_rows):
        messages = local_dataset_rows[row_idx].get("messages")

    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict):
                role = str(msg.get("role")).lower()
                if role in ["assistant", "tool"]:
                    extracted = _extract_content_text(msg.get("content"))
                    if extracted:
                        return extracted
    return "N/A"


def parse_response(data):
    sample = data.get("sample")
    if isinstance(sample, dict):
        items = sample.get("output_items") or sample.get("output") or sample.get("messages") or []
        if isinstance(items, list):
            for msg in reversed(items):
                if isinstance(msg, dict):
                    role = str(msg.get("role", "")).lower()
                    if role in ["assistant", "agent", "model"]:
                        content = msg.get("content") or msg.get("text")
                        if content:
                            return _extract_content_text(content)

        out_text = sample.get("output_text")
        if out_text and isinstance(out_text, str) and not out_text.startswith("Role & Objective"):
            return out_text.strip()

    def find_assistant_content(obj):
        if isinstance(obj, dict):
            role = str(obj.get("role", "")).lower()
            if role in ["assistant", "agent", "model"] and ("content" in obj or "text" in obj):
                c = obj.get("content") or obj.get("text")
                if c:
                    return _extract_content_text(c)
            for k, v in obj.items():
                if k in ["input", "input_messages", "item", "system_prompt"]:
                    continue
                res = find_assistant_content(v)
                if res:
                    return res
        elif isinstance(obj, list):
            for item in reversed(obj):
                res = find_assistant_content(item)
                if res:
                    return res
        return None

    res = find_assistant_content(data)
    return res if res else "N/A"


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
                if target in k.lower():
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

    return deep_score(data)


def generate_html_report(rows_data, avg_rel, avg_grd, overall_avg, passed):
    status_color = "#107c41" if passed else "#d13438"
    status_text = "PASSED" if passed else "FAILED"

    table_rows_html = ""
    for idx, r in enumerate(rows_data, 1):
        rel_str = f"{r['relevance']:.1f} / 5.0" if r['relevance'] is not None else "N/A"
        grd_str = f"{r['groundedness']:.1f} / 5.0" if r['groundedness'] is not None else "N/A"

        table_rows_html += f"""
        <tr>
            <td style="text-align:center;">{idx}</td>
            <td><b>{clean_html(r['query'])}</b></td>
            <td style="max-width: 500px; word-wrap: break-word;">{clean_html(r['response'])}</td>
            <td style="max-width: 300px; word-wrap: break-word;"><small>{clean_html(r['ground_truth'])}</small></td>
            <td style="text-align:center; font-weight:bold; color:#0078d4;">{rel_str}</td>
            <td style="text-align:center; font-weight:bold; color:#107c41;">{grd_str}</td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>AI Agent Evaluation Report</title>
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
    </style>
</head>
<body>
    <h1>🤖 AI Agent Evaluation Report</h1>
    <div class="subtitle">Target Agent: <b>{AGENT_NAME}</b> | Judge Model: {JUDGE_MODEL_DEPLOYMENT}</div>
    
    <div class="cards">
        <div class="card status">
            <div class="card-title">Verdict Status</div>
            <div class="badge">{status_text}</div>
        </div>
        <div class="card">
            <div class="card-title">Overall Score</div>
            <div class="card-value">{overall_avg:.2f} <small style="font-size:14px; color:#888;">/ 5.0</small></div>
            <small style="color:#666;">Threshold: {EVAL_SCORE_THRESHOLD}</small>
        </div>
        <div class="card">
            <div class="card-title">Relevance Score</div>
            <div class="card-value">{avg_rel:.2f} <small style="font-size:14px; color:#888;">/ 5.0</small></div>
        </div>
        <div class="card">
            <div class="card-title">Groundedness Score</div>
            <div class="card-value">{avg_grd:.2f} <small style="font-size:14px; color:#888;">/ 5.0</small></div>
        </div>
    </div>

    <h2>Test Row Breakdown</h2>
    <table>
        <thead>
            <tr>
                <th style="width: 4%; text-align:center;">#</th>
                <th style="width: 22%;">User Query</th>
                <th style="width: 42%;">Generated Agent Response</th>
                <th style="width: 22%;">Ground Truth Context</th>
                <th style="width: 5%; text-align:center;">Relevance</th>
                <th style="width: 5%; text-align:center;">Groundedness</th>
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
# EXECUTION
# ============================================================================
with AIProjectClient(
    endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential(),
    allow_preview=True
) as project_client:

    # 1. Upload Dataset
    dataset_name = "snow-agent-eval-dataset"
    dynamic_version = str(int(time.time()))

    print(f"📤 Uploading local dataset '{LOCAL_DATASET_PATH}'...")
    try:
        dataset = project_client.datasets.upload_file(
            name=dataset_name,
            version=dynamic_version,
            file_path=LOCAL_DATASET_PATH
        )
    except ResourceExistsError:
        dataset = project_client.datasets.get(name=dataset_name, version="1")

    openai_client = project_client.get_openai_client()

    # 2. Pre-Flight OAuth Consent Verification
    print("🔑 Verifying OAuth consent status for Agent...")
    #try:
    agent_oai = project_client.get_openai_client(agent_name=AGENT_NAME)
    test_prompt = local_dataset_rows[0].get("query", "Hello") if local_dataset_rows else "Hello"
    initial_resp = agent_oai.responses.create(input=test_prompt)
    handle_consent_if_needed(agent_oai, initial_resp, test_prompt)
    print("✅ OAuth Authentication confirmed. Proceeding to batch evaluation.\n")
    # except Exception as ex:
    #     print(f"⚠️ Pre-flight check warning: {ex}. Attempting run...\n")

    # 3. Register Evaluation Definition
    data_source_config = {
        "type": "custom",
        "item_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "ground_truth": {"type": "string"},
                "messages": {"type": "array"}
            },
            "required": ["query"]
        },
        "include_sample_schema": True
    }

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

    evaluation = openai_client.evals.create(
        name=f"Agent_Quality_Eval_{AGENT_NAME}",
        data_source_config=data_source_config,
        testing_criteria=testing_criteria
    )

    # 4. Trigger Evaluation Run
    print(f"🚀 Triggered evaluation run for agent '{AGENT_NAME}'...")
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
                        "content": {
                            "type": "input_text",
                            "text": "{{item.query}}"
                        }
                    }
                ]
            },
            "target": {
                "type": "azure_ai_agent",
                "name": AGENT_NAME
            }
        }
    )

    # 5. Status Polling
    terminal_states = {"completed", "failed", "errored", "canceled", "partially_completed"}
    sys.stdout.write("⏳ Polling job status: ")
    sys.stdout.flush()

    while True:
        run_status_obj = openai_client.evals.runs.retrieve(
            eval_id=evaluation.id,
            run_id=eval_run.id
        )
        current_status = getattr(run_status_obj, "status", "unknown").lower()
        sys.stdout.write("▪")
        sys.stdout.flush()

        if current_status in terminal_states or any(k in current_status for k in ["complete", "error", "fail"]):
            print(f" [{current_status.upper()}]")
            break
        time.sleep(6)

    # 6. Extract Results & Compute Metrics
    output_items = list(openai_client.evals.runs.output_items.list(
        eval_id=evaluation.id,
        run_id=eval_run.id
    ))

    rows_summary = []
    rel_scores, grd_scores = [], []

    for idx, item in enumerate(output_items):
        data = item.model_dump() if hasattr(item, "model_dump") else (item if isinstance(item, dict) else vars(item))

        query = parse_query(data, idx)
        ground_truth = parse_ground_truth(data, idx)
        response = parse_response(data)

        rel = parse_metric_score(data, "relevance")
        grd = parse_metric_score(data, "groundedness")

        if rel is not None:
            rel_scores.append(rel)
        if grd is not None:
            grd_scores.append(grd)

        rows_summary.append({
            "query": query,
            "ground_truth": ground_truth,
            "response": response,
            "relevance": rel,
            "groundedness": grd
        })

    avg_rel = sum(rel_scores) / len(rel_scores) if rel_scores else 0.0
    avg_grd = sum(grd_scores) / len(grd_scores) if grd_scores else 0.0
    all_scores = rel_scores + grd_scores
    overall_avg = sum(all_scores) / len(all_scores) if all_scores else 0.0
    passed = overall_avg >= EVAL_SCORE_THRESHOLD

    # 7. Generate HTML Report
    generate_html_report(rows_summary, avg_rel, avg_grd, overall_avg, passed)

    # 8. Print Clean Terminal Summary
    print("\n" + "=" * 80)
    print("📊 EVALUATION SUMMARY DASHBOARD")
    print("=" * 80)
    print(f"🎯 Target Agent:        {AGENT_NAME}")
    print(f"📌 Evaluation Run ID:   {eval_run.id}")
    print(f"🏁 Final Status:         {getattr(run_status_obj, 'status', 'COMPLETED')}")
    print(f"🔢 Total Evaluated Rows: {len(rows_summary)}")
    print("-" * 80)
    print(f"   • Relevance Score:    {avg_rel:.2f} / 5.0")
    print(f"   • Groundedness Score: {avg_grd:.2f} / 5.0")
    print(f"   • Overall Average:    {overall_avg:.2f} / 5.0 (Threshold: {EVAL_SCORE_THRESHOLD})")
    print("-" * 80)
    
    if passed:
        print("✅ VERDICT: EVALUATION PASSED")
    else:
        print("❌ VERDICT: EVALUATION FAILED (Below Threshold)")
        
    print(f"\n📄 Styled HTML Dashboard generated: '{HTML_REPORT_PATH}'")
    print("=" * 80 + "\n")
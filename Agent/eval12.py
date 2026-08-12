"""
publish_with_eval.py

WHAT THIS DOES
1. Reads your new prompt from a text file.
2. Compares it to what's currently published -- if nothing changed, stops.
3. If changed: creates a throwaway "candidate" agent with the SAME model
   and tools as your real agent (copied automatically), but the new prompt.
4. Runs your test questions (eval_dataset.jsonl) against that candidate.
5. Scores each answer using the SAME model/connection that's already
   working for your agent -- no separate judge endpoint, no cognitive
   services URL, nothing extra to configure.
6. If the average score passes your threshold, publishes a new version of
   your REAL agent. If not, stops -- nothing published.
7. Either way, deletes the throwaway candidate agent.

BEFORE YOU RUN
- pip install azure-ai-projects azure-identity openai
  (azure-ai-evaluation is NOT needed anymore -- scoring is done directly
  through the same connection your agent already uses)
- Run `az login` once (or make sure your pipeline identity is logged in).
- Fill in the 4 values in the CONFIG block right below.
"""

import os
import sys
import json
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition


# ============================================================================
# CONFIG -- fill these 4 in. Everything else (model, tools, judge model) is
# copied automatically from your existing agent.
# ============================================================================

PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
AGENT_NAME = "txrh-demoagent-2-copy-1234"
PROMPT_FILE_PATH = "prompt.txt"           # your new instructions, plain text
EVAL_DATASET_PATH = "eval_dataset1.jsonl"  # your test questions
EVAL_SCORE_THRESHOLD = 3.5                # 1-5 scale, pick your bar

# ============================================================================
# Nothing below this line needs editing for normal use.
# ============================================================================

candidate_agent_name = f"{AGENT_NAME}-eval-candidate"

# Two SEPARATE prompts, deliberately isolated from each other -- this
# matches how Azure's own evaluators work (relevance never sees the
# reference; groundedness never sees the query). Mixing both dimensions
# into one call let a bad/mismatched reference contaminate the relevance
# score, which is what caused the inconsistent scores you saw.

RELEVANCE_PROMPT_TEMPLATE = """You are grading whether an assistant's answer addresses the user's question.
Judge ONLY relevance -- whether the response is on-topic and answers what
was asked. You have no reference answer to compare against; don't guess at
factual correctness, only whether it addresses the question asked.

The response may contain operational side-effects (ticket-creation
confirmations, ticket numbers, "are you satisfied?" prompts) mixed in.
Ignore that part entirely -- judge only the substantive answer.

Score 1-5:
  1 = does not address the question at all
  2 = addresses a different or tangential question
  3 = partially addresses the question, missing key parts
  4 = addresses the question well, minor gaps
  5 = directly and completely addresses the question

Respond with ONLY a JSON object, nothing else, like: {{"relevance": 4}}

USER QUERY:
{query}

ASSISTANT RESPONSE:
{response}
"""

GROUNDEDNESS_PROMPT_TEMPLATE = """You are grading whether an assistant's answer is consistent with a reference,
with no invented or contradicted facts. You do NOT know what question was
asked -- judge purely whether RESPONSE is supported by REFERENCE.

The response may contain operational side-effects (ticket-creation
confirmations, ticket numbers, "are you satisfied?" prompts) mixed in.
Ignore that part entirely -- judge only the substantive answer content.

The response is allowed to include more specific or detailed information
than the reference (extra steps, exact numbers, a citation ID). That is
normal elaboration, not a groundedness problem -- only penalize for claims
that actively CONTRADICT the reference or are clearly fabricated.

Score 1-5:
  1 = contradicts the reference or is entirely made up
  2 = mostly unsupported, significant unsupported claims
  3 = broadly consistent with the reference but some unverifiable additions
  4 = consistent with the reference; extra detail is plausible elaboration, not contradiction
  5 = fully consistent with the reference, nothing contradicted or fabricated

Respond with ONLY a JSON object, nothing else, like: {{"groundedness": 5}}

REFERENCE:
{ground_truth}

ASSISTANT RESPONSE:
{response}
"""


def get_text_from_definition(definition, key, default=""):
    """Definitions can come back as a dict or an object depending on SDK
    version -- this reads either shape safely."""
    if isinstance(definition, dict):
        return definition.get(key, default)
    return getattr(definition, key, default)


def load_dataset(path: str) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run_candidate_agent(openai_client, agent_name: str, query: str) -> str:
    """Calls the agent purely by name -- model/tools/routing come from the
    agent's own definition, nothing else needed."""
    response = openai_client.responses.create(
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
        input=query,
    )
    return response.output_text


def _call_judge(openai_client, judge_model: str, prompt: str, expected_key: str) -> float:
    result = openai_client.responses.create(
        model=judge_model,
        instructions="You are a strict, consistent evaluator. Always respond with valid JSON only, no commentary.",
        input=prompt,
    )
    raw = (result.output_text or "").strip()
    cleaned = raw.strip("`")
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        data = json.loads(cleaned)
        return float(data.get(expected_key, 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        print(f"⚠️ Could not parse judge output for {expected_key}, scoring as 0: {raw!r}")
        return 0.0


def judge_response(openai_client, judge_model: str, query: str, response_text: str, ground_truth: str) -> tuple[float, float]:
    """
    Scores a response using the SAME model/connection your agent already
    calls -- ephemeral (non-persisted) calls: just model + instructions,
    no agent resource, no separate endpoint config.

    Deliberately TWO separate calls, each seeing only the inputs relevant
    to that dimension -- relevance never sees the reference, groundedness
    never sees the query -- so a bad/mismatched reference can't leak into
    the relevance score, or vice versa.
    """
    relevance = _call_judge(
        openai_client, judge_model,
        RELEVANCE_PROMPT_TEMPLATE.format(query=query, response=response_text),
        "relevance",
    )
    groundedness = _call_judge(
        openai_client, judge_model,
        GROUNDEDNESS_PROMPT_TEMPLATE.format(ground_truth=ground_truth or "(none provided)", response=response_text),
        "groundedness",
    )
    return relevance, groundedness


def run_evaluation(openai_client, judge_model: str) -> bool:
    print(f"\n🧪 Running eval against candidate agent '{candidate_agent_name}'...")

    if not os.path.exists(EVAL_DATASET_PATH):
        print(f"❌ Can't find eval dataset at '{EVAL_DATASET_PATH}'.")
        return False

    dataset = load_dataset(EVAL_DATASET_PATH)
    if not dataset:
        print(f"❌ Eval dataset '{EVAL_DATASET_PATH}' is empty.")
        return False

    relevance_scores = []
    groundedness_scores = []

    for i, row in enumerate(dataset, start=1):
        query = row["query"]
        ground_truth = row.get("ground_truth", "")

        print(f"[{i}/{len(dataset)}] asking candidate: {query[:60]!r}")
        try:
            response_text = run_candidate_agent(openai_client, candidate_agent_name, query)
        except Exception as err:
            print(f"⚠️ Error running candidate agent: {err}")
            response_text = "Error generating response."

        relevance, groundedness = judge_response(openai_client, judge_model, query, response_text, ground_truth)
        print(f"    relevance={relevance:.1f}  groundedness={groundedness:.1f}")
        relevance_scores.append(relevance)
        groundedness_scores.append(groundedness)

    avg_relevance = sum(relevance_scores) / len(relevance_scores)
    avg_groundedness = sum(groundedness_scores) / len(groundedness_scores)
    avg_score = (avg_relevance + avg_groundedness) / 2.0

    print(f"\n📈 Results:")
    print(f"   Relevance:    {avg_relevance:.2f} / 5.0")
    print(f"   Groundedness: {avg_groundedness:.2f} / 5.0")
    print(f"   Average:      {avg_score:.2f} / 5.0  (threshold: {EVAL_SCORE_THRESHOLD})")

    if avg_score >= EVAL_SCORE_THRESHOLD:
        print("✅ EVAL PASSED")
        return True
    print("❌ EVAL FAILED")
    return False


def main():
    if not os.path.exists(PROMPT_FILE_PATH):
        print(f"📁 '{PROMPT_FILE_PATH}' not found. Creating a starter file -- edit it and re-run.")
        with open(PROMPT_FILE_PATH, "w", encoding="utf-8") as f:
            f.write("You are a helpful assistant.")
        return

    with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
        new_instructions = f.read().strip()

    print("🚀 Connecting to Foundry project...")
    credential = DefaultAzureCredential()

    with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential, allow_preview=True) as client, \
         client.get_openai_client() as openai_client:

        try:
            existing_agent = client.agents.get(agent_name=AGENT_NAME)
        except ResourceNotFoundError:
            print(f"❌ Agent '{AGENT_NAME}' not found. Double-check PROJECT_ENDPOINT and AGENT_NAME.")
            sys.exit(1)

        current_definition = existing_agent.versions.latest.definition
        current_instructions = get_text_from_definition(current_definition, "instructions", "")
        current_model = get_text_from_definition(current_definition, "model", "")
        current_tools = get_text_from_definition(current_definition, "tools", [])

        if current_instructions.strip() == new_instructions.strip():
            print("ℹ️ Prompt hasn't changed -- nothing to do.")
            return

        print("🔔 Prompt change detected. Building candidate agent for testing...")
        print(f"   (reusing existing model '{current_model}' and existing tools automatically)")

        candidate_agent = client.agents.create_version(
            agent_name=candidate_agent_name,
            definition=PromptAgentDefinition(
                model=current_model,
                instructions=new_instructions,
                tools=current_tools,
            ),
        )

        try:
            passed = run_evaluation(openai_client, judge_model=current_model)
        finally:
            try:
                client.agents.delete_version(candidate_agent.name, candidate_agent.version)
                print(f"🧹 Removed candidate agent '{candidate_agent.name}'.")
            except Exception as cleanup_err:
                print(f"⚠️ Could not remove candidate agent (safe to clean up manually): {cleanup_err}")

        if not passed:
            print("\n⛔ Publish blocked -- eval did not pass.")
            sys.exit(1)

        print("\n🚀 Eval passed. Publishing new version...")
        new_version = client.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(
                model=current_model,
                instructions=new_instructions,
                tools=current_tools,
            ),
        )
        print(f"🎯 Published '{AGENT_NAME}' version {new_version.version}.")


if __name__ == "__main__":
    main()
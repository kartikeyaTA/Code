"""
publish_with_eval.py

WHAT THIS DOES
1. Reads your new prompt from a text file.
2. Compares it to what's currently published -- if nothing changed, stops.
3. If it changed: creates a throwaway "candidate" agent with the SAME model
   and tools as your real agent (copied automatically, you don't set these),
   but with the new prompt.
4. Runs your test questions (eval_dataset.jsonl) against that candidate.
5. Scores the answers. If the average score is >= your threshold, it
   publishes a new version of your REAL agent with the new prompt.
   If not, it stops and nothing is published.
6. Either way, the throwaway candidate agent is deleted.

BEFORE YOU RUN
- pip install azure-ai-projects azure-ai-evaluation azure-identity openai
- Run `az login` once (or make sure your pipeline identity is logged in).
- Fill in the 5 values in the CONFIG block right below.
"""

import os
import sys
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.ai.evaluation import evaluate, RelevanceEvaluator, GroundednessEvaluator


# ============================================================================
# CONFIG -- fill these 5 in. See the chat message for exactly where to find
# each one. Everything else (model, tools) is copied automatically from
# your existing agent, so you don't need to know or set those.
# ============================================================================

PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
AGENT_NAME = "txrh-demoagent-2-copy-1234"
PROMPT_FILE_PATH = "prompt.txt"          # your new instructions, plain text
EVAL_DATASET_PATH = "eval_dataset1.jsonl"  # your test questions
EVAL_SCORE_THRESHOLD = 3.5                # 1-5 scale, pick your bar
TARGET_ENDPOINT = "https://roadie-ranger-foundry-resource.cognitiveservices.azure.com/"

# ============================================================================
# Nothing below this line needs editing for normal use.
# ============================================================================

candidate_agent_name = f"{AGENT_NAME}-eval-candidate"


def get_text_from_definition(definition, key, default=""):
    """Definitions can come back as a dict or an object depending on SDK
    version -- this reads either shape safely."""
    if isinstance(definition, dict):
        return definition.get(key, default)
    return getattr(definition, key, default)


def run_candidate_agent(openai_client, agent_name: str, query: str) -> str:
    """Calls the agent purely by name -- model/tools/routing come from the
    agent's own definition, nothing else needed."""
    response = openai_client.responses.create(
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
        input=query,
    )
    return response.output_text


def run_evaluation(openai_client, credential, judge_model: str) -> bool:
    print(f"\n🧪 Running eval against candidate agent '{candidate_agent_name}'...")

    if not os.path.exists(EVAL_DATASET_PATH):
        print(f"❌ Can't find eval dataset at '{EVAL_DATASET_PATH}'.")
        return False

    def target_agent_runner(query: str):
        try:
            return {"response": run_candidate_agent(openai_client, candidate_agent_name, query)}
        except Exception as err:
            print(f"⚠️ Error running candidate agent for query {query[:60]!r}: {err}")
            return {"response": "Error generating response."}

    # This is the ONE model reference the script needs -- purely to grade
    # answers, not to run the agent. Defaults to the same model your agent
    # already uses, so you don't need to name a different one.

    model_config = {
        "azure_endpoint": TARGET_ENDPOINT,
        "azure_deployment": judge_model,
        "api_version": "2024-10-21"
    }

    relevance_eval = RelevanceEvaluator(model_config=model_config, is_reasoning_model=True)
    groundedness_eval = GroundednessEvaluator(model_config=model_config, is_reasoning_model=True)

    try:
        eval_result = evaluate(
            data=EVAL_DATASET_PATH,
            target=target_agent_runner,
            evaluators={"relevance": relevance_eval, "groundedness": groundedness_eval},
            evaluator_config={
                "relevance": {"query": "${data.query}", "response": "${target.response}"},
                "groundedness": {"response": "${target.response}", "context": "${data.ground_truth}"},
            },
        )
        metrics = eval_result.get("metrics", {})

        def extract_score(key):
            for k, v in metrics.items():
                if key in k and isinstance(v, (int, float)):
                    return float(v)
            return 0.0

        relevance_score = extract_score("relevance")
        groundedness_score = extract_score("groundedness")
        avg_score = (relevance_score + groundedness_score) / 2.0 if (relevance_score and groundedness_score) else 0.0

        print(f"   Relevance:    {relevance_score:.2f} / 5.0")
        print(f"   Groundedness: {groundedness_score:.2f} / 5.0")
        print(f"   Average:      {avg_score:.2f} / 5.0  (threshold: {EVAL_SCORE_THRESHOLD})")

        if avg_score >= EVAL_SCORE_THRESHOLD:
            print("✅ EVAL PASSED")
            return True
        print("❌ EVAL FAILED")
        return False

    except Exception as e:
        print(f"❌ Evaluation error: {e}")
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

        # Look up the currently published agent
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
            passed = run_evaluation(openai_client, credential, judge_model=current_model)
        finally:
            try:
                client.agents.delete_version(candidate_agent.name, candidate_agent.version)
                print(f"🧹 Removed candidate agent '{candidate_agent.name}'.")
            except Exception as cleanup_err:
                print(f"⚠️ Could not remove candidate agent (safe to ignore/clean up manually): {cleanup_err}")

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
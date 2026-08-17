import argparse
import sys
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

# ============================================================================
# CONFIGURATION
# ============================================================================
PROJECT_ENDPOINT = "https://txrh-foundry.services.ai.azure.com/api/projects/txrh-project"
MAIN_AGENT_NAME = "txrh-demoagent-2-copy"
CANDIDATE_AGENT_NAME = f"{MAIN_AGENT_NAME}-eval-candidate"


def main():
   
    credential = DefaultAzureCredential()

    # 1. Always Delete the Candidate Agent
    print(f"🧹 Cleaning up candidate agent '{CANDIDATE_AGENT_NAME}'...")
    with AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential, allow_preview=True) as client:
        try:
            client.agents.delete(agent_name=CANDIDATE_AGENT_NAME)
            print(f"✅ Deleted candidate agent '{CANDIDATE_AGENT_NAME}'.")
        except Exception as cleanup_err:
            print(f"⚠️ Could not delete candidate agent (may already be removed): {cleanup_err}")
    
    print("✅ Cleanup complete. Ready for deployment stage.")


if __name__ == "__main__":
    main()
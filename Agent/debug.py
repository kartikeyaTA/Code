from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

# Replace this with the exact endpoint found in Step 1
ENDPOINT = "https://txrh-foundry.cognitiveservices.azure.com/" 
DEPLOYMENT = "gpt-5.4"

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), 
    "https://cognitiveservices.azure.com/.default"
)

client = AzureOpenAI(
    azure_endpoint=ENDPOINT,
    azure_ad_token_provider=token_provider,
    api_version="2024-10-21"
)

try:
    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=[{"role": "user", "content": "ping"}],
        max_completion_tokens=5
    )
    print(" SUCCESS! Valid Endpoint and Deployment combination.")
    print("Response:", response.choices[0].message.content)
except Exception as e:
    print(" FAILED:", e)
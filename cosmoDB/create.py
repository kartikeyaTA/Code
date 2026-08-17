import os
from azure.cosmos import CosmosClient, PartitionKey, exceptions
from azure.identity import DefaultAzureCredential

ENDPOINT = os.environ.get('COSMOS_ENDPOINT', 'https://txrh-comos-nosql-db.documents.azure.com:443/')
# KEY is no longer needed since we are using Managed Identity
DATABASE_NAME = 'txrh-db'
CONTAINER_NAME = 'conversations'

def setup_cosmos_container():
    # 1. Initialize the DefaultAzureCredential
    # This automatically picks up the Managed Identity when running in Azure (App Service, Functions, VM, etc.)
    # When running locally, it can use your Azure CLI or Visual Studio Code login.
    credential = DefaultAzureCredential()

    # 2. Pass the credential object to the CosmosClient instead of the string key
    print(f"Connecting to Cosmos DB at {ENDPOINT} using Managed Identity...")
    client = CosmosClient(ENDPOINT, credential=credential)

    print(f"Creating or retrieving database '{DATABASE_NAME}'...")
    database = client.create_database_if_not_exists(id=DATABASE_NAME)

    # Define the Partition Key
    # The path MUST start with a forward slash '/'
    partition_key = PartitionKey(path="/conversation_id")

    print(f"Creating or retrieving container '{CONTAINER_NAME}'...")

    try:
        # Create the container
        container = database.create_container_if_not_exists(
            id=CONTAINER_NAME,
            partition_key=partition_key,
            offer_throughput=400, # Provision 400 RU/s manually
        )
        print(f"Success! Container '{container.id}' is ready.")

    except exceptions.CosmosHttpResponseError as e:
        print(f"An error occurred: {e.message}")

if __name__ == '__main__':
    setup_cosmos_container()

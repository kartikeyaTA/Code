import os
from azure.cosmos import CosmosClient, exceptions
from azure.identity import DefaultAzureCredential

# Get the Cosmos DB endpoint from environment variables
ENDPOINT = os.environ.get('COSMOS_ENDPOINT', 'https://txrh-comos-nosql-db.documents.azure.com/')

def list_cosmos_databases():
    """
    Connects to Cosmos DB using Managed Identity and lists all databases.
    """
    try:
        # Initialize DefaultAzureCredential to handle Managed Identity
        print("Authenticating with Managed Identity...")
        credential = DefaultAzureCredential()

        # Connect to Cosmos DB using the credential instead of a key
        print(f"Connecting to Cosmos DB at {ENDPOINT}...")
        client = CosmosClient(ENDPOINT, credential=credential)

        print("\nRetrieving databases...")
        # list_databases() returns an iterator of dictionaries containing database metadata
        databases = list(client.list_databases())

        if not databases:
            print("No databases found in this account.")
        else:
            print(f"Found {len(databases)} database(s):")
            for db in databases:
                # 'id' is the standard key for the name in Cosmos DB metadata
                print(f" - {db['id']}")

    except exceptions.CosmosHttpResponseError as e:
        print(f"\nFailed to retrieve databases.")
        print(f"Cosmos DB Error: {e.message}")
        print("Ensure your Managed Identity has the 'Cosmos DB Built-in Data Reader' or 'Contributor' role.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {str(e)}")

if __name__ == '__main__':
    list_cosmos_databases()

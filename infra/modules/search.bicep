param searchServiceName string = 'aisearch-chat-${uniqueString(resourceGroup().id)}'

@description('The location for the AI Search resource.')
param location string 
@description('The pricing tier of the search service.')
@allowed([
  'free'
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param sku string = 'basic'

resource searchService 'Microsoft.Search/searchServices@2023-11-01' = {
  name: toLower(searchServiceName)
  location: location
  sku: {
    name: sku
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    // Enables both API keys and Role-Based Access Control (RBAC) for security flexibility
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

output searchServiceEndpoint string = 'https://${searchServiceName}.search.windows.net'
output searchServiceName string = searchService.name
metadata description = 'Provisions the private Azure AI Foundry Hub, Child Project workspace, and Cognitive Services baseline account.'

param envName string
param location string 
param vnetId string
param endpointsSubnetId string
param keyVaultId string
param storageAccountId string

var aiHubName = 'ai-hub-chat-${envName}'
var aiProjectName = 'ai-project-chat-${envName}'
var cognitiveAccountName = 'cog-openai-chat2-${envName}'
var openAiDnsZoneName = 'privatelink.openai.azure.com'
var openAiPrivateEndpointName = 'pe-openai-core-${envName}'

// 1. Cognitive Services / Azure OpenAI Account Engine
resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: cognitiveAccountName
  location: location
  sku: {
    name: 'S0' // Standard Tier
  }
  kind: 'OpenAI'
  properties: {
    publicNetworkAccess: 'Disabled' // ◄ Crucial: Blocks public web access completely
    customSubDomainName: cognitiveAccountName
  }
}

// 2. Azure AI Foundry Governance Hub Workspace
resource aiHub 'Microsoft.MachineLearningServices/workspaces@2024-04-01-preview' = {
  name: aiHubName
  location: location
  kind: 'Hub' // Specifies this as a parent corporate governance control hub
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    friendlyName: 'AI Chat Platform Master Hub'
    storageAccount: storageAccountId
    keyVault: keyVaultId
    publicNetworkAccess: 'Disabled' // Isolates the control plane from the public web
  }
}

// Connect the Cognitive Services Engine to the AI Hub as an official Infrastructure Service
resource hubServicesConnection 'Microsoft.MachineLearningServices/workspaces/connections@2024-04-01-preview' = {
  parent: aiHub
  name: 'connection-openai-compute'
  properties: {
    category: 'AzureOpenAI'
    target: cognitiveAccount.properties.endpoint
    authType: 'AAD' // ◄ Forces passwordless Microsoft Entra ID authentication for connections
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: cognitiveAccount.id
    }
  }
}

// 3. Azure AI Foundry Child Project (The operational environment workspace for the containers)
resource aiProject 'Microsoft.MachineLearningServices/workspaces@2024-04-01-preview' = {
  name: aiProjectName
  location: location
  kind: 'Project' // Specifies this as a working project environment linked to a parent hub
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    friendlyName: 'AI Chat Production Workspace'
    hubResourceId: aiHub.id // Explicit link to parent hub permissions configuration
  }
}

// 4. Private Endpoint to inject the AI Compute engine into the VNet
resource openAiPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: openAiPrivateEndpointName
  location: location
  properties: {
    subnet: {
      id: endpointsSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'openai-link-connection'
        properties: {
          privateLinkServiceId: cognitiveAccount.id
          groupIds: [
            'account'
          ]
        }
      }
    ]
  }
}

// 5. Private DNS Zone for Local Workspace Name Translation
resource openAiDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: openAiDnsZoneName
  location: 'global'
}

resource dnsZoneVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: openAiDnsZone
  name: 'link-${cognitiveAccountName}-vnet'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

resource dnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: openAiPrivateEndpoint
  name: 'openaiPrivateDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'openai-config'
        properties: {
          privateDnsZoneId: openAiDnsZone.id
        }
      }
    ]
  }
}

// Export references for application orchestration setup
output aiHubId string = aiHub.id
output aiProjectId string = aiProject.id
output openAiEndpoint string = cognitiveAccount.properties.endpoint
output openAiAccountName string = cognitiveAccount.name
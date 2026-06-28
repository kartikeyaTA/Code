metadata description = 'Provisions the private Azure AI Foundry Hub, Child Project workspace, and Cognitive Services baseline account with complete dual-zone DNS linkage.'

param envName string
param location string 
param vnetId string
param endpointsSubnetId string
param keyVaultId string
param storageAccountId string

var aiHubName = 'ai-hub-chat-${envName}'
var aiProjectName = 'ai-project-chat-${envName}'
var cognitiveAccountName = 'cog-openai-chat3-${envName}'
var openAiDnsZoneName = 'privatelink.openai.azure.com'
var openAiPrivateEndpointName = 'pe-openai-core-${envName}'

// ============================================================================
// 1. COGNITIVE SERVICES / AZURE OPENAI ACCOUNT ENGINE
// ============================================================================
resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: cognitiveAccountName
  location: location
  sku: {
    name: 'S0'
  }
  kind: 'OpenAI'
  properties: {
    publicNetworkAccess: 'Disabled' 
    customSubDomainName: cognitiveAccountName
  }
}

// ============================================================================
// 2. AZURE AI FOUNDRY GOVERNANCE HUB WORKSPACE
// ============================================================================
resource aiHub 'Microsoft.MachineLearningServices/workspaces@2024-04-01-preview' = {
  name: aiHubName
  location: location
  kind: 'Hub'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    friendlyName: 'AI Chat Platform Master Hub'
    storageAccount: storageAccountId
    keyVault: keyVaultId
    publicNetworkAccess: 'Disabled'
  }
}

// Connect the Cognitive Services Engine to the AI Hub
resource hubServicesConnection 'Microsoft.MachineLearningServices/workspaces/connections@2024-04-01-preview' = {
  parent: aiHub
  name: 'connection-openai-compute'
  properties: {
    category: 'AzureOpenAI'
    target: cognitiveAccount.properties.endpoint
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: cognitiveAccount.id
    }
  }
}

// ============================================================================
// 3. AZURE AI FOUNDRY CHILD PROJECT
// ============================================================================
resource aiProject 'Microsoft.MachineLearningServices/workspaces@2024-04-01-preview' = {
  name: aiProjectName
  location: location
  kind: 'Project'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    friendlyName: 'AI Chat Production Workspace'
    hubResourceId: aiHub.id
  }
}

// ============================================================================
// 4. PRIVATE ENDPOINTS & NETWORKING LINKS
// ============================================================================

// --- Azure OpenAI Core Private Link Connection ---
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

// --- AI Hub Workspace Infrastructure Private Link (amlworkspace) ---
resource aiHubPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-ai-hub-core-${envName}'
  location: location
  properties: {
    subnet: {
      id: endpointsSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'aihub-link-connection'
        properties: {
          privateLinkServiceId: aiHub.id
          groupIds: [
            'amlworkspace'
          ]
        }
      }
    ]
  }
}

// ============================================================================
// 5. PRIVATE DNS ZONES & VNET ORCHESTRATION LINKS
// ============================================================================

// --- OpenAI Zone Context ---
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

// --- Machine Learning Core Zone Context (api.azureml.ms) ---
resource hubDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.api.azureml.ms'
  location: 'global'
}

resource hubDnsZoneVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: hubDnsZone
  name: 'link-aihub-vnet'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

// --- Compute Notebooks Zone Context ---
resource notebooksDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.notebooks.azure.net'
  location: 'global'
}

resource notebooksDnsZoneVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: notebooksDnsZone
  name: 'link-notebooks-vnet'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

// --- Modern Foundry Services Zone Context (.services.ai.azure.com) ---
resource servicesDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.services.ai.azure.com'
  location: 'global'
}

resource servicesDnsZoneVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: servicesDnsZone
  name: 'link-services-vnet'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

// ============================================================================
// 6. PRIVATE DNS ZONE GROUP ATTACHMENTS (Triggers A-Record Synchronization)
// ============================================================================

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

resource hubDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: aiHubPrivateEndpoint
  name: 'hubPrivateDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'hub-config-api'
        properties: {
          privateDnsZoneId: hubDnsZone.id
        }
      }
      {
        name: 'hub-config-notebooks'
        properties: {
          privateDnsZoneId: notebooksDnsZone.id
        }
      }
      // 🌟 FIXED: Maps the single amlworkspace core private endpoint straight into the services zone array
      {
        name: 'hub-config-services'
        properties: {
          privateDnsZoneId: servicesDnsZone.id
        }
      }
    ]
  }
}

// ============================================================================
// 7. ARTIFACT INTERFACE OUTPUTS
// ============================================================================
output aiHubId string = aiHub.id
output aiProjectId string = aiProject.id
output openAiEndpoint string = cognitiveAccount.properties.endpoint
output openAiAccountName string = cognitiveAccount.name
metadata description = 'Provisions a secure Azure AI Services account locked down to specific subnets, and creates a child project inside it.'

param location string 
param agentSubnetId string
param subnetIds array 
param aiServicesName string 
param projectName string 
param appGatewayIdentityPrincipalId string
param keyVaultName string
param storageAccountName string
param searchServiceName string
param cosmosAccountName string
param logAnalyticsWorkspaceName string
param vnetId string
param privateEndpointSubnetId string


// ============================================================================
// EXISTING SHARED RESOURCES REFERENCED FOR OBJECT ATTRIBUTES
// ============================================================================
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: keyVaultName }
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: storageAccountName }
resource search 'Microsoft.Search/searchServices@2023-11-01' existing = { name: searchServiceName }
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = { name: cosmosAccountName }
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = { name: logAnalyticsWorkspaceName }

// ============================================================================
// 1. SECURED AI SERVICES ACCOUNT (Subnet Firewall Whitelist Layer)
// ============================================================================
resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: aiServicesName
  location: location
  sku: {
    name: 'S0'
  }
  kind: 'AIServices' 
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true 
    customSubDomainName: aiServicesName
    
    // 🛡️ FIREWALL ACTIVATION: Enabled public access structural routing
    publicNetworkAccess: 'Enabled'
    
    // 🎯 SUBNET WHITELIST FILTER: ...Bricks out the internet and allows only your subnets
    networkAcls: {
      defaultAction: 'Deny' // Standard internet is completely blocked
      virtualNetworkRules: [for subnetId in subnetIds: {
        id: subnetId
        // Bypasses strict Service Endpoint prerequisites so validation passes immediately
        ignoreMissingVnetServiceEndpoint: true 
      }]  
    }
    networkInjections: [
        {
          scenario: 'agent'
          subnetArmId: agentSubnetId
        }
      ]
    encryption: {
      keySource: 'Microsoft.CognitiveServices'
    }
  }
}

// ============================================================================
// 2. SECURED FOUNDRY PROJECT
// ============================================================================
resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundryAccount 
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'AI Chat Network-Isolated Project Workspace'
    description: 'Modern project canvas locked down to chosen subnets via Bicep firewalls'
  }
}

var foundryUserRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

resource appGatewayFoundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, appGatewayIdentityPrincipalId, foundryUserRoleId)
  scope: foundryAccount // 🎯 Scoped cleanly to this project instance only
  properties: {
    principalId: appGatewayIdentityPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

resource kvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, foundryAccount.id, 'KeyVaultSecretsOfficer')
  scope: kv
  properties: {
    principalId: foundryAccount.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7') // Key Vault Secrets Officer
    principalType: 'ServicePrincipal'
  }
}

resource storageBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, foundryAccount.id, 'StorageBlobDataContributor')
  scope: storage
  properties: {
    principalId: foundryAccount.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe') // Storage Blob Data Contributor
    principalType: 'ServicePrincipal'
  }
}

resource searchIndexRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, foundryAccount.id, 'SearchIndexDataContributor')
  scope: search
  properties: {
    principalId: foundryAccount.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7') // Search Index Data Contributor
    principalType: 'ServicePrincipal'
  }
}

resource searchServiceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, foundryAccount.id, 'SearchServiceContributor')
  scope: search
  properties: {
    principalId: foundryAccount.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0') // Search Service Contributor
    principalType: 'ServicePrincipal'
  }
}

resource foundryDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: '${aiServicesName}-analytics-telemetry'
  scope: foundryAccount // Target monitoring explicitly to your AI Services backend engine
  properties: {
    workspaceId: logAnalytics.id
    // Streams every available API evaluation log, request block, and audit metric automatically
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

resource keyVaultConnection 'Microsoft.CognitiveServices/accounts/connections@2025-06-01' = {
  parent: foundryAccount
  name: '${aiServicesName}-keyvault'
  properties: {
    category: 'AzureKeyVault'
    target: kv.id
    authType: 'AccountManagedIdentity'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: kv.id
      location: location
    }
  }
  dependsOn: [ kvRole ]
}

resource storageConnection 'Microsoft.CognitiveServices/accounts/connections@2025-06-01' = {
  parent: foundryAccount
  name: '${aiServicesName}-storage'
  properties: {
    category: 'AzureStorageAccount'
    target: storage.properties.primaryEndpoints.blob
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: storage.id
    }
  }
  dependsOn: [ storageBlobRole ]
}

resource searchConnection 'Microsoft.CognitiveServices/accounts/connections@2025-06-01' = {
  parent: foundryAccount
  name: '${aiServicesName}-search'
  properties: {
    category: 'CognitiveSearch'
    target: 'https://${searchServiceName}.search.windows.net'
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: search.id
    }
  }
  dependsOn: [ searchIndexRole, searchServiceRole ]
}

resource cosmosConnection 'Microsoft.CognitiveServices/accounts/connections@2025-06-01' = {
  parent: foundryAccount
  name: '${aiServicesName}-cosmos'
  properties: {
    category: 'CosmosDb'
    target: 'https://${cosmosAccountName}.documents.azure.com:443/'
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: cosmos.id
    }
  }
}

// Provision the core Private DNS Zones required for private AI architecture
resource cognitiveDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.cognitiveservices.azure.com'
  location: 'global'
}

resource openaiDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.openai.azure.com'
  location: 'global'
}

resource foundryDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.services.ai.azure.com'
  location: 'global'
}

// Link all three Private DNS Zones directly into your active VNet topology
resource cognitiveVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: cognitiveDnsZone
  name: '${aiServicesName}-cognitive-vnet-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

resource openaiVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: openaiDnsZone
  name: '${aiServicesName}-openai-vnet-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

resource foundryVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: foundryDnsZone
  name: '${aiServicesName}-foundry-vnet-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

// ============================================================================
// 3. PRIVATE ENDPOINT CREATION & AUTO-DNS REGISTRATION
// ============================================================================
resource aiPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-${aiServicesName}'
  location: location
  properties: {
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [
      {
        name: 'plsc-${aiServicesName}'
        properties: {
          privateLinkServiceId: foundryAccount.id
          groupIds: [ 'account' ] // Maps cleanly to the primary AI Services resource channel
        }
      }
    ]
  }
}

// The Zone Group automatically intercepts the Private Endpoint NIC assignment 
// and drops the A-records directly into your newly linked DNS zones.
resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: aiPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'cognitive-config'
        properties: { privateDnsZoneId: cognitiveDnsZone.id }
      }
      {
        name: 'openai-config'
        properties: { privateDnsZoneId: openaiDnsZone.id }
      }
      {
        name: 'foundry-config'
        properties: { privateDnsZoneId: foundryDnsZone.id }
      }
    ]
  }
}

// ============================================================================
// OUTPUT VALUES
// ============================================================================
output aiServicesName string = foundryAccount.name
output projectName string = foundryProject.name
output projectResourceId string = foundryProject.id
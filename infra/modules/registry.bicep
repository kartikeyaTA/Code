metadata description = 'Provisions the private Azure Container Registry for Docker images with native diagnostic logging enabled.'

param envName string
param location string 
param logAnalyticsWorkspaceId string // Required to stream metric telemetry
param managedIdentityName string
var acrName = 'aichatregistry${envName}'
var acrPullRoleDefinitionId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

// 1. Azure Container Registry Definition
resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Standard' // Standard tier provides optimized pricing for internal enterprise operations
  }
  properties: {
    adminUserEnabled: false // ◄ Secure: Hard-disables standard administrative passwords!
    publicNetworkAccess: 'Enabled' // Allows CI/CD visibility; can be flipped via Private Endpoints on Premium SKU
  }
}

// 2. Diagnostic Settings for Centralized Logging Core Compliance
// FIX: Appended '-preview' to match the supported regional provider extension in eastus2
resource acrDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'acr-diagnostic-metrics'
  scope: containerRegistry // Binds monitoring straight to this registry instance
  properties: {
    workspaceId: logAnalyticsWorkspaceId // Routes straight to Step 1 workspace
    logs: [
      {
        category: 'ContainerRegistryRepositoryEvents' // Tracks pushing and deleting images
        enabled: true
      }
      {
        category: 'ContainerRegistryLoginEvents' // Tracks access attempts
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

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: managedIdentityName
}

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, managedIdentity.id, acrPullRoleDefinitionId)
  scope: containerRegistry
  properties: {
    principalId: managedIdentity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleDefinitionId)
    principalType: 'ServicePrincipal'
  }
}

// Export credentials for downstream Azure Container App environment tracking
output registryId string = containerRegistry.id
output registryLoginServer string = containerRegistry.properties.loginServer
output registryName string = containerRegistry.name
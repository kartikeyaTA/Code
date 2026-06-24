metadata description = 'Provisions all 4 core container microservices with System-Assigned Identities and maps fine-grained RBAC permissions.'

param envName string
param location string 
param environmentId string
param registryLoginServer string

// References to existing resources for RBAC scoping
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: 'stachattranscripts-${envName}'
}
resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: 'cog-openai-chat-${envName}'
}
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: 'kv-ai-chat-${envName}'
}
resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: 'aichatregistry-${envName}'
}

// Immutable Azure Built-In Role Definition Guids
var acrPullRoleId     = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var blobCtrlRoleId    = 'ba923a6b-3e65-488b-81b0-1a1a660a2046' // Storage Blob Data Contributor
var openAiUserRoleId  = '5e0c59e6-11cb-4ab3-b101-739a41601b30' // Cognitive Services OpenAI User
var kvSecretsRoleId   = '46334583-8a30-417c-b847-e6d2def263d0' // Key Vault Secrets User

// Public Bootstrap Image to break deployment deadlocks
var bootstrapImage = 'mcr.microsoft.com/azuredocs/aci-helloworld:latest'

// ============================================================================
// 1. FRONTEND APP (React SPA - Completely Isolated Compute)
// ============================================================================
resource frontendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-frontend-spa-${envName}'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false // Hidden inside environment load balancer; APIM proxy targeted
        targetPort: 80
        transport: 'auto'
      }
      registries: [ { server: registryLoginServer, identity: 'system' } ]
    }
    template: {
      containers: [ { name: 'react-spa', image: bootstrapImage } ]
    }
  }
}

resource frontendAppAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(frontendApp.id, 'frontendApp-acr-pull')
  scope: containerRegistry
  properties: { principalId: frontendApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId), principalType: 'ServicePrincipal' }
}
resource frontendAppBlobCtrl 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(frontendApp.id, 'frontendApp-blob-ctrl')
  scope: storageAccount
  properties: { principalId: frontendApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobCtrlRoleId), principalType: 'ServicePrincipal' }
}
resource frontendAppOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(frontendApp.id, 'frontendApp-openai-user')
  scope: cognitiveAccount
  properties: { principalId: frontendApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId), principalType: 'ServicePrincipal' }
}
resource frontendAppKvSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(frontendApp.id, 'chat-kv-secrets')
  scope: keyVault
  properties: { principalId: frontendApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsRoleId), principalType: 'ServicePrincipal' }
}

// ============================================================================
// 2. CHAT BACKEND APP (FastAPI Python - Heavy Permissions)
// ============================================================================
resource chatBackendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-chat-backend-${envName}'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: false, targetPort: 8000, transport: 'auto' }
      registries: [ { server: registryLoginServer, identity: 'system' } ]
    }
    template: {
      containers: [ { name: 'fastapi-chat', image: bootstrapImage } ]
    }
  }
}

// Chat Backend Permissions Mapping
resource chatAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(chatBackendApp.id, 'chat-acr-pull')
  scope: containerRegistry
  properties: { principalId: chatBackendApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId), principalType: 'ServicePrincipal' }
}
resource chatBlobCtrl 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(chatBackendApp.id, 'chat-blob-ctrl')
  scope: storageAccount
  properties: { principalId: chatBackendApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobCtrlRoleId), principalType: 'ServicePrincipal' }
}
resource chatOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(chatBackendApp.id, 'chat-openai-user')
  scope: cognitiveAccount
  properties: { principalId: chatBackendApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId), principalType: 'ServicePrincipal' }
}
resource chatKvSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(chatBackendApp.id, 'chat-kv-secrets')
  scope: keyVault
  properties: { principalId: chatBackendApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsRoleId), principalType: 'ServicePrincipal' }
}

// ============================================================================
// 3. VOICE BACKEND APP (FastAPI Python - Streaming Core)
// ============================================================================
resource voiceBackendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-voice-backend-${envName}'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: false, targetPort: 8001, transport: 'auto' }
      registries: [ { server: registryLoginServer, identity: 'system' } ]
    }
    template: {
      containers: [ { name: 'fastapi-voice', image: bootstrapImage } ]
    }
  }
}

// Voice Backend Permissions Mapping
resource voiceAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(voiceBackendApp.id, 'voice-acr-pull')
  scope: containerRegistry
  properties: { principalId: voiceBackendApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId), principalType: 'ServicePrincipal' }
}
resource voiceOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(voiceBackendApp.id, 'voice-openai-user')
  scope: cognitiveAccount
  properties: { principalId: voiceBackendApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId), principalType: 'ServicePrincipal' }
}
resource voiceKvSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(voiceBackendApp.id, 'voice-kv-secrets')
  scope: keyVault
  properties: { principalId: voiceBackendApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsRoleId), principalType: 'ServicePrincipal' }
}

// ============================================================================
// 4. SERVICENOW SHIM APP (Python Integration Layer)
// ============================================================================
resource snowShimApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-snow-shim-${envName}'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: false, targetPort: 8002, transport: 'auto' }
      registries: [ { server: registryLoginServer, identity: 'system' } ]
    }
    template: {
      containers: [ { name: 'python-snow', image: bootstrapImage } ]
    }
  }
}

// ServiceNow Shim Permissions Mapping
resource snowAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(snowShimApp.id, 'snow-acr-pull')
  scope: containerRegistry
  properties: { principalId: snowShimApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId), principalType: 'ServicePrincipal' }
}
resource snowKvSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(snowShimApp.id, 'snow-kv-secrets')
  scope: keyVault
  properties: { principalId: snowShimApp.identity.principalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsRoleId), principalType: 'ServicePrincipal' }
}

// Export Internal App Endpoints so APIM can dynamically lock onto them in Step 9
output frontendFqdn string = frontendApp.properties.configuration.ingress.fqdn
output chatBackendFqdn string = chatBackendApp.properties.configuration.ingress.fqdn
output voiceBackendFqdn string = voiceBackendApp.properties.configuration.ingress.fqdn
output snowShimFqdn string = snowShimApp.properties.configuration.ingress.fqdn
output chatBackendPrincipalId string = chatBackendApp.identity.principalId
output voiceBackendPrincipalId string = voiceBackendApp.identity.principalId
output snowShimPrincipalId string = snowShimApp.identity.principalId
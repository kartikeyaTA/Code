metadata description = 'Applies the explicit built-in enterprise RBAC roles mapped in the tracking architecture sheet.'

param keyVaultName string
param storageAccountName string
param openAiAccountName string
param acrName string

// Principal IDs gathered dynamically from your application container outputs
param appGatewayPrincipalId string
param apimPrincipalId string
param chatBackendPrincipalId string
param voiceBackendPrincipalId string
param snowShimPrincipalId string
param acaEnvironmentPrincipalId string

// Built-in Azure RBAC Role Definition IDs (Static Global GUIDs)
var keyVaultSecretsUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var storageBlobDataContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var cognitiveServicesOpenAIUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0c59e6-11cb-4ab3-b101-73b46716af50')
var acrPullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

// Existing Resource References
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: keyVaultName }
resource st 'Microsoft.Storage/storageAccounts@2023-01-01' existing = { name: storageAccountName }
resource cogn 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = { name: openAiAccountName }
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: acrName }

// ============================================================================
// 1. KEY VAULT SECRETS USER ASSIGNMENTS
// ============================================================================
resource agwKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, appGatewayPrincipalId, keyVaultSecretsUserRole)
  scope: kv
  properties: { principalId: appGatewayPrincipalId, roleDefinitionId: keyVaultSecretsUserRole, principalType: 'ServicePrincipal' }
}

resource apimKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, apimPrincipalId, keyVaultSecretsUserRole)
  scope: kv
  properties: { principalId: apimPrincipalId, roleDefinitionId: keyVaultSecretsUserRole, principalType: 'ServicePrincipal' }
}

resource chatKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, chatBackendPrincipalId, keyVaultSecretsUserRole)
  scope: kv
  properties: { principalId: chatBackendPrincipalId, roleDefinitionId: keyVaultSecretsUserRole, principalType: 'ServicePrincipal' }
}

resource voiceKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, voiceBackendPrincipalId, keyVaultSecretsUserRole)
  scope: kv
  properties: { principalId: voiceBackendPrincipalId, roleDefinitionId: keyVaultSecretsUserRole, principalType: 'ServicePrincipal' }
}

resource snowKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, snowShimPrincipalId, keyVaultSecretsUserRole)
  scope: kv
  properties: { principalId: snowShimPrincipalId, roleDefinitionId: keyVaultSecretsUserRole, principalType: 'ServicePrincipal' }
}

// ============================================================================
// 2. DATA PLANE & AI SERVICES ASSIGNMENTS
// ============================================================================
resource chatStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(st.id, chatBackendPrincipalId, storageBlobDataContributorRole)
  scope: st
  properties: { principalId: chatBackendPrincipalId, roleDefinitionId: storageBlobDataContributorRole, principalType: 'ServicePrincipal' }
}

resource chatOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(cogn.id, chatBackendPrincipalId, cognitiveServicesOpenAIUserRole)
  scope: cogn
  properties: { principalId: chatBackendPrincipalId, roleDefinitionId: cognitiveServicesOpenAIUserRole, principalType: 'ServicePrincipal' }
}

resource voiceOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(cogn.id, voiceBackendPrincipalId, cognitiveServicesOpenAIUserRole)
  scope: cogn
  properties: { principalId: voiceBackendPrincipalId, roleDefinitionId: cognitiveServicesOpenAIUserRole, principalType: 'ServicePrincipal' }
}

// ============================================================================
// 3. SECURE CONTAINER REGISTRY IMAGE PULL ASSIGNMENT
// ============================================================================
resource acaAcrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, acaEnvironmentPrincipalId, acrPullRole)
  scope: registry
  properties: { principalId: acaEnvironmentPrincipalId, roleDefinitionId: acrPullRole, principalType: 'ServicePrincipal' }
}
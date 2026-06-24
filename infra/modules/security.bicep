metadata description = 'Provisions the secure Key Vault with RBAC authorization and the standalone Application Gateway Managed Identity.'

param envName string
param location string 
var keyVaultName = 'kv-ai-chat-${envName}'
var appGatewayIdentityName = 'id-app-gateway-${envName}'

// 1. Create the Standalone User-Assigned Managed Identity for the Edge WAF
resource appGatewayIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: appGatewayIdentityName
  location: location
}

// 2. Central Key Vault with Modern RBAC Azure Authorization Enabled
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true // ◄ Critical: Disables old access policies, activates RBAC!
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled' 
  }
}

// 3. Explicitly grant "Key Vault Secrets User" to the WAF Identity
// FIX: Added the if (!empty(...)) gate to prevent crashing on unpropagated Entra ID tokens
resource gwKvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(appGatewayIdentity.properties.principalId)) {
  name: guid(keyVault.id, appGatewayIdentity.name, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    principalId: appGatewayIdentity.properties.principalId 
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') 
    principalType: 'ServicePrincipal'
  }
}

// Export security tokens so main.bicep can map them to downstream network/compute blocks
output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output appGatewayIdentityId string = appGatewayIdentity.id
output appGatewayIdentityPrincipalId string = appGatewayIdentity.properties.principalId
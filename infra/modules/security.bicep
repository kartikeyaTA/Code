metadata description = 'Provisions the secure Key Vault with RBAC authorization and the standalone Application Gateway Managed Identity.'

param envName string
param location string 
var keyVaultName = 'testkaraichat2${envName}'
var appGatewayIdentityName = 'id-app-gateway-${envName}'
param pipelineServicePrincipalObjectId string = 'd56c738c-506d-4880-b359-fa3cec389733'
// 1. Create the Standalone User-Assigned Managed Identity for the Edge WAF
resource appGatewayIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: appGatewayIdentityName
  location: location
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: 'stachattranscripts${envName}'
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
// FIX: Removed the invalid 'if' condition to fix BCP177 compiler error
resource gwKvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, appGatewayIdentity.name, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    principalId: appGatewayIdentity.properties.principalId 
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') 
    principalType: 'ServicePrincipal'
  }
}

resource pipelineKvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, pipelineServicePrincipalObjectId, 'KeyVaultSecretsOfficer')
  scope: keyVault
  properties: {
    principalId: pipelineServicePrincipalObjectId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7') // Key Vault Secrets Officer
    principalType: 'ServicePrincipal'
  }
}

resource storagevRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, appGatewayIdentity.name, 'BlobUser')
  scope: storageAccount
  properties: {
    principalId: appGatewayIdentity.properties.principalId 
    // ◄ FIXED: Corrected the official Azure GUID for Key Vault Secrets User
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1') 
    principalType: 'ServicePrincipal'
  }
}

// Export security tokens so main.bicep can map them to downstream network/compute blocks
output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output appGatewayIdentityId string = appGatewayIdentity.id
output appGatewayIdentityName string = appGatewayIdentity.name
output appGatewayIdentityPrincipalId string = appGatewayIdentity.properties.principalId
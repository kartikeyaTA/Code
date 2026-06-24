metadata description = 'Provisions the secure Key Vault with RBAC authorization and the standalone Application Gateway Managed Identity.'

param envName string
param location string 
var keyVaultName = 'testkaraichat${envName}'
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
// FIX: Removed the invalid 'if' condition to fix BCP177 compiler error


// Export security tokens so main.bicep can map them to downstream network/compute blocks
output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output appGatewayIdentityId string = appGatewayIdentity.id
output appGatewayIdentityPrincipalId string = appGatewayIdentity.properties.principalId
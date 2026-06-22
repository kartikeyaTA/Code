param envName string
param location string

// Azure Key Vault and Storage names must be globally unique
var logWorkspaceName = 'log-aichat-${envName}'
var keyVaultName = 'kv-aichat-${envName}-${uniqueString(resourceGroup().id)}'
var storageName = 'sttrawdaichat${uniqueString(resourceGroup().id)}'

resource logWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logWorkspaceName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: substring(keyVaultName, 0, min(length(keyVaultName), 24))
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true // Modern Azure standard using RBAC roles over old access policies
    publicNetworkAccess: 'Disabled'
  }
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: substring(storageName, 0, min(length(storageName), 24))
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    publicNetworkAccess: 'Disabled'
    allowBlobPublicAccess: false
  }
}

output logWorkspaceId string = logWorkspace.id
output keyVaultId string = keyVault.id
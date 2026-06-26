metadata description = 'Provisions a single core Chat Backend microservice using a pre-warmed User-Assigned Identity to bypass provisioning deadlocks.'

param envName string
param location string 
param environmentId string
param registryLoginServer string

// References to existing resources for RBAC scoping
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: 'stachattranscripts${envName}'
}
resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: 'cog-openai-chat2-${envName}'
}
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: 'testkaraichat2${envName}'
}

// Reference to your core User identity to avoid Entra ID race conditions
resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: 'id-app-gateway-${envName}'
}

// Target your real custom python container built and resting in your registry repo
var realProductionImage = '${registryLoginServer}/core-service:latest'

// ============================================================================
// CHAT BACKEND APP (FastAPI Python - Single Focused Compute Core)
// ============================================================================
resource chatBackendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-chat-backend-${envName}'
  location: location
  identity: { 
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${appIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { 
        external: false 
        targetPort: 80 
        transport: 'http' 
      }
      // Uses the pre-assigned identity block to query Key Vault without a replication lag timeout
      secrets: [
        {
          name: 'vault-secret'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/my-dummy-secret'
          identity: appIdentity.id
        }
      ]
      // References the registry via the pre-warmed user assigned identity bundle
      registries: [
        {
          server: registryLoginServer
          identity: appIdentity.id
        }
      ]
    }
    template: {
      containers: [ 
        { 
          name: 'fastapi-chat' 
          image: realProductionImage 
          env: [
            {
              name: 'AZURE_CLIENT_ID'
              value: appIdentity.properties.clientId
            }
            {
              name: 'MY_DUMMY_SECRET'
              secretRef: 'vault-secret'
            }
            {
              name: 'STORAGE_ACCOUNT_NAME'
              value: 'stachattranscripts${envName}'
            }
          ]
        } 
      ]
      scale: {
        minReplicas: 1 // Keep 1 warm so our logging endpoints don't drop to zero
        maxReplicas: 10
      }
    }
  }
}

// Export Internal App Endpoints cleanly so downstream network blocks don't crash
output chatBackendFqdn string = chatBackendApp.properties.configuration.ingress.fqdn
output chatBackendPrincipalId string = appIdentity.properties.principalId
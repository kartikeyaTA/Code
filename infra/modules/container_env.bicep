metadata description = 'Deploys an internally-isolated Azure Container Apps Managed Environment attached to a delegated private subnet.'

param envName string
param location string 
param acaSubnetId string

// Reference the existing telemetry workspace to dynamically fetch operational ingestion keys
resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: 'log-analytics-ai-chat-${envName}'
}

var environmentName = 'aca-env-chat-${envName}'

// 1. Isolated Azure Container Apps Managed Environment
resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    // Logging Configuration - Pipes all microservice console logs straight to Step 1
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: logAnalyticsWorkspace.listKeys().primarySharedKey
      }
    }
    // Network Configuration - Seals the cluster behind a private boundary
    vnetConfiguration: {
      internal: true // ◄ Crucial: Completely hides the cluster behind an Internal Load Balancer
      infrastructureSubnetId: acaSubnetId // Binds to our delegated /23 subnet space
    }
    zoneRedundant: false // Can be enabled for multi-availability zone production high-availability
  }
}

// Export the Environment ID string so the actual apps can deploy inside this cluster shell in Step 8
output environmentId string = managedEnvironment.id
output environmentDefaultDomain string = managedEnvironment.properties.defaultDomain
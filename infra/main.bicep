targetScope = 'tenant' // Keeps the orchestrator at tenant level to loop across independent subscriptions

param envName string
param location string
param subscriptionConfigs array // Will contain the static Sub IDs

module resourceGroupDeployment './modules/resourcegroup.bicep' = [for sub in subscriptionConfigs: {
  name: 'rg-deployment-${sub.aliasName}'
  scope: subscription(sub.subscriptionId) // ◄ Completely stable! Bicep knows the target scope immediately.
  params: {
    resourceGroupName: 'rg-${sub.aliasName}-shared'
    location: location
    envName: envName
  }
}]


output processedSubscriptions array = [for sub in subscriptionConfigs: {
  alias: sub.aliasName
  id: sub.subscriptionId
}]
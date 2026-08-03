targetScope = 'subscription' 

param resourceGroupName string
param envName string
param location string
param aiServicesName string 
param projectName string


resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: {
    Environment: envName
  }
}


module telemetry './modules/telemetry.bicep' = {
  name: 'telemetry-deployment'
  scope: rg 
  params: {
    envName: envName
    location: location
  }
}

module aifoundry './modules/foundry_models.bicep' = {
  name: 'aifoundry-deployment'
  scope: rg 
  params: {
    aiServicesName: aiServicesName
    projectName: projectName
    location: location
    logAnalyticsWorkspaceName: telemetry.outputs.workspaceName
  }
}
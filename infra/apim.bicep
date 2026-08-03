targetScope = 'subscription' 

param resourceGroupName string
param envName string
param location string
param publisherEmail string 
param publisherName string 
param apimName string
param vnetName string

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: {
    Environment: envName
  }
}

module network './modules/networking.bicep' = {
  name: 'networking-deployment'
  scope: rg 
  params: {
    envName: envName
    vnetName: vnetName
    location: location
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

module apim './modules/apim.bicep' = {
  name: 'apim-deployment'
  scope: rg
  params: {
    apimName: apimName
    envName: envName
    location: location
    apimSubnetId: network.outputs.apimSubnetId // Deploys cleanly inside private subnet block
    logAnalyticsWorkspaceId: telemetry.outputs.workspaceId
    chatBackendUrl: 'dummy' // Dynamically maps to our verified python URL output
    publisherEmail: publisherEmail
    publisherName: publisherName
    containerenvIP: 'containerEnv.outputs.environmentStaticIp'
  }
  dependsOn: [
    network 
  ]
}
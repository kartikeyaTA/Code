targetScope = 'subscription' 

@description('The name of the environment (e.g., dev, qa, prod)')
param envName string

@description('The name of the resource group to create')
param resourceGroupName string

@description('The Azure region where all resources will be deployed')
param location string

@description('The name of the project')
param Project string

@description('The name of the project Manager')
param ManagedBy string

@description('The name of the vnet')
param vnetNameParam string

@description('The name of the nat')
param natGatewayNameParam string

// 1. Create the Resource Group directly here (gives us the 'rg' identifier)
resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: {
    Environment: envName
    Project: Project
    ManagedBy: ManagedBy
  }
}

// 2. Deploy your Network Module inside the Resource Group
module network './modules/networking.bicep' = {
  name: 'networking-deployment'
  scope: rg // Now this perfectly matches the resource group above!
  params: {
    envName: envName
    location: location
    vnetNameParam: vnetNameParam
    natGatewayNameParam: natGatewayNameParam
  }
}

// 3. Deploy your Security Module inside the Resource Group
module securityModule './modules/security.bicep' = {
  name: 'security-deployment'
  scope: rg // Fixed: Unique identifier 'securityModule' avoids duplication
  params: {
    envName: envName
    location: location
  }
}
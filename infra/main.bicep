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
param Project ManagedBy

// Create the Resource Group
resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: {
    Environment: envName
    Project: Project
    ManagedBy: ManagedBy
  }
}

output resourceGroupName string = rg.name
output resourceGroupId string = rg.id
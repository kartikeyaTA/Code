targetScope = 'subscription'
param envName string
param resourceGroupName string
param location string
param Project string
param ManagedBy string

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
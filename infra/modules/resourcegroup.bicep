targetScope = 'subscription'

param envName string
param resourceGroupName string
param location string

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: {
    Environment: envName
  }
}

output resourceGroupName string = rg.name
output resourceGroupId string = rg.id
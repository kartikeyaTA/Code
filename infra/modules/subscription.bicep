targetScope = 'tenant'

param billingScope string
param aliasName string
param displayName string
param envName string

resource subscriptionAlias 'Microsoft.Subscription/aliases@2021-10-01' = {
  name: aliasName
  properties: {
    billingScope: billingScope
    displayName: displayName
    workload: (envName == 'prod') ? 'Production' : 'DevTest' // Maps production workloads dynamically
  }
}

output subscriptionId string = subscriptionAlias.properties.subscriptionId
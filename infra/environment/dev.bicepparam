using '../main.bicep'

param envName = 'dev'
param location = 'eastus2' 



param subscriptionConfigs = [
  {
    aliasName: 'Application'
    subscriptionId: 'b0366117-1664-4ef1-aa8b-d68e8ae762f9'
  }
  {
    aliasName: 'APIM'
    subscriptionId: '86da7736-332a-493c-b690-660b3c3e9f9c'
  }
  {
    aliasName: 'Learning'
    subscriptionId: 'a0c64e05-02e0-4758-891f-e6731cfa3357'
  }
]
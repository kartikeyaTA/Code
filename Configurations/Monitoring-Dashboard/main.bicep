param dashboardName string = 'Agent-Monitoring-Code'
param location string = 'East US'

resource dashboard 'Microsoft.Dashboard/dashboards@2025-11-01-preview' = {
  name: dashboardName
  location: location
  properties: {}
}

resource dashboardDefinition 'Microsoft.Dashboard/dashboards/dashboardDefinitions@2025-11-01-preview' = {
  parent: dashboard
  name: 'default'
  properties: {
    serializedData: loadTextContent('./dashboard.json')
  }
}

metadata description = 'Deploys the central Log Analytics Workspace and Application Insights engine for unified platform monitoring.'

param envName string
param location string 
param retentionInDays int = 30
var logAnalyticsName = 'log-analytics-ai-chat-${envName}'
param appInsightsName string = 'app-insights-ai-chat-${envName}'

// 1. Central Log Analytics Workspace (The raw data lake for container stdout/stderr & diagnostic logs)
resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

// 2. Application Insights Component (Tied to the Workspace for OpenTelemetry traces from FastAPI/Python)
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspace.id // Workspace link
    RetentionInDays: retentionInDays
    publicNetworkAccessForIngestion: 'Enabled' // Required for distributed app tracing
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// Export outputs so downstream networking and compute layers can bind to this logging array
output workspaceId string = logAnalyticsWorkspace.id
output workspaceName string = logAnalyticsWorkspace.name
output workspaceCustomerId string = logAnalyticsWorkspace.properties.customerId
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
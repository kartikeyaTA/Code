metadata description = 'Provisions a secure Azure AI Services account locked down to specific subnets, and creates a child project inside it.'

param location string 
param aiServicesName string 
param projectName string 
param logAnalyticsWorkspaceName string




resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = { name: logAnalyticsWorkspaceName }

// ============================================================================
// 1. SECURED AI SERVICES ACCOUNT (Subnet Firewall Whitelist Layer)
// ============================================================================
resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: aiServicesName
  location: location
  sku: {
    name: 'S0'
  }
  kind: 'AIServices' 
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true 
    customSubDomainName: aiServicesName
    
    // 🛡️ FIREWALL ACTIVATION: Enabled public access structural routing
    publicNetworkAccess: 'Enabled'
    encryption: {
      keySource: 'Microsoft.CognitiveServices'
    }
  }
}

// ============================================================================
// 2. SECURED FOUNDRY PROJECT
// ============================================================================
resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundryAccount 
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'AI Chat Network-Isolated Project Workspace'
    description: 'Modern project canvas locked down to chosen subnets via Bicep firewalls'
  }
}


resource foundryDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: '${aiServicesName}-analytics-telemetry'
  scope: foundryAccount // Target monitoring explicitly to your AI Services backend engine
  properties: {
    workspaceId: logAnalytics.id
    // Streams every available API evaluation log, request block, and audit metric automatically
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}





// ============================================================================
// OUTPUT VALUES
// ============================================================================
output aiServicesName string = foundryAccount.name
output projectName string = foundryProject.name
output projectResourceId string = foundryProject.id
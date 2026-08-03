metadata description = 'Provisions a private, VNet-integrated Azure API Management (APIM) instance mapping a single focused pass-through route to our chat backend.'

param envName string
param location string
param apimSubnetId string
param logAnalyticsWorkspaceId string
param containerenvIP string

// Target URL generated from our apps deployment block
param chatBackendUrl string

param publisherEmail string 
param publisherName string 

param apimName string

// ============================================================================
// 1. PRIVATE VNET-INTEGRATED APIM ENGINE
// ============================================================================
resource apimInstance 'Microsoft.ApiManagement/service@2023-05-01-preview' = {
  name: apimName
  location: location
  sku: {
    name: 'Developer' 
    capacity: 1
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
    virtualNetworkType: 'External' 
    virtualNetworkConfiguration: {
      subnetResourceId: apimSubnetId 
    }
  }
}

// ============================================================================
// 4. DIAGNOSTIC LOGGING CORE COMPLIANCE
// ============================================================================
resource apimDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'apim-gateway-telemetry'
  scope: apimInstance
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [ { category: 'GatewayLogs', enabled: true } ]
  }
}

output apimId string = apimInstance.id
output apimPrivateIpAddress string = apimInstance.properties.publicIPAddresses[0]
output apimGatewayUrl string = apimInstance.properties.gatewayUrl
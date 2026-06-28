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

var apimName = 'apim-gateway-chat2-${envName}'

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
    virtualNetworkType: 'Internal' 
    virtualNetworkConfiguration: {
      subnetResourceId: apimSubnetId 
    }
  }
}

// ============================================================================
// 2. BACKEND TARGET PROXIES (Registering Internal Container Locations)
// ============================================================================
resource chatBackendProxy 'Microsoft.ApiManagement/service/backends@2023-05-01-preview' = {
  name: 'chat-backend-target'
  parent: apimInstance
  properties: {
    description: 'Internal route to the FastAPI Chat container'
    url: 'http://${containerenvIP}:80'
    protocol: 'http'
    tls: {
      validateCertificateChain: false
      validateCertificateName: false
    }
  }
}

// ============================================================================
// 3. PASS-THROUGH API ROUTING CONFIGURATION
// ============================================================================
resource chatApi 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  name: 'chat-api'
  parent: apimInstance
  properties: {
    displayName: 'Chat Core API'
    path: 'backend' // ◄ Traffic hitting http://<APIM_IP>/backend will proxy to the container
    protocols: ['https','http']
    subscriptionRequired: false
  }
}

resource chatApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-05-01-preview' = {
  parent: chatApi
  name: 'policy'
  properties: {
    value: '<policies><inbound><base /><set-header name="Host" exists-action="override"><value>${chatBackendUrl}</value></set-header><set-backend-service backend-id="chat-backend-target" /></inbound></policies>'
    format: 'xml'
  }
}

// Operation 1: Root status route
resource statusGetOperation 'Microsoft.ApiManagement/service/apis/operations@2023-05-01-preview' = {
  name: 'get-backend-status'
  parent: chatApi
  properties: {
    displayName: 'Get Backend Status'
    method: 'GET'
    urlTemplate: '/'
  }
}

// Operation 2: Fixed duplicate name to clear compiler blockages
resource listBlobsGetOperation 'Microsoft.ApiManagement/service/apis/operations@2023-05-01-preview' = {
  name: 'get-backend-status-blob'
  parent: chatApi
  properties: {
    displayName: 'Get Backend Status Blobs'
    method: 'GET'
    urlTemplate: '/list-blobs'
  }
}

resource statusGetOperationPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2023-05-01-preview' = {
  name: 'policy'
  parent: statusGetOperation // ◄ Targets your GET resource block
  properties: {
    value: '<policies><inbound><base /><set-header name="Host" exists-action="override"><value>${chatBackendUrl}</value></set-header><set-backend-service backend-id="chat-backend-target" /></inbound></policies>'
    format: 'xml'
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
output apimPrivateIpAddress string = apimInstance.properties.privateIPAddresses[0]
output apimGatewayUrl string = apimInstance.properties.gatewayUrl
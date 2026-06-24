metadata description = 'Provisions a private, VNet-integrated Azure API Management (APIM) instance, maps proxy backends for Frontend, Chat, and Voice containers, and enforces Entra ID JWT validation.'

param envName string
param location string
param apimSubnetId string
param logAnalyticsWorkspaceId string

// Fully qualified private domain locations passed from Step 8 (Apps module)
param frontendUrl string
param chatBackendUrl string
param voiceBackendUrl string

param publisherEmail string 
param publisherName string 

param entraTenantId string 
param frontendClientId string 
var apimName = 'apim-gateway-chat-${envName}'

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
resource frontendBackendProxy 'Microsoft.ApiManagement/service/backends@2023-05-01-preview' = {
  name: 'frontend-spa-target'
  parent: apimInstance
  properties: {
    description: 'Internal route to the React Frontend UI'
    url: 'http://${frontendUrl}'
    protocol: 'http'
  }
}

resource chatBackendProxy 'Microsoft.ApiManagement/service/backends@2023-05-01-preview' = {
  name: 'chat-backend-target'
  parent: apimInstance
  properties: {
    description: 'Internal route to the FastAPI Chat container'
    url: 'http://${chatBackendUrl}'
    protocol: 'http'
  }
}

resource voiceBackendProxy 'Microsoft.ApiManagement/service/backends@2023-05-01-preview' = {
  name: 'voice-backend-target'
  parent: apimInstance
  properties: {
    description: 'Internal route to the FastAPI Voice container'
    url: 'http://${voiceBackendUrl}'
    protocol: 'http'
  }
}

// ============================================================================
// 3. API ENDPOINTS & ZERO-TRUST ROUTING POLICIES
// ============================================================================

// --- A. FRONTEND SPA CATCH-ALL ROUTE (Serves static web assets via APIM) ---
resource rootUiApi 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  name: 'root-ui-api'
  parent: apimInstance
  properties: {
    displayName: 'Frontend UI Gateway'
    path: '' 
    protocols: ['https','http']
  }
}

resource rootUiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-05-01-preview' = {
  parent: rootUiApi
  name: 'policy'
  properties: {
    value: '''
    <policies>
      <inbound>
        <base />
        <set-backend-service backend-id="frontend-spa-target" />
      </inbound>
    </policies>
    '''
    format: 'xml'
  }
}

// --- GLOBAL BACKEND POLICY BLOCK TEMPLATE FOR ENTRA ID SECURED API PATHS ---
var entraJwtValidationBlock = '''
<validate-jwt token-value="@(context.Request.Headers.GetValueOrDefault("Authorization","").Split(' ').Last())" 
              failed-validation-httpcode="401" 
              failed-validation-error-message="Unauthorized: Corporate Microsoft Entra ID Token Invalid or Expired.">
  <openid-config url="https://login.microsoftonline.com/${entraTenantId}/v2.0/.well-known/openid-configuration" />
  <audiences>
    <audience>${frontendClientId}</audience>
  </audiences>
  <issuers>
    <issuer>https://sts.windows.net/${entraTenantId}/</issuer>
    <issuer>https://login.microsoftonline.com/${entraTenantId}/v2.0</issuer>
  </issuers>
</validate-jwt>
'''

// --- B. CHAT CORE API ROUTE ---
resource chatApi 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  name: 'chat-api'
  parent: apimInstance
  properties: {
    displayName: 'Chat Core API'
    path: 'api/chat' 
    protocols: ['https','http']
  }
}

resource chatApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-05-01-preview' = {
  parent: chatApi
  name: 'policy'
  properties: {
    value: '''
    <policies>
      <inbound>
        <base />
        ${entraJwtValidationBlock}
        <set-backend-service backend-id="chat-backend-target" />
      </inbound>
    </policies>
    '''
    format: 'xml'
  }
}

// --- C. REALTIME VOICE API ROUTE ---
resource voiceApi 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  name: 'voice-api'
  parent: apimInstance
  properties: {
    displayName: 'Voice Stream API'
    path: 'api/voice' 
    protocols: ['https','http']
  }
}

resource voiceApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-05-01-preview' = {
  parent: voiceApi
  name: 'policy'
  properties: {
    value: '''
    <policies>
      <inbound>
        <base />
        ${entraJwtValidationBlock}
        <set-backend-service backend-id="voice-backend-target" />
      </inbound>
    </policies>
    '''
    format: 'xml'
  }
}

// ============================================================================
// 4. DIAGNOSTIC LOGGING CORE COMPLIANCE
// ============================================================================
resource apimDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01' = {
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
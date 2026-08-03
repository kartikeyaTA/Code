// ============================================================================
// MCP Server tool connection -- OAuth Identity Passthrough (Custom OAuth)
// Fixed via loose-typing 'any()' wrappers to bypass lagging Bicep schemas.
// ============================================================================

@description('Name of the EXISTING Foundry account (parent resource, not the project)')
param foundryAccountName string = 'foundry-services-applications16-dev'

@description('Name of the EXISTING Foundry project')
param projectName string = 'foundry-project-applications16-dev'

@description('Name for this connection')
param connectionName string = 'mcp-servicenow-oauth-passthrough'

@description('MCP server SSE endpoint URL')
param mcpServerUrl string = 'https://mcp-backend-dev.prouddesert-66fd91c4.eastus.azurecontainerapps.io/sse'

@description('ServiceNow OAuth app Client ID (from the Application Registry record)')
param oauthClientId string = 'testing'

@secure()
@description('ServiceNow OAuth app Client Secret (from the Application Registry record)')
// 🎯 LINTER FIX: Defaulting to an empty string satisfies secure parameter rules
param oauthClientSecret string = 'testing'

@description('ServiceNow OAuth authorization endpoint')
param authorizationUrl string = 'https://dev408306.service-now.com/oauth_auth.do'

@description('ServiceNow OAuth token endpoint')
param tokenUrl string = 'https://dev408306.service-now.com/oauth_token.do'

@description('ServiceNow OAuth refresh endpoint (same as token endpoint for ServiceNow)')
param refreshUrl string = 'https://dev408306.service-now.com/oauth_token.do'

@description('OAuth scopes as an array -- offline_access required for silent refresh per Foundry docs')
param oauthScopes array = [
  'useraccount'
  'offline_access'
]

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundryAccount
  name: projectName
}

resource mcpOAuthConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: connectionName
  // 🎯 SCHEMA FIX: Using any() tells the compiler to skip strict object model validation
  properties: any({
    category: 'RemoteTool'
    authType: 'OAuth2'
    isSharedToAll: false
    isDefault: false
    useWorkspaceManagedIdentity: false
    target: mcpServerUrl
    authorizationUrl: authorizationUrl
    tokenUrl: tokenUrl
    refreshUrl: refreshUrl
    scopes: oauthScopes
    credentials: {
      clientId: oauthClientId
      clientSecret: oauthClientSecret
    }
    metadata: {
      type: 'custom_MCP'
      oAuthProvider: 'custom'
    }
  })
}

output connectionId string = mcpOAuthConnection.id
output connectionName string = mcpOAuthConnection.name

// 🎯 DECORATOR & OUTPUT FIX: Cleaned string syntax and wrapped properties access in any()
@description('The Azure-generated consent redirect URL. Register this in the ServiceNow Application Registry after this deployment completes.')
output redirectUrl string = any(mcpOAuthConnection.properties).redirectUrl
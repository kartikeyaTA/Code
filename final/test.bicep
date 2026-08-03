// ============================================================================
// Sub A — Foundry "Admin-connected models" BYOM connection to APIM
//
// CONFIDENCE NOTE: this resource type (Microsoft.CognitiveServices/accounts/
// projects/connections, category ApiManagement) is very new. The shape below
// is my best-effort match to what the portal wizard writes. If deployment
// rejects a property name, the az rest fallback at the bottom of this file
// calls the same REST endpoint directly and is more likely to be current --
// swap to that block instead of debugging the Bicep resource type further.
// ============================================================================

@description('Name of the EXISTING Foundry account (parent resource, not the project)')
param foundryAccountName string = 'txrh-foundry'

@description('Name of the EXISTING Foundry project')
param projectName string = 'txrh-project'

@description('Name for this connection, shown in Admin-connected models')
param connectionName string = 'apim-model-gateway'

@description('Full APIM gateway URL up to (not including) the operation path, e.g. https://<apim-name>.azure-api.net/models')
param apimGatewayUrl string = 'https://apim-gateway-application-test-dev4.azure-api.net/models/openai/v1'

@description('APIM subscription key')
param apimSubscriptionKey string = '6bdc037ef31b48fd8c1af56a8fcab446'

@description('Model deployment name -- must exactly match the deployment name on your Sub C resource(s)')
param modelName string = 'gpt-5'


@description('Model version -- leave empty if your deployment has no distinct version string')
param modelVersion string = '2025-08-07'

@description('Whether the deployment name is expected in the URL path (false for the v1-style body-based model selection used here)')
param deploymentInPath bool = false

@description('Name for the MCP key-based connection')
param mcpKeyConnectionName string = 'mcp-servicenow-copy'

@description('MCP server SSE endpoint URL')
param mcpServerUrl string = 'https://servicenow-mcp-app.greenmeadow-610a0edf.eastus.azurecontainerapps.io/sse'

@secure()
@description('The static shared secret sent as X-MCP-Caller-Secret header to the MCP server')
param mcpCallerSecret string = 'mcp_key'



resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundryAccount
  name: projectName
}

resource mcpKeyConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: mcpKeyConnectionName
  properties: {
    category: 'RemoteTool'
    authType: 'CustomKeys'
    isSharedToAll: false
    isDefault: true
    useWorkspaceManagedIdentity: false
    target: mcpServerUrl
    credentials: {
      // BEST-EFFORT SHAPE -- verify via `az resource show` after first deploy.
      // "CustomKeys" (plural) suggests a dict of header-name -> value pairs
      // rather than the single `key` field used by ApiManagement/ApiKey above.
      keys: {
        'x-api-key': mcpCallerSecret
      }
    }
    metadata: {
      type: 'custom_MCP'
    }
  }
}


// ============================================================================
// FALLBACK -- if the resource type above rejects deployment, run this instead
// (as a script step, e.g. via `az rest`), using the same parameter values:
//
// az rest --method PUT \
//   --uri "https://management.azure.com/subscriptions/<subA-id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundryAccountName>/projects/<projectName>/connections/<connectionName>?api-version=2025-06-01" \
//   --body '{
//     "properties": {
//       "category": "ApiManagement",
//       "authType": "ApiKey",
//       "target": "<apimGatewayUrl>",
//       "credentials": { "key": "<apimSubscriptionKey>" },
//       "metadata": {
//         "deploymentInPath": "false",
//         "inferenceApiVersion": "v1",
//         "staticModels": "[{\"name\":\"gpt-5.4-mini\",\"displayName\":\"GPT-5.4 Mini (via APIM Gateway)\",\"properties\":{\"model\":{\"format\":\"OpenAI\",\"name\":\"gpt-5.4-mini\"}}}]"
//       }
//     }
//   }'
// ============================================================================

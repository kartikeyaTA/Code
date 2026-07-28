// ============================================================================
// Sub C — Deploy a model onto an ALREADY-EXISTING Foundry/Cognitive Services
// resource. Run this once per resource (primary + secondary), pointing
// `accountName` at each one in turn.
// ============================================================================

@description('Name of your EXISTING Primary Cognitive Services / Foundry account, e.g. model-1-testing')
param accountName1 string = 'foundry-services-model-dev17'

@description('Name of your EXISTING Secondary Cognitive Services / Foundry account')
param accountName2 string = 'foundry-services-model-dev18'

@description('Model deployment name — must be IDENTICAL across primary and secondary for failover to be transparent')
param deploymentName string = 'gpt-5'

@description('Underlying model name as it appears in the model catalog')
param modelName string = 'gpt-5'

@description('Model version — leave empty to use the default/latest')
param modelVersion string = '2025-08-07'

@description('Model capacity (in units of 1,000 TPM, depends on SKU/quota)')
param capacity int = 100

// Reference your existing resource instead of creating a new one
resource account1 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName1
}

resource account2 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName2
}

resource deployment1 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account1
  name: deploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: empty(modelVersion) ? null : modelVersion
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
}

resource deployment2 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account2
  name: deploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: empty(modelVersion) ? null : modelVersion
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
}

output accountName1 string = account1.name
output accountId1 string = account1.id
output openAiV1Endpoint1 string = 'https://${accountName1}.openai.azure.com/openai/v1'
output deploymentName1 string = deployment1.name
output accountName2 string = account2.name
output accountId2 string = account2.id
output openAiV1Endpoint2 string = 'https://${accountName2}.openai.azure.com/openai/v1'
output deploymentName2 string = deployment2.name

metadata description = 'Isolates cognitive model deployments to prevent platform preflight race conditions.'

param cognitiveAccountName string

// Reference the pre-existing OpenAI account resource built during the foundation module pass
resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: cognitiveAccountName
}

// Deploy the GPT-4o Model Deployment inside the verified AI Engine
resource gpt4oMiniDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: cognitiveAccount
  name: 'o4-mini-deployment' // Changing deployment name to match model
  sku: {
    name: 'GlobalStandard'
    capacity: 50 
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'o4-mini'    // SWAP MODEL: Future-proof, highly stable version
      version: '2025-04-16'  // Use the exact active GA version code
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.Default'
  }
}

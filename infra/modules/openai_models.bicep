metadata description = 'Isolates cognitive model deployments to prevent platform preflight race conditions.'

param cognitiveAccountName string

// Reference the pre-existing OpenAI account resource built during the foundation module pass
resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: cognitiveAccountName
}

// Deploy the GPT-4o Model Deployment inside the verified AI Engine
resource gpt4oDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: cognitiveAccount
  name: 'gpt-4o'
  sku: {
    name: 'Standard'
    capacity: 50 // Allocated Tokens-Per-Minute capacity setting
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-11-20' // Utilizing a stable enterprise version stamp
    }
  }
}
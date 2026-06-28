targetScope = 'subscription' 

@description('The name of the environment (e.g., dev, qa, prod)')
param envName string

@description('The name of the resource group to create')
param resourceGroupName string

@description('The Azure region where all resources will be deployed')
param location string

@description('The name of the project')
param Project string

@description('The name of the project Manager')
param ManagedBy string

@description('The email address associated with the owner of the APIM instance.')
param publisherEmail string 

@description('The name of the corporate organization owning this API gateway.')
param publisherName string 

@description('The corporate custom domain assigned to the platform used to register the OAuth redirect handshake.')
param customDomainName string

// ============================================================================
// 1. RESOURCE GROUP PROVISIONING
// ============================================================================
resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: {
    Environment: envName
    Project: Project
    ManagedBy: ManagedBy
  }
}

// ============================================================================
// 2. INFRASTRUCTURE MODULE DEPLOYMENTS
// ============================================================================
module network './modules/networking.bicep' = {
  name: 'networking-deployment'
  scope: rg 
  params: {
    envName: envName
    location: location
  }
}

module security './modules/security.bicep' = {
  name: 'security-deployment'
  scope: rg 
  params: {
    envName: envName
    location: location
  }
}

module telemetry './modules/telemetry.bicep' = {
  name: 'telemetry-deployment'
  scope: rg 
  params: {
    envName: envName
    location: location
  }
}

module storage './modules/storage.bicep' = {
  name: 'storage-deployment'
  scope: rg 
  params: {
    envName: envName
    location: location
    vnetId: network.outputs.vnetId
    endpointsSubnetId: network.outputs.endpointsSubnetId
  }
}

module registry './modules/registry.bicep' = {
  name: 'registry-deployment'
  scope: rg 
  params: {
    envName: envName
    location: location
    logAnalyticsWorkspaceId: telemetry.outputs.workspaceId
    managedIdentityName: security.outputs.appGatewayIdentityName
  }
}

module aifoundry './modules/ai_foundry.bicep' = {
  name: 'aifoundry-deployment'
  scope: rg 
  params: {
    envName: envName
    location: location
    vnetId: network.outputs.vnetId
    endpointsSubnetId: network.outputs.endpointsSubnetId
    keyVaultId: security.outputs.keyVaultId
    storageAccountId: storage.outputs.storageAccountId
  }
}

module openAiModels './modules/openai_models.bicep' = {
  name: 'openai-models-deployment'
  scope: rg
  params: {
    cognitiveAccountName: aifoundry.outputs.openAiAccountName
  }
  dependsOn: [
    aifoundry 
  ]
}

module containerEnv './modules/container_env.bicep' = {
  name: 'container-env-deployment'
  scope: rg
  params: {
    envName: envName
    location: location
    acaSubnetId: network.outputs.acaSubnetId 
  }
}

// ============================================================================
// 3. CORE SERVICE APPLICATIONS
// ============================================================================
module apps './modules/apps.bicep' = {
  name: 'apps-deployment'
  scope: rg
  params: {
    envName: envName
    location: location
    environmentId: containerEnv.outputs.environmentId 
    registryLoginServer: registry.outputs.registryLoginServer 
  }
  dependsOn: [
    security
    aifoundry
    openAiModels
  ]
}

// ============================================================================
// 4. PRIVATE API MANAGEMENT INGRESS GATEWAY
// ============================================================================
module apim './modules/apim.bicep' = {
  name: 'apim-deployment'
  scope: rg
  params: {
    envName: envName
    location: location
    apimSubnetId: network.outputs.apimSubnetId // Deploys cleanly inside private subnet block
    logAnalyticsWorkspaceId: telemetry.outputs.workspaceId
    chatBackendUrl: apps.outputs.chatBackendFqdn // Dynamically maps to our verified python URL output
    publisherEmail: publisherEmail
    publisherName: publisherName
    containerenvIP: containerEnv.outputs.environmentStaticIp
  }
  dependsOn: [
    apps
  ]
}

// ============================================================================
// 5. PUBLIC WEB APPLICATION FIREWALL (WAF) INGRESS EDGE
// ============================================================================
module waf './modules/app_gateway.bicep' = {
  name: 'waf-deployment'
  scope: rg
  params: {
    envName: envName
    location: location
    agwSubnetId: network.outputs.agwSubnetId
    appGatewayIdentityId: security.outputs.appGatewayIdentityId // Uses pre-warmed framework identity
    apimPrivateIpAddress: apim.outputs.apimPrivateIpAddress // Loops directly to the internal APIM instance
    apimGatewayUrl: apim.outputs.apimGatewayUrl
    logAnalyticsWorkspaceId: telemetry.outputs.workspaceId
  }
  dependsOn: [
    apim
  ]
}

module devopsRunner './modules/vm-runner.bicep' = {
  name: 'devops-runner-deployment'
  scope: rg
  params: {
    envName: envName
    location: location
    devopsSubnetId: network.outputs.devopsSubnetId // Maps reference from network module outputs
    adminPassword: 'RashiJacky@5301' // Recommend extracting from Key Vault references
  }
}

// ============================================================================
// GLOBAL ARCHITECTURAL OUTPUT TRACKING
// ============================================================================
output resourceGroupId string = rg.id
output vnetId string = network.outputs.vnetId
output keyVaultUri string = security.outputs.keyVaultUri
output storageAccountName string = storage.outputs.storageAccountName
output registryLoginServer string = registry.outputs.registryLoginServer
output aiProjectConnection string = '${aifoundry.outputs.openAiEndpoint}/api/projects/ai-project-chat-${envName}'
output chatBackendUrl  string = apps.outputs.chatBackendFqdn
// Entry points for external web browsers

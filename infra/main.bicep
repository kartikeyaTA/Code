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

// 1. Create the Resource Group directly here (gives us the 'rg' identifier)
resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: {
    Environment: envName
    Project: Project
    ManagedBy: ManagedBy
  }
}

// 2. Deploy your Network Module inside the Resource Group
module network './modules/networking.bicep' = {
  name: 'networking-deployment'
  scope: rg // Now this perfectly matches the resource group above!
  params: {
    envName: envName
    location: location
  }
}

// 3. Deploy your Security Module inside the Resource Group
module security './modules/security.bicep' = {
  name: 'security-deployment'
  scope: rg // Fixed: Unique identifier 'securityModule' avoids duplication
  params: {
    envName: envName
    location: location
  }
  dependsOn: [
    rg
  ]
}

module telemetry './modules/telemetry.bicep' = {
  name: 'telemetry-deployment'
  scope: rg // Now this perfectly matches the resource group above!
  params: {
    envName: envName
    location: location
  }
}

module storage './modules/storage.bicep' = {
  name: 'storage-deployment'
  scope: rg // Now this perfectly matches the resource group above!
  params: {
    envName: envName
    location: location
    vnetId: network.outputs.vnetId
    endpointsSubnetId: network.outputs.endpointsSubnetId
  }
}

module registry './modules/registry.bicep' = {
  name: 'registry-deployment'
  scope: rg // Now this perfectly matches the resource group above!
  params: {
    envName: envName
    location: location
    logAnalyticsWorkspaceId: telemetry.outputs.workspaceId
  }
}

module aifoundry './modules/ai_foundry.bicep' = {
  name: 'aifoundry-deployment'
  scope: rg // Now this perfectly matches the resource group above!
  params: {
    envName: envName
    location: location
    vnetId: network.outputs.vnetId
    endpointsSubnetId: network.outputs.endpointsSubnetId
    keyVaultId: security.outputs.keyVaultId
    storageAccountId: storage.outputs.storageAccountId
  }
}

module containerEnv './modules/container_env.bicep' = {
  name: 'container-env-deployment'
  scope: rg
  params: {
    envName: envName
    location: location
    acaSubnetId: network.outputs.acaSubnetId // Mapping to delegated /23 subnet
    logAnalyticsWorkspaceId: telemetry.outputs.workspaceId
  }
}

module apps './modules/apps.bicep' = {
  name: 'apps-deployment'
  scope: rg
  params: {
    envName: envName
    location: location
    environmentId: containerEnv.outputs.environmentId // Hosting inside Step 7 cluster
    registryLoginServer: registry.outputs.registryLoginServer // Link for passwordless image pulls
  }
}

module apim './modules/apim.bicep' = {
  name: 'apim-deployment'
  scope: rg
  params: {
    envName: envName
    location: location
    apimSubnetId: network.outputs.apimSubnetId
    logAnalyticsWorkspaceId: telemetry.outputs.workspaceId
    publisherEmail: publisherEmail
    publisherName: publisherName
    frontendUrl: apps.outputs.frontendFqdn
    chatBackendUrl: apps.outputs.chatBackendFqdn
    voiceBackendUrl: apps.outputs.voiceBackendFqdn
    entraTenantId: 'test'
    frontendClientId: 'test'
    //entraTenantId: entra.outputs.tenantId
    //frontendClientId: entra.outputs.frontendClientId
  }
}

module appGateway './modules/app_gateway.bicep' = {
  name: 'app-gateway-edge-deployment'
  scope: rg
  params: {
    envName: envName
    location: location
    agwSubnetId: network.outputs.agwSubnetId
    appGatewayIdentityId: security.outputs.appGatewayIdentityId
    apimPrivateIpAddress: apim.outputs.apimPrivateIpAddress
    logAnalyticsWorkspaceId: telemetry.outputs.workspaceId
  }
}

//module entra './modules/entra.bicep' = {
  //name: 'entra-identity-deployment'
  //params: {
  //  envName: envName
    //customDomainName: customDomainName
  //}
//}

module rbac './modules/role_assignments.bicep' = {
  name: 'security-rbac-matrix-deployment'
  scope: rg
  params: {
    keyVaultName: 'kv-secure-chat-${envName}'
    storageAccountName: storage.outputs.storageAccountName
    openAiAccountName: 'cog-openai-chat-${envName}'
    acrName: 'aichatregistry-${envName}'
    
    // Injecting principal identification tags cleanly 
    appGatewayPrincipalId: security.outputs.appGatewayIdentityId
    apimPrincipalId: apim.outputs.apimId
    chatBackendPrincipalId: apps.outputs.chatBackendPrincipalId // Make sure to export this principalId from apps.bicep!
    voiceBackendPrincipalId: apps.outputs.voiceBackendPrincipalId
    snowShimPrincipalId: apps.outputs.snowShimPrincipalId
    acaEnvironmentPrincipalId: containerEnv.outputs.environmentId
  }
  dependsOn: [
    security
    storage
    network
    apim
    apps
    containerEnv
    registry
    rg
  ]
}

output resourceGroupId string = rg.id
output vnetId string = network.outputs.vnetId
output keyVaultUri string = security.outputs.keyVaultUri
output storageAccountName string = storage.outputs.storageAccountName
output registryLoginServer string = registry.outputs.registryLoginServer
output aiProjectConnection string = '${aifoundry.outputs.openAiEndpoint}/api/projects/ai-project-chat-${envName}'

output internalFrontendUrl string = apps.outputs.frontendFqdn
output internalChatBackendUrl string = apps.outputs.chatBackendFqdn
output internalVoiceBackendUrl string = apps.outputs.voiceBackendFqdn
output internalSnowShimUrl string = apps.outputs.snowShimFqdn
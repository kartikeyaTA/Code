metadata description = 'Deploys an internally-isolated Azure Container Apps Managed Environment attached to a delegated private subnet.'

param envName string
param location string 
param acaSubnetId string
param apimSubnetId string
param vnetId string
param privateEndpointSubnetId string

// Reference the existing telemetry workspace to dynamically fetch operational ingestion keys
resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: 'log-analytics-ai-chat-${envName}'
}

var environmentNamepri = 'aca-env-chat-pri-${envName}'

// 1. Isolated Azure Container Apps Managed Environment (Private)
resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentNamepri
  location: location
  properties: {
    // Logging Configuration - Pipes all microservice console logs straight to Step 1
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: logAnalyticsWorkspace.listKeys().primarySharedKey
      }
    }
    // Network Configuration - Seals the cluster behind a private boundary
    vnetConfiguration: {
      internal: true // ◄ Crucial: Completely hides the cluster behind an Internal Load Balancer
      infrastructureSubnetId: apimSubnetId // Binds to our delegated /23 subnet space
    }
    zoneRedundant: false 
  }
}

// ============================================================================
// 2. REGIONAL PRIVATE DNS ZONE & VIRTUAL NETWORK LINK
// ============================================================================
// Standardized Azure Container Apps regional private link namespace
resource acaPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.${location}.azurecontainerapps.io'
  location: 'global'
}

// Binds the Private DNS tree natively to your target VNet routing mesh
resource acaVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: acaPrivateDnsZone
  name: '${environmentNamepri}-vnet-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

module wildcardDnsRecord './dns_wildcard.bicep' = {
  name: 'aca-wildcard-dns-deployment'
  params: {
    privateDnsZoneName: acaPrivateDnsZone.name
    // Safely extracts the runtime random domain string inside the child deployment scope
    recordName: '*.${split(managedEnvironment.properties.defaultDomain, '.')[0]}'
    staticIp: acaPrivateEndpoint.properties.customDnsConfigs[0].ipAddresses[0]
  }
  dependsOn: [
    acaDnsZoneGroup
  ]
}

// ============================================================================
// 3. SECURED PRIVATE ENDPOINT
// ============================================================================
resource acaPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-${environmentNamepri}'
  location: location
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'plsc-${environmentNamepri}'
        properties: {
          privateLinkServiceId: managedEnvironment.id
          groupIds: [
            'managedEnvironments'
          ]
        }
      }
    ]
  }
}

// ============================================================================
// 4. AUTOMATED DNS ZONE GROUP (The Record Mapping Engine)
// ============================================================================
resource acaDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: acaPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'aca-env-config'
        properties: {
          privateDnsZoneId: acaPrivateDnsZone.id
        }
      }
    ]
  }
}

var environmentNamepub = 'aca-env-chat-pub-${envName}'

// 5. Public Azure Container Apps Managed Environment (External)
resource managedEnvironmentpri 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentNamepub
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: logAnalyticsWorkspace.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      internal: false // ◄ Exposed externally via public load balancer
      infrastructureSubnetId: acaSubnetId 
    }
    zoneRedundant: false 
  }
}

// ============================================================================
// OUTPUT VALUES
// ============================================================================
output environmentId string = managedEnvironment.id
output environmentDefaultDomain string = managedEnvironment.properties.defaultDomain
output environmentStaticIp string = managedEnvironment.properties.staticIp

output environmentIdpri string = managedEnvironmentpri.id
output environmentDefaultDomainpri string = managedEnvironmentpri.properties.defaultDomain
output environmentStaticIppri string = managedEnvironmentpri.properties.staticIp
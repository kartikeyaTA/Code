metadata description = 'Establishes the foundational Virtual Network, subnet segmentation, dedicated APIM NSG safety profiles, and outbound NAT Gateway architecture.'

param envName string
param location string 
param vnetName string
var natGatewayName = 'nat-outbound-${envName}'
var publicIpName = 'pip-nat-${envName}'
var apimNsgName = 'nsg-apim-${envName}'

// ============================================================================
// 1. SECURITY PLANE: SECURITY GROUPS WITH ALLOWANCE RULES
// ============================================================================
resource apimNsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: apimNsgName
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow_APIM_Management_Inbound'
        properties: {
          description: 'Mandatory Azure platform control plane routing port for internal APIM deployments.'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '3443'
          sourceAddressPrefix: 'ApiManagement'
          destinationAddressPrefix: 'VirtualNetwork'
          access: 'Allow'
          priority: 100
          direction: 'Inbound'
        }
      }
      // ◄ FIXED: Allows the upstream Web Application Firewall to cross subnet boundaries over Web Channels
      {
          name: 'Allow_WAF_to_APIM_Inbound'
          properties: {
            description: 'Allows inbound HTTPS traffic from the Internet service tag directly to the Virtual Network.'
            protocol: 'Tcp'
            sourcePortRange: '*'
            // 1. Changes port array to a single string parameter for port 443
            destinationPortRanges: [
            '80'
            '443'
            ]
            // 2. Swaps local VNet subnet strings to official platform Service Tags
            sourceAddressPrefix: 'Internet'
            destinationAddressPrefix: 'VirtualNetwork'
            access: 'Allow'
            priority: 110
            direction: 'Inbound'
          }
      }
    ]
  }
}

// ============================================================================
// 2. EDGE PLANE: OUTBOUND PUBLIC NAT ENGINE
// ============================================================================
resource publicIP 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: publicIpName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static' 
  }
}

resource natGateway 'Microsoft.Network/natGateways@2023-11-01' = {
  name: natGatewayName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIpAddresses: [
      {
        id: publicIP.id 
      }
    ]
    idleTimeoutInMinutes: 5
  }
}

// ============================================================================
// 3. CORE NETWORK PLANE: VIRTUAL NETWORK WITH BINDINGS
// ============================================================================
resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.0.0.0/16' 
      ]
    }
    subnets: [
      {
        name: 'snet-agw'
        properties: {
          addressPrefix: '10.0.1.0/24' 
          serviceEndpoints: [
            {
              service: 'Microsoft.CognitiveServices'
              locations: [
                location
              ]
            }
          ]
        }
      }
      {
        name: 'snet-foundry-agents'
        properties: {
          addressPrefix: '10.0.8.0/24'
          defaultOutboundAccess: false
          natGateway: {
            id: natGateway.id
          }
          
          // ◄ MOVE IT HERE: Direct child of subnet properties
          serviceEndpoints: [
            {
              service: 'Microsoft.CognitiveServices'
              locations: [
                location
              ]
            }
          ]
          
          delegations: [
            {
              name: 'foundry-agent-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments' // ◄ Keep ONLY this here
              }
            }
          ]
        }
      }
      {
        name: 'snet-devops-runners'
        properties: {
          addressPrefix: '10.0.7.0/24' // Dedicated segment for deployment runners
          serviceEndpoints: [
            {
              service: 'Microsoft.CognitiveServices'
              locations: [
                location
              ]
            }
          ]
          natGateway: {
            id: natGateway.id // Inherits the secure outbound NAT Gateway IP
          }
        }
      }
      {
        name: 'snet-apim'
        properties: {
          addressPrefix: '10.0.2.0/24' 
          defaultOutboundAccess: false
          networkSecurityGroup: {
            id: apimNsg.id
          }
          natGateway: {
            id: natGateway.id
          }
          delegations: [
            {
              name: 'aca-runtime-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments' 
              }
            }
          ]
          serviceEndpoints: [
            {
              service: 'Microsoft.Sql'
            }
            {
              service: 'Microsoft.Storage'
            }
            {
              service: 'Microsoft.AzureActiveDirectory'
            }
            {
              service: 'Microsoft.ServiceBus' 
            }
            {
              service: 'Microsoft.KeyVault'    
            }
            {
              service: 'Microsoft.EventHub'
            }
            {
              service: 'Microsoft.CognitiveServices'
            }
          ]
        }
      }
      {
        name: 'snet-container-apps'
        properties: {
          addressPrefix: '10.0.4.0/23'
              serviceEndpoints: [
            {
              service: 'Microsoft.CognitiveServices'
              locations: [
                location
              ]
            }
          ] 
          natGateway: {
            id: natGateway.id 
          }
          delegations: [
            {
              name: 'aca-runtime-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments' 
              }
            }
          ]
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: '10.0.6.0/24' 
          privateEndpointNetworkPolicies: 'Disabled' 
          serviceEndpoints: [
            {
              service: 'Microsoft.CognitiveServices'
              locations: [
                location
              ]
            }
          ]
        }
      }
    ]
  }
}

// ============================================================================
// STRUCTURAL REFERENCE TOKEN OUTPUTS
// ============================================================================
output vnetId string = vnet.id
output vnetName string = vnet.name
output agwSubnetId string = '${vnet.id}/subnets/snet-agw'
output apimSubnetId string = '${vnet.id}/subnets/snet-apim'
output acaSubnetId string = '${vnet.id}/subnets/snet-container-apps'
output endpointsSubnetId string = '${vnet.id}/subnets/snet-private-endpoints'
output natPublicIpAddress string = publicIP.properties.ipAddress
output devopsSubnetId string = '${vnet.id}/subnets/snet-devops-runners'
output agentSubnetId string = '${vnet.id}/subnets/snet-foundry-agents'
metadata description = 'Establishes the foundational Virtual Network, subnet segmentation, and outbound NAT Gateway architecture.'

param envName string
param location string 

var vnetName = 'vnet-ai-chat-${envName}'
var natGatewayName = 'nat-outbound-${envName}'
var publicIpName = 'pip-nat-${envName}'

// 1. Static Public IP Address for the Outbound NAT Gateway
resource publicIP 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: publicIpName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static' // Enforces that this IP will never change
  }
}

// 2. NAT Gateway Appliance for Secure, Predictable Outbound External Routing
resource natGateway 'Microsoft.Network/natGateways@2023-11-01' = {
  name: natGatewayName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIpAddresses: [
      {
        id: publicIP.id // Binds our static public IP to the NAT engine
      }
    ]
    idleTimeoutInMinutes: 5
  }
}

// 3. Core Virtual Network with Microservice and Data Subnet Zoning
resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.0.0.0/16' // The overall master network wrapper
      ]
    }
    subnets: [
      {
        name: 'snet-agw'
        properties: {
          addressPrefix: '10.0.1.0/24' // Public WAF subnet boundary
        }
      }
      {
        name: 'snet-apim'
        properties: {
          addressPrefix: '10.0.2.0/24' // Private APIM management hub boundary
        }
      }
      {
        name: 'snet-container-apps'
        properties: {
          addressPrefix: '10.0.4.0/23' // /23 allocation (512 IPs) for auto-scaling containers
          natGateway: {
            id: natGateway.id // Forces all outbound internet traffic from this subnet through the NAT
          }
          delegations: [
            {
              name: 'aca-runtime-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments' // Hands control over to the Container Apps infrastructure
              }
            }
          ]
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: '10.0.6.0/24' // Deep-isolation bucket for data/AI services
          privateEndpointNetworkPolicies: 'Disabled' // Mandatory setting for private endpoints to attach successfully
        }
      }
    ]
  }
}

// Export specific infrastructure reference tokens for downstream modules
output vnetId string = vnet.id
output vnetName string = vnet.name
output agwSubnetId string = '${vnet.id}/subnets/snet-agw'
output apimSubnetId string = '${vnet.id}/subnets/snet-apim'
output acaSubnetId string = '${vnet.id}/subnets/snet-container-apps'
output endpointsSubnetId string = '${vnet.id}/subnets/snet-private-endpoints'
output natPublicIpAddress string = publicIP.properties.ipAddress // Outputs the IP string for documentation or whitelisting
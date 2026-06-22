param envName string
param location string
param vnetNameParam string
param natGatewayNameParam string

var vnetName = '${vnetNameParam}-${envName}'
var natGatewayName = '${natGatewayNameParam}-${envName}'
var publicIpName = 'pip-nat-${envName}'

// 1. Static Public IP for Outbound NAT Traffic
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

// 2. NAT Gateway to ensure a single outbound IP for ServiceNow firewall white-listing
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
  }
}

// 3. Virtual Network Container
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
        name: 'snet-apim'
        properties: {
          addressPrefix: '10.0.1.0/24'
        }
      }
      {
        name: 'snet-container-apps'
        properties: {
          addressPrefix: '10.0.2.0/23' // /23 grants 512 IPs required by internal ACA routing
          natGateway: {
            id: natGateway.id
          }
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'snet-integration-function'
        properties: {
          addressPrefix: '10.0.4.0/24'
          natGateway: {
            id: natGateway.id
          }
          delegations: [
            {
              name: 'function-delegation'
              properties: {
                serviceName: 'Microsoft.Web/serverFarms'
              }
            }
          ]
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: '10.0.5.0/24'
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output acaSubnetId string = '${vnet.id}/subnets/snet-container-apps'
output endpointsSubnetId string = '${vnet.id}/subnets/snet-private-endpoints'
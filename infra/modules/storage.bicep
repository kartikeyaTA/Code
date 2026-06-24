metadata description = 'Deploys an enterprise-grade secure Azure Storage Account for JSONL transcripts, isolated via Private Endpoint and Private DNS.'

param envName string
param location string 
param vnetId string
param endpointsSubnetId string

var storageAccountName = 'stachattranscripts-${envName}'
var privateEndpointName = 'pe-storage-blob-${envName}'
var blobDnsZoneName = 'privatelink.blob.${environment().suffixes.storage}'

// 1. Storage Account Definition with Full Internet Disablement
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS' // Standard Locally Redundant Storage for optimized cost-to-performance ratio
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    publicNetworkAccess: 'Disabled' // ◄ Shuts down all inbound internet access paths
    allowBlobPublicAccess: false    // Prevents anonymous web access to any container files
    minimumTlsVersion: 'TLS1_2'     // Enforces modern cryptographic compliance
    networkAcls: {
      bypass: 'AzureServices'       // Allows internal Azure backend planes (like logging) to communicate
      defaultAction: 'Deny'
    }
  }
}

// Instantiate the foundational Blob Service container layer
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

// Create the explicit storage bucket for holding raw JSONL text streams
resource transcriptContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'transcripts'
}

// 2. Private Endpoint Creation (Injects the service into snet-private-endpoints)
resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: privateEndpointName
  location: location
  properties: {
    subnet: {
      id: endpointsSubnetId // Plugs cleanly into our Step 2 subnet
    }
    privateLinkServiceConnections: [
      {
        name: 'storage-blob-connection'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: [
            'blob' // Specifying the isolated blob data plane subgroup
          ]
        }
      }
    ]
  }
}

// 3. Private DNS Zone for Local Subnet Resolution
resource blobDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: blobDnsZoneName
  location: 'global'
}

// Explicitly link this local DNS Zone to the primary architecture VNet
resource dnsZoneVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: blobDnsZone
  name: 'link-${storageAccountName}-vnet'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

// Automatic DNS Configuration mapping Private Endpoint IP to the DNS Zone
resource dnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: privateEndpoint
  name: 'blobPrivateDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'storage-blob-config'
        properties: {
          privateDnsZoneId: blobDnsZone.id
        }
      }
    ]
  }
}

// Export output values for downstream app permissions mapping
output storageAccountId string = storageAccount.id
output storageAccountName string = storageAccount.name
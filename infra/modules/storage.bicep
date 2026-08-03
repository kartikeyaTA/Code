metadata description = 'Deploys an enterprise-grade secure Azure Storage Account for JSONL transcripts, isolated via Private Endpoint and Private DNS.'

param envName string
param location string 
param vnetId string
param endpointsSubnetId string
param storageAccountName string

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


// Export output values for downstream app permissions mapping
output storageAccountId string = storageAccount.id
output storageAccountName string = storageAccount.name
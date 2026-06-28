metadata description = 'Provisions a secure self-hosted Azure DevOps runner node matching the verified portal infrastructure parameters.'

param envName string
param location string
param devopsSubnetId string
param adminUsername string = 'kartikeya'

@secure()
param adminPassword string

// Aligns parameters exactly with your live portal configuration
var vmName = 'vm-devops-runner-${envName}'
var nicName = 'nic-runner-${envName}'
var contributorRoleDefinitionId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'

// 1. Private Network Interface Card (NIC) bounded to your network topology
resource nic 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: nicName
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig-internal'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: devopsSubnetId
          }
        }
      }
    ]
  }
}

// 2. Private Deployment VM Instance (Matching your successful portal setup)
resource vm 'Microsoft.Compute/virtualMachines@2023-09-01' = {
  name: vmName
  location: location
  identity: {
    type: 'SystemAssigned' // ◄ Spawns identity credentials for passwordless automation execution
  }
  properties: {
    hardwareProfile: {
      vmSize: 'Standard_D2s_v3' // ◄ Mapped from your verified unrestricted capacity tier
    }
    securityProfile: {
      securityType: 'TrustedLaunch' // Matches portal baseline security parameters
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
    storageProfile: {
      imageReference: {
        publisher: 'canonical'
        offer: 'ubuntu-24_04-lts' // ◄ Mapped directly from your successful image payload
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        name: 'disk-os-runner-${envName}'
        createOption: 'FromImage'
        managedDisk: {
          storageAccountType: 'StandardSSD_LRS' // Enforces clean string conversion
        }
        diskSizeGB: 30
      }
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      adminPassword: adminPassword
      linuxConfiguration: {
        disablePasswordAuthentication: false
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
        }
      ]
    }
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true // Allows browser terminal execution via Serial Console
      }
    }
  }
}

// 3. Integrated Role Assignment (Inherits your file deployment group scope naturally)
resource vmRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().subscriptionId, resourceGroup().id, vmName, contributorRoleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRoleDefinitionId)
    principalId: vm.identity.principalId // ◄ Grants permissions to this VM identity automatically
    principalType: 'ServicePrincipal'
  }
}

// Export structural references for upstream orchestration visibility
output vmPrincipalId string = vm.identity.principalId
output vmName string = vm.name

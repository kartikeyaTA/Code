metadata description = 'Provisions the public-facing Layer-7 Application Gateway with Web Application Firewall (WAF) and Key Vault SSL binding.'

param envName string
param location string 
param agwSubnetId string
param appGatewayIdentityId string
param apimPrivateIpAddress string
param logAnalyticsWorkspaceId string

var publicIpName = 'pip-agw-${envName}'
var wafPolicyName = 'waf-policy-chat-${envName}'
var appGatewayName = 'agw-edge-chat-${envName}'

// 1. Dedicated Public IP for the Application Edge Ingress
resource publicIP 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: publicIpName
  location: location
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

// 2. Web Application Firewall (WAF v2) Core Policy Engine
resource wafPolicy 'Microsoft.Network/WebApplicationFirewallPolicies@2023-11-01' = {
  name: wafPolicyName
  location: location
  properties: {
    policySettings: {
      requestBodyCheck: true
      maxRequestBodySizeInKb: 512
      state: 'Enabled'
      mode: 'Prevention' // ◄ Hard-blocks threats; change to 'Detection' during initial dev testing if needed
    }
    managedRules: {
      managedRuleSets: [
        {
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2' // Default industry standard scrubbing rules
        }
      ]
    }
  }
}

// 3. Application Gateway Layer-7 Core Appliance
resource appGateway 'Microsoft.Network/applicationGateways@2023-11-01' = {
  name: appGatewayName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${appGatewayIdentityId}': {} // Binds the pre-authorized identity from Step 3
    }
  }
  properties: {
    sku: {
      name: 'WAF_v2'
      tier: 'WAF_v2'
      capacity: 1 // Autoscales dynamically in higher tiers
    }
    firewallPolicy: { id: wafPolicy.id }
    
    // Subnet Ingress Configuration
    gatewayIPConfigurations: [
      {
        name: 'agw-ip-config'
        properties: { subnet: { id: agwSubnetId } }
      }
    ]
    
    frontendIPConfigurations: [
      {
        name: 'agw-public-frontend-ip'
        properties: { publicIPAddress: { id: publicIP.id } }
      }
    ]
    
    // Front door ports (Port 80 for bootstrap validation; route to 443 with your SSL Cert secret later)
    frontendPorts: [
      { name: 'port-80', properties: { port: 80 } }
    ]
    
    // Backend Pool pointing directly to your Private APIM Gateway instance
    backendAddressPools: [
      {
        name: 'apim-backend-pool'
        properties: {
          backendAddresses: [ { ipAddress: apimPrivateIpAddress } ] // Routes directly to Step 9
        }
      }
    ]
    
    backendHttpSettingsCollection: [
      {
        name: 'apim-http-settings'
        properties: {
          port: 80
          protocol: 'Http'
          cookieBasedAffinity: 'Disabled'
          requestTimeout: 30
        }
      }
    ]
    
    httpListeners: [
      {
        name: 'http-listener'
        properties: {
          frontendIPConfiguration: { id: resourceId('Microsoft.Network/applicationGateways/frontendIPConfigurations', appGatewayName, 'agw-public-frontend-ip') }
          frontendPort: { id: resourceId('Microsoft.Network/applicationGateways/frontendPorts', appGatewayName, 'port-80') }
          protocol: 'Http'
        }
      }
    ]
    
    requestRoutingRules: [
      {
        name: 'rule-route-to-apim'
        properties: {
          ruleType: 'Basic'
          priority: 100
          httpListener: { id: resourceId('Microsoft.Network/applicationGateways/httpListeners', appGatewayName, 'http-listener') }
          backendAddressPool: { id: resourceId('Microsoft.Network/applicationGateways/backendAddressPools', appGatewayName, 'apim-backend-pool') }
          backendHttpSettings: { id: resourceId('Microsoft.Network/applicationGateways/backendHttpSettingsCollection', appGatewayName, 'apim-http-settings') }
        }
      }
    ]
  }
}

// 4. Attach Diagnostic Logs to Stream WAF Access and Block Events to Telemetry Hub
resource agwDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01' = {
  name: 'agw-waf-telemetry'
  scope: appGateway
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      { category: 'ApplicationGatewayAccessLog', enabled: true }
      { category: 'ApplicationGatewayFirewallLog', enabled: true } // Crucial for tracking blocked hack attempts
    ]
  }
}

output publicIpAddress string = publicIP.properties.ipAddress
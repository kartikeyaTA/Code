metadata description = 'Provisions a public-facing Layer-7 Application Gateway with WAF routing directly to our pass-through APIM core via Public IP.'

param envName string
param location string 
param agwSubnetId string
param appGatewayIdentityId string
param apimPrivateIpAddress string
param logAnalyticsWorkspaceId string
param apimGatewayUrl string // Expecting gatewayUrl output from your APIM module

var publicIpName = 'pip-agw-${envName}'
var wafPolicyName = 'waf-policy-chat-${envName}'
var appGatewayName = 'agw-edge-chat-${envName}'

// Sanitized hostname string: Automatically extracts just the FQDN domain from the APIM Gateway URL
var cleanApimHost = replace(replace(apimGatewayUrl, 'https://', ''), '/', '')

// 1. Public IP address entry-point (Your direct browser access link)
resource publicIP 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: publicIpName
  location: location
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

// 2. Core Web Application Firewall (WAF v2) Policy Engine
resource wafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2023-11-01' = {
  name: wafPolicyName
  location: location
  properties: {
    policySettings: {
      requestBodyCheck: true
      maxRequestBodySizeInKb: 512
      state: 'Enabled'
      mode: 'Detection' 
    }
    managedRules: {
      managedRuleSets: [
        {
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
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
      '${appGatewayIdentityId}': {}
    }
  }
  properties: {
    sku: {
      name: 'WAF_v2'
      tier: 'WAF_v2'
      capacity: 1
    }
    firewallPolicy: { id: wafPolicy.id }
    
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
    
    frontendPorts: [
      { name: 'port-80', properties: { port: 80 } }
    ]
    
    backendAddressPools: [
      {
        name: 'apim-backend-pool'
        properties: {
          backendAddresses: [
            {
              ipAddress: apimPrivateIpAddress
            }
          ]
        }
      }
    ]

    probes: [
      {
        name: 'apim-health-probe'
        properties: {
          protocol: 'Https'
          host: cleanApimHost // ◄ FIXED: Uses clean parsed hostname parameter
          path: '/status-0123456789abcdef'
          interval: 30
          timeout: 30
          unhealthyThreshold: 3
          pickHostNameFromBackendHttpSettings: false
          minServers: 0
          match: {
            statusCodes: [
              '200-399'
            ]
          }
        }
      }
    ]
    
    backendHttpSettingsCollection: [
      {
        name: 'apim-http-settings'
        properties: {
          port: 443                            
          protocol: 'Https'                    
          cookieBasedAffinity: 'Disabled'
          requestTimeout: 30  
          pickHostNameFromBackendAddress: false 
          hostName: cleanApimHost // ◄ FIXED: Uses clean parsed hostname parameter
          // ◄ FIXED: Added missing probe relationship hook
          probe: {
            id: resourceId('Microsoft.Network/applicationGateways/probes', appGatewayName, 'apim-health-probe')
          }
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

// 4. Attach Diagnostic Telemetry for WAF Logs
resource agwDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'agw-waf-telemetry'
  scope: appGateway
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      { category: 'ApplicationGatewayAccessLog', enabled: true }
      { category: 'ApplicationGatewayFirewallLog', enabled: true }
    ]
  }
}

output publicIpAddress string = publicIP.properties.ipAddress
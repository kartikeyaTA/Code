extension microsoftGraph // ◄ Injects the Microsoft Graph capability into this template

metadata description = 'Automates the generation of the Microsoft Entra ID App Registration required for React SPA user authentication.'

param envName string
param customDomainName string 

var appDisplayName = 'app-ai-chat-frontend-${envName}'

// 1. Provision the Microsoft Entra ID App Registration Shell
resource frontendAppRegistration 'Microsoft.Graph/applications@v1.0' = {
  name: appDisplayName
  displayName: appDisplayName
  uniqueName: appDisplayName
  
  // Configure OAuth authentication profiles matching your React SPA structure
  spa: {
    redirectUris: [
      'https://${customDomainName}/' // ◄ Automatically whitelists your domain for login handshakes
    ]
  }
}

// 2. Instantiate a Service Principal matching the registration inside your tenant
resource appServicePrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: frontendAppRegistration.appId
}

// Export the parameters so they feed natively into Step 9 (APIM validation logic)
output tenantId string = tenant().tenantId
output frontendClientId string = frontendAppRegistration.appId
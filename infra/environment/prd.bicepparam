using '../main.bicep' // Points to your infrastructure file path

param envName = 'prd'
param resourceGroupName = 'ai-chatbot-prd'
param location = 'eastus2' 
param Project = 'AI-Chat-ServiceNow'
param ManagedBy = 'IAC_Bicep'
param vnetNameParam = 'vnet-ai-chat'
param natGatewayNameParam = 'nat-outbound'

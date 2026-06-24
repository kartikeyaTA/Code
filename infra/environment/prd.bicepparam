using '../main.bicep' // Points to your infrastructure file path

param envName = 'prod'
param resourceGroupName = 'ai-chatbot-prod'
param location = 'eastus2' 
param Project = 'AI-Chat-ServiceNow-prod'
param ManagedBy = 'IAC_Bicep'

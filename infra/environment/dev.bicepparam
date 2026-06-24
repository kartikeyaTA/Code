using '../main.bicep' // Points to your infrastructure file path

param envName = 'dev'
param resourceGroupName = 'ai-chatbot-dev'
param location = 'eastus2' 
param Project = 'AI-Chat-ServiceNow-dev'
param ManagedBy = 'IAC_Bicep'
param publisherEmail  = 'kartikeya532001@gmail.com'
param publisherName  = 'Enterprise-Cloud-Core'
param customDomainName = 'yourcompany.com'


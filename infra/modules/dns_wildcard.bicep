// infra/modules/dns_wildcard.bicep
metadata description = 'Helper module to safely evaluate dynamic runtime strings into static resource names for DNS A-Records.'

param privateDnsZoneName string
param recordName string
param staticIp string

resource acaPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: privateDnsZoneName
}

resource acaWildcardDnsRecord 'Microsoft.Network/privateDnsZones/A@2020-06-01' = {
  parent: acaPrivateDnsZone
  name: recordName
  properties: {
    ttl: 3600
    aRecords: [
      {
        ipv4Address: staticIp
      }
    ]
  }
}
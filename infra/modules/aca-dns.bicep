param zoneName string
param ip string
param vnetId string
resource zone 'Microsoft.Network/privateDnsZones@2020-06-01' = { name: zoneName, location: 'global' }
resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = { parent: zone, name: 'vnet', location: 'global', properties: { registrationEnabled: false, virtualNetwork: { id: vnetId } } }
resource record 'Microsoft.Network/privateDnsZones/A@2020-06-01' = { parent: zone, name: '*', properties: { ttl: 60, aRecords: [{ ipv4Address: ip }] } }

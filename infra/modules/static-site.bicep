@description('Static Web Apps name, unique within the resource group.')
param name string
@description('Azure Static Web Apps control-plane location; static content is globally distributed.')
param location string
param tags object
resource site 'Microsoft.Web/staticSites@2023-12-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Free', tier: 'Free' }
  properties: { publicNetworkAccess: 'Enabled' }
}
output hostname string = site.properties.defaultHostname

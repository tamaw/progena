targetScope = 'resourceGroup'

@allowed(['sit', 'prd'])
param environment string
param location string = resourceGroup().location
@description('Static Web Apps resource location; verify supported locations and residency for your subscription.')
param staticSiteLocation string = 'eastasia'
@description('Globally unique, lowercase alphanumeric prefix, 3-10 characters.')
@minLength(3)
@maxLength(10)
param namePrefix string = 'progena'
@secure()
@description('PostgreSQL administrator password. Supply securely; do not commit it.')
param postgresAdminPassword string
param postgresAdminUser string = 'progenaadmin'
@description('PFX contents, base64 encoded, for the public gateway HTTPS listener.')
@secure()
param gatewayCertificatePfx string = ''
@secure()
param gatewayCertificatePassword string = ''
@description('Public hostname covered by the gateway certificate.')
param publicHostname string = ''
@description('Existing container image references. Must be available from the created ACR.')
param backendImage string = ''
param runtimeImage string = ''
@description('Existing PostgreSQL URI in Key Vault secret, including TLS. Populate after database initialization.')
param databaseSecretName string = 'progena-database-url'
@description('Existing provider encryption key stored in Key Vault.')
param providerKeySecretName string = 'progena-provider-encryption-key'
@description('Existing Foundry credential in Key Vault, if using API-key authentication.')
param foundryKeySecretName string = 'foundry-api-key'
param postgresSku string = 'Standard_B1ms'
param postgresStorageGb int = 32
param runtimeCpu string = '1.0'
param backendCpu string = '0.5'
@description('Disabled until Entra authentication and authorization are implemented and reviewed.')
param deployUnprotectedDemo bool = false
var suffix = take(uniqueString(subscription().id, resourceGroup().id, environment), 8)
var base = '${namePrefix}-${environment}-${suffix}'
var baseWebName = '${base}-web'
var storageName = take('${namePrefix}${environment}${suffix}fs', 24)
var registryName = take('${namePrefix}${environment}${suffix}acr', 50)
var acaName = '${base}-aca'
var runtimeName = '${base}-runtime'
var backendName = '${base}-backend'
var gatewayName = '${base}-agw'
var gatewayId = resourceId('Microsoft.Network/applicationGateways', gatewayName)
var gatewayInternal = '${base}-backend.${aca.properties.defaultDomain}'
var tags = { environment: environment, application: 'progena', managedBy: 'bicep' }

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: '${base}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.60.0.0/16'] }
    subnets: [
      { name: 'gateway', properties: { addressPrefix: '10.60.0.0/24' } }
      { name: 'containerapps', properties: { addressPrefix: '10.60.2.0/23', delegations: [{ name: 'aca', properties: { serviceName: 'Microsoft.App/environments' } }] } }
      { name: 'postgres', properties: { addressPrefix: '10.60.4.0/24', delegations: [{ name: 'postgres', properties: { serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers' } }] } }
    ]
  }
}
resource pgDns 'Microsoft.Network/privateDnsZones@2020-06-01' = { name: '${base}.postgres.database.azure.com', location: 'global' }
resource pgDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: pgDns
  name: 'vnet'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: vnet.id } }
}
resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: '${base}-pg'
  location: location
  tags: tags
  sku: { name: postgresSku, tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: postgresAdminUser
    administratorLoginPassword: postgresAdminPassword
    storage: { storageSizeGB: postgresStorageGb }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { delegatedSubnetResourceId: '${vnet.id}/subnets/postgres', privateDnsZoneArmResourceId: pgDns.id }
  }
  dependsOn: [pgDnsLink]
}
resource extensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview' = {
  parent: postgres
  name: 'azure.extensions'
  properties: { value: 'vector', source: 'user-override' }
}
resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: postgres
  name: 'progena'
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}
resource backendDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: postgres
  name: 'backend'
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}
resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${base}-logs'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30, publicNetworkAccessForIngestion: 'Enabled', publicNetworkAccessForQuery: 'Enabled' }
}
resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${base}-insights'
  location: location
  kind: 'web'
  tags: tags
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false, publicNetworkAccess: 'Enabled' }
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = { name: '${base}-identity', location: location, tags: tags }
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, identity.id, 'acrpull')
  properties: { principalId: identity.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') }
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: take('${base}-kv', 24)
  location: location
  tags: tags
  properties: { tenantId: tenant().tenantId, sku: { family: 'A', name: 'standard' }, enableRbacAuthorization: true, enableSoftDelete: true, softDeleteRetentionInDays: 90, enablePurgeProtection: true, publicNetworkAccess: 'Enabled' }
}
resource vaultRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, identity.id, 'secret-reader')
  properties: { principalId: identity.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') }
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { supportsHttpsTrafficOnly: true, minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false, allowSharedKeyAccess: true, publicNetworkAccess: 'Enabled' }
}
resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = { parent: storage, name: 'default' }
resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: 'worktrees'
  properties: { accessTier: 'TransactionOptimized', shareQuota: 100 }
}
resource aca 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: acaName
  location: location
  tags: tags
  properties: {
    vnetConfiguration: { internal: true, infrastructureSubnetId: '${vnet.id}/subnets/containerapps' }
    appLogsConfiguration: { destination: 'log-analytics', logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey } }
    zoneRedundant: false
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}
module acaDns 'modules/aca-dns.bicep' = {
  name: 'aca-private-dns'
  params: { zoneName: aca.properties.defaultDomain, ip: aca.properties.staticIp, vnetId: vnet.id }
}
resource mount 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: aca
  name: 'worktrees'
  properties: { azureFile: { accountName: storage.name, accountKey: storage.listKeys().keys[0].value, shareName: share.name, accessMode: 'ReadWrite' } }
}
module site 'modules/static-site.bicep' = {
  name: 'static-site'
  params: { name: baseWebName, location: staticSiteLocation, tags: tags }
}
resource foundry 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: '${base}-ai'
  location: location
  kind: 'OpenAI'
  sku: { name: 'S0' }
  tags: tags
  properties: { customSubDomainName: '${base}-ai', publicNetworkAccess: 'Enabled' }
}

var appSecrets = [
  { name: 'database-url', keyVaultUrl: '${vault.properties.vaultUri}secrets/${databaseSecretName}', identity: identity.id }
  { name: 'provider-key', keyVaultUrl: '${vault.properties.vaultUri}secrets/${providerKeySecretName}', identity: identity.id }
  { name: 'foundry-key', keyVaultUrl: '${vault.properties.vaultUri}secrets/${foundryKeySecretName}', identity: identity.id }
]
var appEnv = [
  { name: 'PROGENA_DATABASE_URL', secretRef: 'database-url' }
  { name: 'BACKEND_DATABASE_NAME', value: 'backend' }
  { name: 'PROGENA_PROVIDER_ENCRYPTION_KEY', secretRef: 'provider-key' }
  { name: 'AZURE_OPENAI_API_KEY', secretRef: 'foundry-key' }
  { name: 'AZURE_OPENAI_BASE_URL', value: 'https://${foundry.name}.openai.azure.com/' }
  { name: 'PROGENA_WORKTREE_DIR', value: '/app/repos' }
]
resource runtime 'Microsoft.App/containerApps@2024-03-01' = if (deployUnprotectedDemo) {
  name: runtimeName
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    managedEnvironmentId: aca.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: false, targetPort: 9501, allowInsecure: false, transport: 'auto' }
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
      secrets: appSecrets
    }
    template: {
      scale: { minReplicas: 1, maxReplicas: 1 }
      volumes: [{ name: 'worktrees', storageType: 'AzureFile', storageName: mount.name, mountOptions: 'uid=10001,gid=10001,dir_mode=0770,file_mode=0660' }]
      containers: [{
        name: 'runtime'
        image: runtimeImage
        resources: { cpu: json(runtimeCpu), memory: '2Gi' }
        env: concat(appEnv, [{ name: 'PROGENA_HTTP_HOST', value: '0.0.0.0' }, { name: 'PROGENA_BACKEND_URL', value: 'https://${backendName}.${aca.properties.defaultDomain}' }])
        volumeMounts: [{ volumeName: 'worktrees', mountPath: '/app/repos' }]
        probes: [{ type: 'Startup', httpGet: { path: '/health', port: 9501 }, initialDelaySeconds: 10, periodSeconds: 10, failureThreshold: 24 }]
      }]
    }
  }
  dependsOn: [acrPull, vaultRead]
}
resource backend 'Microsoft.App/containerApps@2024-03-01' = if (deployUnprotectedDemo) {
  name: backendName
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    managedEnvironmentId: aca.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      // External to the environment but private to the VNet because the environment is internal.
      ingress: { external: true, targetPort: 8000, allowInsecure: false, transport: 'auto' }
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
      secrets: appSecrets
    }
    template: {
      scale: { minReplicas: 1, maxReplicas: 1 }
      volumes: [{ name: 'worktrees', storageType: 'AzureFile', storageName: mount.name, mountOptions: 'uid=10001,gid=10001,dir_mode=0770,file_mode=0660' }]
      containers: [{
        name: 'backend'
        image: backendImage
        resources: { cpu: json(backendCpu), memory: '1Gi' }
        env: concat(appEnv, [{ name: 'PROGENA_RUNTIME_URL', value: 'https://${runtimeName}.internal.${aca.properties.defaultDomain}' }])
        volumeMounts: [{ volumeName: 'worktrees', mountPath: '/app/repos' }]
        probes: [{ type: 'Startup', httpGet: { path: '/healthz', port: 8000 }, initialDelaySeconds: 10, periodSeconds: 10, failureThreshold: 24 }]
      }]
    }
  }
  dependsOn: [acrPull, vaultRead]
}
resource publicIp 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: '${base}-pip'
  location: location
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}
resource gateway 'Microsoft.Network/applicationGateways@2024-05-01' = if (deployUnprotectedDemo) {
  name: gatewayName
  location: location
  tags: tags
  properties: {
    sku: { name: 'Basic', tier: 'Basic' }
    gatewayIPConfigurations: [{ name: 'gateway', properties: { subnet: { id: '${vnet.id}/subnets/gateway' } } }]
    frontendIPConfigurations: [{ name: 'public', properties: { publicIPAddress: { id: publicIp.id } } }]
    frontendPorts: [{ name: 'https', properties: { port: 443 } }]
    sslCertificates: [{ name: 'tls', properties: { data: gatewayCertificatePfx, password: gatewayCertificatePassword } }]
    httpListeners: [{ name: 'https', properties: { frontendIPConfiguration: { id: '${gatewayId}/frontendIPConfigurations/public' }, frontendPort: { id: '${gatewayId}/frontendPorts/https' }, protocol: 'Https', sslCertificate: { id: '${gatewayId}/sslCertificates/tls' }, hostName: publicHostname, requireServerNameIndication: true } }]
    backendAddressPools: [
      { name: 'frontend', properties: { backendAddresses: [{ fqdn: site.outputs.hostname }] } }
      { name: 'backend', properties: { backendAddresses: [{ fqdn: gatewayInternal }] } }
    ]
    backendHttpSettingsCollection: [
      { name: 'frontend', properties: { port: 443, protocol: 'Https', cookieBasedAffinity: 'Disabled', pickHostNameFromBackendAddress: true, requestTimeout: 60 } }
      { name: 'backend', properties: { port: 443, protocol: 'Https', cookieBasedAffinity: 'Disabled', pickHostNameFromBackendAddress: true, requestTimeout: 240, probe: { id: '${gatewayId}/probes/backend' } } }
    ]
    probes: [{ name: 'backend', properties: { protocol: 'Https', pickHostNameFromBackendHttpSettings: true, path: '/healthz', interval: 30, timeout: 10, unhealthyThreshold: 3 } }]
    urlPathMaps: [{
      name: 'routes'
      properties: {
        defaultBackendAddressPool: { id: '${gatewayId}/backendAddressPools/frontend' }
        defaultBackendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/frontend' }
        pathRules: [{ name: 'api', properties: { paths: ['/api/*', '/v1/*'], backendAddressPool: { id: '${gatewayId}/backendAddressPools/backend' }, backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/backend' } } }]
      }
    }]
    requestRoutingRules: [{ name: 'route', properties: { ruleType: 'PathBasedRouting', priority: 100, httpListener: { id: '${gatewayId}/httpListeners/https' }, urlPathMap: { id: '${gatewayId}/urlPathMaps/routes' } } }]
    globalConfiguration: { enableResponseBuffering: false }
  }
  dependsOn: [acaDns, backend]
}
output gatewayPublicIp string = publicIp.properties.ipAddress
output staticWebAppHostname string = site.outputs.hostname
output registryLoginServer string = registry.properties.loginServer
output vaultName string = vault.name
output postgresFqdn string = postgres.properties.fullyQualifiedDomainName
output foundryEndpoint string = 'https://${foundry.name}.openai.azure.com/'





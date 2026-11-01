using '../main.bicep'
param environment = 'sit'
param location = 'australiaeast'
param postgresSku = 'Standard_B1ms'
param postgresStorageGb = 32
param postgresAdminPassword = readEnvironmentVariable('PROGENA_PG_ADMIN_PASSWORD')


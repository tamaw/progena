using '../main.bicep'
// Placeholder only. PRD sizing, access, reliability, and secrets require review.
param environment = 'prd'
param location = 'australiaeast'
param postgresSku = 'Standard_B1ms'
param postgresStorageGb = 32
param postgresAdminPassword = readEnvironmentVariable('PROGENA_PG_ADMIN_PASSWORD')


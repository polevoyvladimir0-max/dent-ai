#!/usr/bin/env pwsh
[CmdletBinding()]
param(
    [string]$SourceRoot = "C:\dent_ai",
    [string]$BackupDir = "C:\dent_ai\backups",
    [switch]$IncludeStorage
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path $SourceRoot)) {
    throw "Source root '$SourceRoot' не существует."
}

New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupName = "dent_ai_$timestamp.zip"
$target = Join-Path $BackupDir $backupName

$relativePaths = @(
    'pricing_catalog.xlsx',
    'staging_price_items.csv',
    'db',
    'scripts',
    'infra',
    'fonts',
    'app',
    'bot',
    'agent',
    'pdf_generator.py',
    'docker-compose.yml'
)

if ($IncludeStorage) {
    $relativePaths += 'storage'
}

$resolved = @()
foreach ($rel in $relativePaths) {
    $full = Join-Path $SourceRoot $rel
    if (Test-Path $full) {
        $resolved += $full
    }
}

if (-not $resolved) {
    throw 'Нет доступных путей для бэкапа.'
}

if (Test-Path $target) {
    Remove-Item $target -Force
}

Compress-Archive -Path $resolved -DestinationPath $target -CompressionLevel Optimal

Write-Host "Backup created: $target"

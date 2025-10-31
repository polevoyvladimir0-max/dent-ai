#!/usr/bin/env pwsh
[CmdletBinding()]
param(
    [string]$ProjectRoot = "C:\dent_ai",
    [switch]$SkipIngest,
    [switch]$SkipMigrate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-InProject {
    param([string]$Command)
    Push-Location $ProjectRoot
    try {
        Write-Host "==> $Command"
        Invoke-Expression $Command
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path $ProjectRoot)) {
    throw "Project root '$ProjectRoot' не найден."
}

if (-not $SkipMigrate) {
    Invoke-InProject "python scripts/extract_pricing.py"
    Invoke-InProject "python -m scripts.calc_plan --help"  # sanity-check python env
}

if (-not $SkipIngest) {
    Invoke-InProject "python scripts/ingest_pricing.py"
}

Invoke-InProject "docker compose up -d --build"

Write-Host 'Deploy completed.'

#requires -Version 7

[CmdletBinding()]
param(
    [ValidateSet("compose", "ci")]
    [string]$Target = "compose",

    [string]$Profile,

    [string]$SchemaPath,

    [string]$SecretsPath,

    [string]$OutputPath,

    [switch]$Force,

    [switch]$DryRun,

    [switch]$Quiet
)

Set-StrictMode -Version Latest

function Resolve-SchemaPath {
    param([string]$PathOverride)
    if ($PathOverride) {
        return (Resolve-Path -Path $PathOverride -ErrorAction Stop).Path
    }
    $default = Join-Path -Path (Split-Path -Path $PSCommandPath -Parent) -ChildPath "..\env\schema.json"
    return (Resolve-Path -Path $default -ErrorAction Stop).Path
}

function Merge-Hashtable {
    param([hashtable]$Target, [hashtable]$Source)
    foreach ($key in $Source.Keys) {
        $Target[$key] = $Source[$key]
    }
}

function Read-JsonAggregate {
    param([string]$Path)
    if (-not $Path) { return @{} }
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Secrets file or directory '$Path' not found"
    }

    $item = Get-Item -LiteralPath $Path
    if ($item.PSIsContainer) {
        $result = @{}
        $files = Get-ChildItem -LiteralPath $Path -Filter '*.json' | Sort-Object Name
        foreach ($file in $files) {
            Merge-Hashtable -Target $result -Source (Read-JsonAggregate -Path $file.FullName)
        }
        return $result
    }

    $raw = Get-Content -LiteralPath $item.FullName -Encoding UTF8 -Raw
    if (-not $raw) { return @{} }
    return ConvertFrom-Json -InputObject $raw -AsHashtable
}

function To-InvariantString {
    param($Value)
    if ($null -eq $Value) { return "" }
    if ($Value -is [string]) { return $Value }
    return [System.Convert]::ToString($Value, [System.Globalization.CultureInfo]::InvariantCulture)
}

function Should-FlagPlaceholder {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    $markers = @('changeme','change-me','replace_with','replace-me','todo','example','sample','password')
    foreach ($marker in $markers) {
        if ($Value.ToLowerInvariant().Contains($marker)) { return $true }
    }
    return $false
}

function Get-OrderedKeys {
    param(
        [hashtable]$Schema,
        [string]$Target,
        [hashtable]$Variables
    )
    $ordered = @()
    if ($Schema.ContainsKey('order')) {
        $orderSection = $Schema.order
        if ($orderSection -is [hashtable] -and $orderSection.ContainsKey($Target)) {
            $ordered = @($orderSection[$Target])
        }
    }
    $tail = ($Variables.Keys | Where-Object { $ordered -notcontains $_ }) | Sort-Object
    return @($ordered + $tail)
}

function Wrap-Description {
    param([string]$Text)
    if (-not $Text) { return @() }
    $width = 110
    $words = $Text -split '\s+'
    $lines = @()
    $buffer = ''
    foreach ($word in $words) {
        if ($buffer.Length + $word.Length + 1 -gt $width) {
            if ($buffer) { $lines += $buffer.TrimEnd() }
            $buffer = ''
        }
        $buffer += "$word "
    }
    if ($buffer) { $lines += $buffer.TrimEnd() }
    return $lines
}

function Format-EnvLine {
    param([string]$Key, [string]$Value)
    if ($null -eq $Value) { $Value = "" }
    if ($Value -match '^[A-Za-z0-9_./:-]*$') {
        return "$Key=$Value"
    }
    $builder = New-Object System.Text.StringBuilder
    foreach ($ch in $Value.ToCharArray()) {
        switch ([int][char]$ch) {
            34 { [void]$builder.Append('"'); continue }
            92 { [void]$builder.Append('\'); continue }
            10 { [void]$builder.Append('\n'); continue }
            13 { continue }
            9  { [void]$builder.Append('\t'); continue }
        }
        [void]$builder.Append($ch)
    }
    return ('{0}="{1}"' -f $Key, $builder.ToString())
}

$schemaPath = Resolve-SchemaPath -PathOverride $SchemaPath
$schemaRaw = Get-Content -LiteralPath $schemaPath -Encoding UTF8 -Raw
$schema = ConvertFrom-Json -InputObject $schemaRaw -AsHashtable

if (-not $schema.ContainsKey('variables')) {
    throw "Schema '$schemaPath' missing 'variables' section"
}

if (-not $Profile) {
    $Profile = if ($Target -eq 'compose') { 'local' } else { 'ci' }
}

if (-not $OutputPath) {
    $OutputPath = if ($Target -eq 'compose') { '.env' } else { ".env.$Target" }
}

$variables = @{}
foreach ($pair in $schema.variables.GetEnumerator()) {
    $meta = $pair.Value
    $scopes = @()
    if ($meta -and $meta.ContainsKey('scopes')) {
        $scopes = @($meta.scopes | ForEach-Object { $_.ToString().ToLowerInvariant() })
    }
    if ($scopes -and ($scopes -contains $Target)) {
        $variables[$pair.Key] = $meta
    }
}

if (-not $variables.Count) {
    throw "No variables defined for target '$Target'"
}

$profileOverrides = @{}
if ($schema.ContainsKey('profiles')) {
    $profiles = $schema.profiles
    if ($profiles -is [hashtable] -and $profiles.ContainsKey($Profile)) {
        $profileOverrides = $profiles[$Profile]
    }
}

$secretOverrides = Read-JsonAggregate -Path $SecretsPath
$orderedKeys = Get-OrderedKeys -Schema $schema -Target $Target -Variables $variables

$result = [ordered]@{}
$errors = @()

foreach ($key in $orderedKeys) {
    $meta = $variables[$key]
    $value = $null
    if ($meta.ContainsKey('default')) { $value = $meta.default }
    if ($profileOverrides.ContainsKey($key)) { $value = $profileOverrides[$key] }
    if ($secretOverrides.ContainsKey($key)) { $value = $secretOverrides[$key] }

    $valueString = To-InvariantString -Value $value
    $isSecret = ($meta.ContainsKey('secret') -and [bool]$meta.secret)
    $isRequired = ($meta.ContainsKey('required') -and [bool]$meta.required)

    $allowEmpty = ($meta.ContainsKey('allowEmpty') -and [bool]$meta.allowEmpty)
    if ($isRequired -and -not $allowEmpty -and [string]::IsNullOrWhiteSpace($valueString)) {
        $errors += "Variable '$key' is required but missing"
    }

    if ($meta.ContainsKey('minLength') -and $valueString.Length -lt [int]$meta.minLength) {
        $errors += "Variable '$key' must be at least $($meta.minLength) characters"
    }

    if ($meta.ContainsKey('pattern') -and $valueString) {
        $pattern = $meta.pattern
        if (-not [System.Text.RegularExpressions.Regex]::IsMatch($valueString, $pattern)) {
            $errors += "Variable '$key' does not match pattern '$pattern'"
        }
    }

    if ($isSecret -and (Should-FlagPlaceholder -Value $valueString)) {
        $errors += "Variable '$key' looks like a placeholder"
    }

    $result[$key] = $valueString
}

if ($secretOverrides.Count) {
    $unknown = $secretOverrides.Keys | Where-Object { -not $variables.ContainsKey($_) }
    if ($unknown) {
        Write-Warning "Unknown keys in secrets payload: $($unknown -join ', ')"
    }
}

if ($errors.Count) {
    throw "Validation failed:`n - " + ($errors -join "`n - ")
}

if (-not $DryRun) {
    $resolvedOutput = if ([System.IO.Path]::IsPathRooted($OutputPath)) {
        $OutputPath
    } else {
        Join-Path -Path (Get-Location) -ChildPath $OutputPath
    }
    $directory = Split-Path -Path $resolvedOutput -Parent
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    if ((Test-Path -LiteralPath $resolvedOutput) -and -not $Force) {
        throw "File '$OutputPath' already exists. Use -Force to overwrite"
    }

    $builder = New-Object System.Text.StringBuilder
    if (-not $Quiet) {
        $timestamp = (Get-Date).ToString('s')
        [void]$builder.AppendLine("# Generated by infra/scripts/generate-env.ps1 at $timestamp")
        [void]$builder.AppendLine("# Target: $Target, Profile: $Profile")
        [void]$builder.AppendLine()
    }

    foreach ($key in $orderedKeys) {
        $meta = $variables[$key]
        $value = $result[$key]
        if (-not $Quiet -and $meta.ContainsKey('description')) {
            foreach ($line in (Wrap-Description -Text $meta.description)) {
                [void]$builder.AppendLine("# $line")
            }
        }
        [void]$builder.AppendLine((Format-EnvLine -Key $key -Value $value))
        if (-not $Quiet) { [void]$builder.AppendLine() }
    }

    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($resolvedOutput, $builder.ToString(), $utf8)
}

if (-not $Quiet) {
    foreach ($key in $orderedKeys) {
        $meta = $variables[$key]
        $value = $result[$key]
        $display = $value
        if ($meta.ContainsKey('secret') -and [bool]$meta.secret -and $value) {
            $display = '********'
        }
        Write-Output ("{0} = {1}" -f $key, $display)
    }
}

return $result


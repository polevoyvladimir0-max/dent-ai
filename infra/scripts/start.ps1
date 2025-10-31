param(
    [switch]$WithObservability,
    [switch]$Rebuild
)

$profileArgs = @()
if ($WithObservability) {
    $profileArgs += '--profile'
    $profileArgs += 'observability'
}

if ($Rebuild) {
    docker compose @profileArgs build --pull
}

docker compose @profileArgs up -d

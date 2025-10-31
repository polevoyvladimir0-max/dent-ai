param([switch]$WithObservability)

$profileArgs = @()
if ($WithObservability) {
    $profileArgs += '--profile'
    $profileArgs += 'observability'
}

docker compose @profileArgs down

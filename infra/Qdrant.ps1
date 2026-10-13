param(
    [ValidateSet('up', 'status', 'stop')]
    [string]$Action = 'up',
    [string]$WslDistribution = 'Ubuntu'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$composePath = Join-Path $PSScriptRoot 'docker-compose.yml'
$keepAlivePath = Join-Path $repoRoot '.tools\qdrant-wsl-keepalive.json'
$envPath = Join-Path $repoRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    $envPath = Join-Path $repoRoot '.env.example'
}

function Get-ManagedKeepAlive {
    if (-not (Test-Path -LiteralPath $keepAlivePath)) { return $null }
    try {
        $record = Get-Content -LiteralPath $keepAlivePath -Raw | ConvertFrom-Json
        $process = Get-Process -Id $record.pid -ErrorAction Stop
        if ($process.ProcessName -eq 'wsl' -and
            $process.StartTime.ToUniversalTime().Ticks.ToString() -eq $record.startedAtTicks -and
            $record.distribution -eq $WslDistribution) {
            return $process
        }
    }
    catch { return $null }
    return $null
}

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCommand) {
    $composeArguments = @('compose', '--env-file', $envPath, '-f', $composePath)
    switch ($Action) {
        'up' { & $dockerCommand.Source @composeArguments up -d --wait --wait-timeout 90 qdrant }
        'status' { & $dockerCommand.Source @composeArguments ps qdrant }
        'stop' { & $dockerCommand.Source @composeArguments stop qdrant }
    }
}
else {
    if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
        throw 'Install Docker with Compose, or Docker in a WSL Ubuntu distribution.'
    }
    if ($Action -eq 'up' -and -not (Get-ManagedKeepAlive)) {
        # systemd services alone do not keep an idle WSL instance running.
        $keepAlive = Start-Process -FilePath 'wsl.exe' -WindowStyle Hidden -PassThru `
            -ArgumentList @('-d', $WslDistribution, '-u', 'root', '--exec', 'sleep', 'infinity')
        New-Item -ItemType Directory -Path (Split-Path -Parent $keepAlivePath) -Force | Out-Null
        @{
            pid = $keepAlive.Id
            startedAtTicks = $keepAlive.StartTime.ToUniversalTime().Ticks.ToString()
            distribution = $WslDistribution
        } | ConvertTo-Json | Set-Content -LiteralPath $keepAlivePath -Encoding UTF8
    }
    $wslComposePath = (& wsl -d $WslDistribution -u root --exec wslpath -u $composePath.Replace('\', '/'))
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the Compose path in WSL.' }
    $wslEnvPath = (& wsl -d $WslDistribution -u root --exec wslpath -u $envPath.Replace('\', '/'))
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the environment file in WSL.' }
    $composeArguments = @(
        'compose', '--env-file', $wslEnvPath.Trim(), '-f', $wslComposePath.Trim()
    )
    if ($Action -eq 'up') {
        & wsl -d $WslDistribution -u root --exec systemctl start docker
        if ($LASTEXITCODE -ne 0) {
            throw 'Docker is not available in WSL. See README for installation steps.'
        }
    }
    switch ($Action) {
        'up' { & wsl -d $WslDistribution -u root --exec docker @composeArguments up -d --wait --wait-timeout 90 qdrant }
        'status' { & wsl -d $WslDistribution -u root --exec docker @composeArguments ps qdrant }
        'stop' { & wsl -d $WslDistribution -u root --exec docker @composeArguments stop qdrant }
    }
}

if ($LASTEXITCODE -ne 0) {
    throw 'Qdrant Compose command failed. Check Docker status and network access.'
}

if ($Action -eq 'stop' -and -not $dockerCommand) {
    $keepAlive = Get-ManagedKeepAlive
    if ($keepAlive) {
        Stop-Process -InputObject $keepAlive
        Remove-Item -LiteralPath $keepAlivePath
    }
}

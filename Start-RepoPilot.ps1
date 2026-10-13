param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$apiDir = Join-Path $repoRoot 'services\api'
$webDir = Join-Path $repoRoot 'apps\web'
$logDir = Join-Path $repoRoot 'output\startup'

function Test-Url {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Wait-ForUrl {
    param(
        [string]$Name,
        [string]$Url,
        [int]$TimeoutSeconds = 90
    )

    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        if (Test-Url $Url) {
            Write-Host "$Name ready: $Url"
            return
        }
        Start-Sleep -Milliseconds 750
    }
    throw "$Name did not become ready in $TimeoutSeconds seconds. Check logs in $logDir"
}

function Start-LoggedProcess {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $stdout = Join-Path $logDir "$Name-$stamp.stdout.log"
    $stderr = Join-Path $logDir "$Name-$stamp.stderr.log"
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    Write-Host "$Name starting (PID $($process.Id)); logs: $logDir"
}

$envFile = Join-Path $repoRoot '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $repoRoot '.env.example') -Destination $envFile
    Write-Host 'Created .env from .env.example.'
}

$settings = @{}
foreach ($line in Get-Content -LiteralPath $envFile) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $settings[$Matches[1]] = $Matches[2].Trim().Trim('"', "'")
    }
}

$provider = if ($env:LLM_PROVIDER) { $env:LLM_PROVIDER } else { $settings['LLM_PROVIDER'] }
$baseUrl = if ($env:LLM_BASE_URL) { $env:LLM_BASE_URL } else { $settings['LLM_BASE_URL'] }
$usesLocalOllama = $provider -eq 'openai_compatible' -and
    $baseUrl -match '^https?://(127\.0\.0\.1|localhost):11434(?:/|$)'

if ($usesLocalOllama) {
    $ollamaUrl = 'http://127.0.0.1:11434/api/tags'
    if (-not (Test-Url $ollamaUrl)) {
        $ollamaCommand = Get-Command ollama.exe -ErrorAction SilentlyContinue
        $ollamaExe = if ($ollamaCommand) { $ollamaCommand.Source } else { 'E:\RepoPilot\Ollama\ollama.exe' }
        if (-not (Test-Path -LiteralPath $ollamaExe)) {
            throw 'Ollama was not found. Install it or configure LLM_BASE_URL in .env.'
        }
        if (-not $env:OLLAMA_MODELS -and (Test-Path -LiteralPath 'E:\RepoPilot\OllamaModels')) {
            $env:OLLAMA_MODELS = 'E:\RepoPilot\OllamaModels'
        }
        Start-LoggedProcess -Name 'ollama' -Executable $ollamaExe -Arguments @('serve') `
            -WorkingDirectory (Split-Path -Parent $ollamaExe)
    }
    Wait-ForUrl -Name 'Ollama' -Url $ollamaUrl
}

$apiUrl = 'http://127.0.0.1:8000/health'
if (-not (Test-Url $apiUrl)) {
    $pythonExe = Join-Path $apiDir '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        throw 'Backend environment is missing. In services/api run: uv sync --dev'
    }
    Start-LoggedProcess -Name 'api' -Executable $pythonExe `
        -Arguments @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--reload') `
        -WorkingDirectory $apiDir
}

$webUrl = 'http://localhost:5173/'
$webProbeUrl = 'http://127.0.0.1:5173/'
if (-not (Test-Url $webProbeUrl)) {
    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    $viteScript = Join-Path $webDir 'node_modules\vite\bin\vite.js'
    if (-not $nodeCommand) {
        throw 'Node.js was not found. Install Node.js 20.19 or newer.'
    }
    if (-not (Test-Path -LiteralPath $viteScript)) {
        throw 'Frontend dependencies are missing. In apps/web run: npm install'
    }
    Start-LoggedProcess -Name 'web' -Executable $nodeCommand.Source `
        -Arguments @($viteScript, '--host', '127.0.0.1', '--port', '5173', '--strictPort') `
        -WorkingDirectory $webDir
}

Wait-ForUrl -Name 'API' -Url $apiUrl
Wait-ForUrl -Name 'Web' -Url $webProbeUrl

if (-not $NoBrowser) {
    Start-Process $webUrl
}
Write-Host 'RepoPilot is ready.'

#requires -Version 5.1

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (!(Test-Path (Join-Path $projectRoot ".env"))) {
    throw "Missing .env. Follow the README setup instructions before starting the project."
}

function Get-LocalSetting {
    param(
        [string]$Name,
        [string]$DefaultValue
    )

    $processValue = [Environment]::GetEnvironmentVariable($Name)
    if (![string]::IsNullOrWhiteSpace($processValue)) {
        return $processValue
    }

    $pattern = "^\s*$([regex]::Escape($Name))\s*=\s*(.*?)\s*$"
    foreach ($line in Get-Content -LiteralPath (Join-Path $projectRoot ".env")) {
        if ($line -match $pattern) {
            $value = $Matches[1].Trim()
            if (
                $value.Length -ge 2 -and
                (
                    ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                    ($value.StartsWith("'") -and $value.EndsWith("'"))
                )
            ) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }

    return $DefaultValue
}

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect($HostName, $Port, $null, $null)
        if (!$pending.AsyncWaitHandle.WaitOne(1500, $false)) {
            return $false
        }
        $client.EndConnect($pending)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Wait-ForTcpPort {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -HostName $HostName -Port $Port) {
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return (Test-TcpPort -HostName $HostName -Port $Port)
}

$neo4jUri = [Uri](Get-LocalSetting "NEO4J_URI" "bolt://127.0.0.1:7687")
$neo4jHost = $neo4jUri.DnsSafeHost
$neo4jPort = $neo4jUri.Port
if ($neo4jPort -lt 0) {
    $neo4jPort = 7687
}

if (!(Test-TcpPort -HostName $neo4jHost -Port $neo4jPort)) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (!$docker) {
        throw "Neo4j is not reachable and Docker is unavailable. Start the Neo4j DBMS and check NEO4J_URI in .env."
    }

    $null = & $docker.Source info --format '{{.ServerVersion}}' 2>$null
    $dockerReady = $LASTEXITCODE -eq 0
    if (!$dockerReady) {
        $dockerDesktopCandidates = @(
            (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Docker Desktop\Docker Desktop.exe")
        )
        $dockerDesktop = $dockerDesktopCandidates |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1

        if (!$dockerDesktop) {
            throw "Docker Desktop was not found. Start Neo4j in Neo4j Desktop or install Docker Desktop."
        }

        if (!(Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
            Write-Host "Starting Docker Desktop..."
            Start-Process -FilePath $dockerDesktop
        }

        $deadline = (Get-Date).AddSeconds(180)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 3
            $null = & $docker.Source info --format '{{.ServerVersion}}' 2>$null
            if ($LASTEXITCODE -eq 0) {
                $dockerReady = $true
                break
            }
        }
        if (!$dockerReady) {
            throw "Docker Engine did not become ready. Start Docker Desktop and retry."
        }
    }

    $containers = @(
        & $docker.Source ps -a `
            --filter "name=simplegraphrag-neo4j" `
            --format "{{.Names}}" 2>$null
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Could not query Docker for the Neo4j container."
    }
    if ($containers -notcontains "simplegraphrag-neo4j") {
        throw "Neo4j is not reachable and the simplegraphrag-neo4j container does not exist. Follow the README Neo4j setup instructions."
    }

    Write-Host "Starting Neo4j..."
    & $docker.Source start simplegraphrag-neo4j | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not start the simplegraphrag-neo4j container."
    }
    if (!(Wait-ForTcpPort -HostName $neo4jHost -Port $neo4jPort -TimeoutSeconds 90)) {
        throw "Neo4j did not open $($neo4jUri.Host):$neo4jPort. Check the container logs and NEO4J_URI."
    }
}

$ollamaBaseUrl = Get-LocalSetting "OLLAMA_BASE_URL" "http://127.0.0.1:11434"
$ollamaModel = Get-LocalSetting "OLLAMA_MODEL" "qwen3:4b"
$ollamaUri = [Uri]$ollamaBaseUrl
$ollamaHost = $ollamaUri.DnsSafeHost
if ($ollamaHost -eq "::1") {
    $ollamaHost = "[::1]"
}
$ollamaPort = $ollamaUri.Port
if ($ollamaUri.IsDefaultPort) {
    $ollamaPort = 11434
}
if ([string]::IsNullOrWhiteSpace($env:OLLAMA_HOST)) {
    $env:OLLAMA_HOST = "$ollamaHost`:$ollamaPort"
}

if (!(Test-TcpPort -HostName $ollamaUri.DnsSafeHost -Port $ollamaPort)) {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (!$ollama) {
        throw "Ollama is not reachable and the ollama command was not found. Install Ollama and retry."
    }

    if (!(Get-Process -Name "ollama" -ErrorAction SilentlyContinue)) {
        Write-Host "Starting Ollama..."
        Start-Process -FilePath $ollama.Source -ArgumentList "serve" -WindowStyle Hidden
    }

    if (!(Wait-ForTcpPort -HostName $ollamaUri.DnsSafeHost -Port $ollamaPort -TimeoutSeconds 60)) {
        throw "Ollama did not become available at $ollamaBaseUrl."
    }
}

$ollamaTags = Invoke-RestMethod `
    -Uri "$($ollamaBaseUrl.TrimEnd('/'))/api/tags" `
    -TimeoutSec 10
$installedModels = @($ollamaTags.models | ForEach-Object { [string]$_.name })
if ($installedModels -notcontains $ollamaModel) {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (!$ollama) {
        throw "The Ollama model '$ollamaModel' is missing and the ollama command was not found."
    }
    Write-Host "Downloading the configured Ollama model '$ollamaModel'..."
    & $ollama.Source pull $ollamaModel
    if ($LASTEXITCODE -ne 0) {
        throw "Could not download the Ollama model '$ollamaModel'."
    }
}

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (!(Test-Path -LiteralPath $python)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (!$pythonCommand) {
        throw "Python was not found. Install Python and follow the README setup instructions."
    }
    $python = $pythonCommand.Source
}

$healthUrl = "http://127.0.0.1:8501/_stcore/health"
if (Test-TcpPort -HostName "127.0.0.1" -Port 8501) {
    $health = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
    if ($health.StatusCode -eq 200) {
        Write-Host "SimpleGraphRAG is already running at http://127.0.0.1:8501"
        return
    }
    throw "Port 8501 is in use, but SimpleGraphRAG is not healthy there."
}

Write-Host "Starting SimpleGraphRAG..."
& $python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
if ($LASTEXITCODE -ne 0) {
    throw "Streamlit exited with an error."
}

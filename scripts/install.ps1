[CmdletBinding()]
param(
    [string] $Python,
    [string] $FFmpeg = 'ffmpeg',
    [string] $FFprobe = 'ffprobe',
    [string] $Codex = 'codex',
    [string] $WhisperCpu,
    [string] $WhisperVulkan,
    [string] $WhisperModel,
    [string] $DataDir,
    [string] $ScriptsDir = (Join-Path $env:APPDATA 'Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility')
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $workspaceRoot '.runtime'
$whisperRelease = 'v1.9.2'

function Get-ToolPath([string] $Command) {
    if (Test-Path -LiteralPath $Command -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Command).Path
    }
    return (Get-Command -Name $Command -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}

function Save-VerifiedDownload([string] $Url, [string] $Destination, [string] $Sha256) {
    if ((Test-Path -LiteralPath $Destination -PathType Leaf) -and
        (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $Sha256) {
        return
    }
    $temporary = "$Destination.download"
    & curl.exe --fail --location --silent --show-error --retry 2 --output $temporary $Url
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed: $Url"
    }
    if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne $Sha256) {
        throw "Unexpected SHA-256: $temporary"
    }
    Move-Item -LiteralPath $temporary -Destination $Destination -Force
}

if (-not $Python) {
    $uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($uv) {
        $env:UV_CACHE_DIR = Join-Path $workspaceRoot '.cache\uv'
        $foundPython = & $uv.Source python find 3.13 2>$null
        if ($LASTEXITCODE -eq 0) {
            $Python = $foundPython
        }
    }
    if (-not $Python) {
        $Python = 'python'
    }
}
$Python = Get-ToolPath $Python
& $Python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.10+ is required. Pass its absolute path with -Python.'
}
$FFmpeg = Get-ToolPath $FFmpeg
$FFprobe = Get-ToolPath $FFprobe
$Codex = Get-ToolPath $Codex
if ($WhisperVulkan) {
    $WhisperVulkan = Get-ToolPath $WhisperVulkan
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
if (-not $WhisperCpu) {
    $cpuDir = Join-Path $runtimeRoot 'whisper-cpu'
    $WhisperCpu = Join-Path $cpuDir 'Release\whisper-cli.exe'
    if (-not (Test-Path -LiteralPath $WhisperCpu -PathType Leaf)) {
        $zip = Join-Path $runtimeRoot 'whisper-bin-x64.zip'
        $url = "https://github.com/ggml-org/whisper.cpp/releases/download/$whisperRelease/whisper-bin-x64.zip"
        Save-VerifiedDownload $url $zip '49dcc16de826f20bd53d44f947a1ae49dfa81f86cad67a64d80820cb192d674a'
        Expand-Archive -LiteralPath $zip -DestinationPath $cpuDir -Force
    }
}
$WhisperCpu = Get-ToolPath $WhisperCpu
if (-not $WhisperModel) {
    $modelName = 'ggml-large-v3-turbo-q5_0.bin'
    $WhisperModel = Join-Path $runtimeRoot $modelName
    $url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$modelName"
    Save-VerifiedDownload $url $WhisperModel '394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2'
}
$WhisperModel = (Resolve-Path -LiteralPath $WhisperModel).Path
if (-not $DataDir) {
    $DataDir = Join-Path $runtimeRoot 'data'
}
$DataDir = (New-Item -ItemType Directory -Path $DataDir -Force).FullName

$configFile = Join-Path $runtimeRoot 'settings.json'
$configuration = [ordered]@{
    data_dir = $DataDir
    python = $Python
    ffmpeg = $FFmpeg
    ffprobe = $FFprobe
    codex = $Codex
    whisper_cpu = $WhisperCpu
    whisper_vulkan = $WhisperVulkan
    whisper_model = $WhisperModel
}
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($configFile, ($configuration | ConvertTo-Json), $utf8)
$directory = (New-Item -ItemType Directory -Path $ScriptsDir -Force).FullName
$menuScript = Join-Path $directory 'Davinci Semantic Search.py'
$entry = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'resolve_entry.py'), $utf8)
$script = @(
    '# -*- coding: utf-8 -*-',
    ('WORKSPACE_ROOT = ' + (ConvertTo-Json -Compress -InputObject $workspaceRoot)),
    ('CONFIG_FILE = ' + (ConvertTo-Json -Compress -InputObject $configFile)),
    '',
    $entry
) -join [Environment]::NewLine
[IO.File]::WriteAllText($menuScript, $script, $utf8)
Write-Output "Resolve script: $menuScript"
Write-Output "Index and logs: $DataDir"

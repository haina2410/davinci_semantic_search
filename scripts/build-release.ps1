[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:beta|preview)\.[0-9]+)?$')]
    [string] $Version,
    [ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:beta|preview)\.[0-9]+)?$')]
    [string] $PreviousVersion,
    [string] $OutputDir
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
if (-not $OutputDir) { $OutputDir = Join-Path $repo ".cache/releases/$Version" }
$directory = (New-Item -ItemType Directory -Path $OutputDir -Force).FullName
$tag = "refs/tags/$Version"
$installerName = "davinci-semantic-search-$Version-windows-x64-setup.exe"
$installer = Join-Path $directory $installerName
$staging = Join-Path $directory ".setup-$([guid]::NewGuid().ToString('N'))"
[IO.Directory]::CreateDirectory($staging) | Out-Null
$archive = Join-Path $staging 'payload.zip'
try {
    & git -C $repo archive --format=zip "--output=$archive" $tag -- README.md davinci_search scripts/install.ps1 scripts/uninstall.ps1 scripts/uninstall-prompt.ps1 scripts/installation.ps1 scripts/resolve_entry.py
    if ($LASTEXITCODE -ne 0) { throw "Could not package tag $Version." }
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
    $package = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Update)
    try {
        $writer = [IO.StreamWriter]::new($package.CreateEntry('version.txt').Open())
        try { $writer.WriteLine($Version) } finally { $writer.Dispose() }
    }
    finally { $package.Dispose() }

    $launcher = Join-Path $staging 'setup.cs'
    $source = & git -C $repo show "${tag}:scripts/setup.cs"
    if ($LASTEXITCODE -ne 0) { throw "Setup launcher is missing from tag $Version." }
    [IO.File]::WriteAllLines($launcher, [string[]]$source)
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    $output = Join-Path $staging $installerName
    & $compiler /nologo /target:exe /platform:x64 /optimize+ /reference:System.IO.Compression.FileSystem.dll "/resource:$archive,payload.zip" "/out:$output" $launcher
    if ($LASTEXITCODE -ne 0) { throw 'Could not compile the Windows setup program.' }
    Move-Item -LiteralPath $output -Destination $installer -Force
}
finally {
    if ((Split-Path -Parent $staging) -eq $directory -and (Split-Path -Leaf $staging) -match '^\.setup-[0-9a-f]{32}$') {
        [IO.Directory]::Delete($staging, $true)
    }
}
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
$hashFile = "$installer.sha256"
Set-Content -LiteralPath $hashFile -Value "$hash  $installerName" -Encoding Ascii

$range = if ($PreviousVersion) { "refs/tags/$PreviousVersion..$tag" } else { $tag }
$changes = & git -C $repo log --no-merges $range '--format=- %s (%h)'
if ($LASTEXITCODE -ne 0) { throw 'Could not generate release notes.' }
$notes = @(
    "# Davinci Semantic Search $Version"
    ''
    'Download and double-click the Windows x64 setup EXE. Reopen Resolve and choose Workspace > Scripts > Davinci Semantic Search.'
    'Setup checks Python first and downloads missing dependencies. CPU is the default.'
    ''
    '## Changes'
    ''
    $changes
) -join "`n"
$notesFile = Join-Path $directory 'release-notes.md'
[IO.File]::WriteAllText($notesFile, $notes, [Text.UTF8Encoding]::new($false))

[pscustomobject]@{ Installer = $installer; Checksum = $hashFile; Notes = $notesFile }

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
$archiveName = "davinci-semantic-search-$Version-windows-x64.zip"
$archive = Join-Path $directory $archiveName

& git -C $repo archive --format=zip "--output=$archive" "--prefix=davinci-semantic-search-$Version/" $tag -- README.md davinci_search scripts/install.ps1 scripts/uninstall.ps1 scripts/installation.ps1 scripts/resolve_entry.py
if ($LASTEXITCODE -ne 0) { throw "Could not package tag $Version." }
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$package = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Update)
try {
    $entry = $package.CreateEntry("davinci-semantic-search-$Version/version.txt")
    $writer = [IO.StreamWriter]::new($entry.Open())
    try { $writer.WriteLine($Version) } finally { $writer.Dispose() }
}
finally { $package.Dispose() }
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
$hashFile = "$archive.sha256"
Set-Content -LiteralPath $hashFile -Value "$hash  $archiveName" -Encoding Ascii

$range = if ($PreviousVersion) { "refs/tags/$PreviousVersion..$tag" } else { $tag }
$changes = & git -C $repo log --no-merges $range '--format=- %s (%h)'
if ($LASTEXITCODE -ne 0) { throw 'Could not generate release notes.' }
$notes = @(
    "# Davinci Semantic Search $Version"
    ''
    'Extract the Windows x64 ZIP and follow its README to install, update, or uninstall.'
    'CPU is the default. The optional Vulkan package is verified by the installer.'
    ''
    '## Changes'
    ''
    $changes
) -join "`n"
$notesFile = Join-Path $directory 'release-notes.md'
[IO.File]::WriteAllText($notesFile, $notes, [Text.UTF8Encoding]::new($false))

[pscustomobject]@{ Archive = $archive; Checksum = $hashFile; Notes = $notesFile }

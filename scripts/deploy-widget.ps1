# deploy-widget.ps1
# Builds the Angular web component and copies dist files to the API's static/widget/ folder.
# Usage: .\scripts\deploy-widget.ps1
#   -SkipBuild   Skip the Angular build (use existing dist)
#   -ApiDir      Path to the API project (default: auto-detect sibling)

param(
    [switch]$SkipBuild,
    [string]$ApiDir = ""
)

$ErrorActionPreference = "Stop"

# Resolve paths
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiRoot = if ($ApiDir) { $ApiDir } else { Split-Path -Parent $ScriptDir }
$WidgetRoot = Join-Path (Split-Path -Parent $ApiRoot) "metadata-agent-fuerAPI"
$WidgetDist = Join-Path $WidgetRoot "dist"
$TargetDir = Join-Path (Join-Path (Join-Path (Join-Path $ApiRoot "src") "static") "widget") "dist"
$TargetAssets = Join-Path (Join-Path (Join-Path (Join-Path $ApiRoot "src") "static") "widget") "assets"

Write-Host "=== Deploy Widget ===" -ForegroundColor Cyan
Write-Host "  Widget source: $WidgetRoot"
Write-Host "  Target dir:    $TargetDir"

# Step 1: Build Angular (unless skipped)
if (-not $SkipBuild) {
    Write-Host "`n[1/3] Building Angular web component..." -ForegroundColor Yellow
    Push-Location $WidgetRoot
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Angular build failed" }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "`n[1/3] Skipping build (using existing dist)" -ForegroundColor DarkGray
}

# Step 2: Verify dist exists
if (-not (Test-Path $WidgetDist)) {
    Write-Error "Widget dist folder not found: $WidgetDist. Run build first."
    exit 1
}

# Step 3: Copy files
Write-Host "`n[2/3] Copying dist files..." -ForegroundColor Yellow

# Clean target
if (Test-Path $TargetDir) { Remove-Item -Recurse -Force $TargetDir }
New-Item -ItemType Directory -Force $TargetDir | Out-Null

# Copy JS and CSS files (skip index.html, examples, 3rdpartylicenses)
Get-ChildItem $WidgetDist -File | Where-Object {
    $_.Extension -in '.js', '.css'
} | ForEach-Object {
    # Strip hash from filename for stable URLs: main.abc123.js → main.js
    $stableName = $_.Name -replace '\.[a-f0-9]{16,20}\.', '.'
    Copy-Item $_.FullName (Join-Path $TargetDir $stableName)
    Write-Host "  $($_.Name) → $stableName"
}

# Copy i18n assets
$i18nSource = Join-Path (Join-Path $WidgetDist "assets") "i18n"
$i18nTarget = Join-Path $TargetAssets "i18n"
if (Test-Path $i18nSource) {
    if (Test-Path $i18nTarget) { Remove-Item -Recurse -Force $i18nTarget }
    Copy-Item -Recurse $i18nSource $i18nTarget
    Write-Host "  assets/i18n/ → copied"
}


# Step 3: Create gzip pre-compressed versions
Write-Host "`n[3/4] Creating gzip pre-compressed versions..." -ForegroundColor Yellow
Get-ChildItem $TargetDir -File | Where-Object { $_.Extension -in '.js', '.css' } | ForEach-Object {
    $gzPath = $_.FullName + ".gz"
    $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
    $ms = New-Object System.IO.MemoryStream
    $gz = New-Object System.IO.Compression.GZipStream($ms, [System.IO.Compression.CompressionLevel]::Optimal)
    $gz.Write($bytes, 0, $bytes.Length)
    $gz.Close()
    [System.IO.File]::WriteAllBytes($gzPath, $ms.ToArray())
    $ms.Close()
    $ratio = [math]::Round(100 - ($ms.ToArray().Length / $bytes.Length * 100))
    Write-Host "  $($_.Name).gz ($ratio% smaller)"
}

# Step 4: Summary
Write-Host "`n[4/4] Done!" -ForegroundColor Green
$totalSize = (Get-ChildItem $TargetDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host "  Total size: $([math]::Round($totalSize / 1KB)) KB ($([math]::Round($totalSize / 1MB, 2)) MB)"
Write-Host "  Files:"
Get-ChildItem $TargetDir -File | ForEach-Object {
    Write-Host "    $($_.Name) ($([math]::Round($_.Length / 1KB)) KB)"
}

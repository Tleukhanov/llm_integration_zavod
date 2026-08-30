<#
run_clip.ps1 - Windows launcher for the shorts-clipper pipeline.

Directs output/models to the D: drive (this machine's C: is nearly full)
and invokes the bundled venv python. No external module dependencies.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Url,
    [string]$OutputDir = "D:\shorts_output",
    [string]$ModelsDir = "D:\shorts_models",
    [int]$Count = 1,
    [string]$EnvFile = ".env",
    [switch]$Upload
)

$ErrorActionPreference = "Stop"

$Python = "D:\Projects\shorts-clipper\.venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $ModelsDir)) {
    New-Item -ItemType Directory -Path $ModelsDir -Force | Out-Null
}

$env:SHORTS_OUTPUT_DIR = $OutputDir
$env:SHORTS_MODELS_DIR = $ModelsDir

$cmd = @($Python, "-m", "shorts_clipper", "--env", $EnvFile, "clip", $Url, "-c", "$Count")
if ($Upload) {
    $cmd += "--upload"
}

Write-Host "Output dir: $OutputDir"
Write-Host "Models dir: $ModelsDir"
Write-Host "Python:     $Python"
Write-Host "Command:    $($cmd -join ' ')"

& $cmd
exit $LASTEXITCODE
# First run (needs internet): creates .venv, installs packages, downloads the model.
# Later runs: no internet needed; goes straight to processing.
# Any arguments are passed to process_pdfs.py, e.g.  .\run.ps1 C:\notebooks C:\annotated
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment (.venv)..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment. Is Python 3.13+ installed?" }
}

# Reinstall packages only when requirements.txt has changed.
$reqHash = (Get-FileHash requirements.txt -Algorithm SHA256).Hash
$marker = Join-Path $PSScriptRoot ".venv\requirements.sha256"
if (-not (Test-Path $marker) -or (Get-Content $marker -Raw).Trim() -ne $reqHash) {
    Write-Host "Installing Python packages..."
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Package installation failed." }
    Set-Content $marker $reqHash
}

& $venvPython download_model.py
if ($LASTEXITCODE -ne 0) { throw "Model download failed." }

& $venvPython process_pdfs.py @args

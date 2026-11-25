# One-time setup (needs internet). Safe to re-run: it only does work when something changed.
#   1. Detects the GPU and picks a matching PyTorch build (NVIDIA CUDA, AMD ROCm, or CPU)
#   2. Creates .venv with a compatible Python and installs packages
#   3. Downloads the Chandra OCR 2 model
#
#   .\setup.ps1                 # auto-detect
#   .\setup.ps1 -Backend cpu    # force a backend (remembered for later runs)
param(
    [ValidateSet("auto", "cuda", "rocm", "cpu")]
    [string]$Backend = "auto"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvDir = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$stateFile = Join-Path $venvDir "setup-state.txt"

# --- PyTorch builds per backend ----------------------------------------------
# NVIDIA: CUDA 13.0 wheels (supports RTX 50-series / Blackwell; needs driver 580+).
# AMD:    AMD's ROCm wheels for Windows, built per GPU family. Python <= 3.13 only.
# CPU:    standard PyPI wheels.
$torchSpecs = @{
    cuda = @{ Index = "https://download.pytorch.org/whl/cu130"; Packages = @("torch>=2.14,<2.15", "torchvision>=0.29,<0.30"); Pythons = @("3.14", "3.13", "3.12") }
    cpu  = @{ Index = "https://pypi.org/simple";                 Packages = @("torch>=2.14,<2.15", "torchvision>=0.29,<0.30"); Pythons = @("3.14", "3.13", "3.12") }
    rocm = @{ Index = $null;                                     Packages = @("torch==2.11.0+rocm7.13.0", "torchvision==0.26.0+rocm7.13.0"); Pythons = @("3.13", "3.12") }
}
# AMD wheel index per GPU family (RX 7000 = gfx110X, RX 9000 = gfx120X).
$rocmIndexes = [ordered]@{
    'Radeon.*RX\s*9\d{3}' = "https://repo.amd.com/rocm/whl/gfx120X-all/"
    'Radeon.*RX\s*7\d{3}' = "https://repo.amd.com/rocm/whl/gfx110X-all/"
}

function Get-GpuNames { (Get-CimInstance Win32_VideoController).Name }

function Get-RocmIndex {
    foreach ($name in Get-GpuNames) {
        foreach ($pattern in $rocmIndexes.Keys) {
            if ($name -match $pattern) { return $rocmIndexes[$pattern] }
        }
    }
    return $null
}

# --- Choose backend ------------------------------------------------------------
$previous = if (Test-Path $stateFile) { (Get-Content $stateFile -Raw).Trim() } else { "" }
$previousBackend = ($previous -split "\|")[0]

if ($Backend -eq "auto") {
    if ($previousBackend) {
        $Backend = $previousBackend   # keep whatever was chosen/forced last time
    } elseif (Get-GpuNames | Where-Object { $_ -match "NVIDIA" }) {
        $Backend = "cuda"
    } elseif (Get-RocmIndex) {
        $Backend = "rocm"
    } else {
        $Backend = "cpu"
    }
}
$spec = $torchSpecs[$Backend]
if ($Backend -eq "rocm") {
    $spec.Index = Get-RocmIndex
    if (-not $spec.Index) { throw "No supported AMD Radeon RX 7000/9000 GPU found. Use -Backend cpu." }
}
Write-Host "PyTorch backend: $Backend"

# --- Virtual environment --------------------------------------------------------
$reqHash = (Get-FileHash requirements.txt -Algorithm SHA256).Hash
$state = "$Backend|$($spec.Index)|$($spec.Packages -join ',')|$reqHash"

if ((Test-Path $venvPython) -and $previousBackend -and $previousBackend -ne $Backend) {
    Write-Host "Backend changed ($previousBackend -> $Backend); recreating .venv..."
    Remove-Item -Recurse -Force $venvDir
}

if (-not (Test-Path $venvPython)) {
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in $spec.Pythons) {
            & py "-$v" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Creating .venv with Python $v..."
                & py "-$v" -m venv $venvDir
                $created = ($LASTEXITCODE -eq 0)
                break
            }
        }
    }
    if (-not $created -and (Get-Command uv -ErrorAction SilentlyContinue)) {
        $newest = [version]$spec.Pythons[0]
        $range = ">=$($spec.Pythons[-1]),<$($newest.Major).$($newest.Minor + 1)"
        Write-Host "Creating .venv with uv (Python $range)..."
        & uv venv --seed --python $range $venvDir
        $created = ($LASTEXITCODE -eq 0)
    }
    if (-not $created) {
        throw "Need Python $($spec.Pythons -join ' / ') for the '$Backend' backend. Install one from python.org and re-run."
    }
}

# --- Packages ------------------------------------------------------------------
if ($previous -ne $state) {
    Write-Host "Installing PyTorch ($Backend)..."
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install --index-url $spec.Index @($spec.Packages)
    if ($LASTEXITCODE -ne 0) { throw "PyTorch installation failed." }

    Write-Host "Installing other packages..."
    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Package installation failed." }

    Set-Content $stateFile $state
}

# --- Model -----------------------------------------------------------------------
& $venvPython download_model.py
if ($LASTEXITCODE -ne 0) { throw "Model download failed." }

Write-Host "Setup complete."

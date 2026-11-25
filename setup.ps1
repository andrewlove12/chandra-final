# Setup (needs internet). The installer runs this; it's safe to re-run and only
# does work when something changed.
#   1. Picks the PyTorch build: NVIDIA (CUDA), AMD (ROCm) or the standard build (CPU)
#   2. Downloads a pinned uv, which installs the project's exact Python version
#      into .\python (never uses or changes any Python already on the PC)
#   3. Creates .venv from that Python and installs the packages
#   4. Downloads the OCR models
#
#   .\setup.ps1 -Backend cuda|rocm|cpu   # what the installer passes
#   .\setup.ps1                          # auto: previous choice, else detect the GPU
#
# Everything is logged to logs\setup.log. Exit code 0 = success, 1 = failure.
param(
    [ValidateSet("auto", "cuda", "rocm", "cpu")]
    [string]$Backend = "auto"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Windows PowerShell 5.1 started by a program that was itself started from
# PowerShell 7 (e.g. the installer launched from a pwsh window) inherits
# PowerShell 7's module folders, loads the wrong modules and loses commands
# such as Get-FileHash. Drop those folders for this process.
if ($PSVersionTable.PSEdition -eq "Desktop") {
    $env:PSModulePath = ($env:PSModulePath -split ";" |
        Where-Object { $_ -and $_ -notmatch "\\PowerShell\\(7[^\\]*\\)?Modules\\?$" }) -join ";"
}

function Get-Sha256([string]$Path) {
    # .NET directly: works regardless of which PowerShell modules load.
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream)) -replace "-", "")
    } finally { $stream.Dispose() }
}

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
try { Start-Transcript -Path (Join-Path $logDir "setup.log") -Append | Out-Null } catch { }

trap {
    Write-Host ""
    Write-Host "SETUP FAILED: $_" -ForegroundColor Red
    Write-Host "Details are in $(Join-Path $logDir 'setup.log')"
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

# Pinned uv release (https://github.com/astral-sh/uv); it downloads the Python builds.
$uvVersion = "0.13.9"
$uvSha256 = "EDD61F7F5728A56CC62455386661860BDFC7BAB65872F39B0C25BDCC1D8E1BEB"
$uvDir = Join-Path $PSScriptRoot "tools\uv"

function Get-PortableUv {
    $uvExe = Join-Path $uvDir "uv.exe"
    if (Test-Path $uvExe) { return $uvExe }
    New-Item -ItemType Directory -Force $uvDir | Out-Null
    $zip = Join-Path $uvDir "uv.zip"
    $url = "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip"
    Write-Host "Downloading uv $uvVersion..."
    $ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is very slow with its progress bar
    Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
    $hash = Get-Sha256 $zip
    if ($hash -ne $uvSha256) {
        Remove-Item $zip -Force
        throw "uv download failed its checksum (got $hash). Try again later."
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $uvDir)
    Remove-Item $zip -Force
    return $uvExe
}

$venvDir = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$stateFile = Join-Path $venvDir "setup-state.txt"

# --- PyTorch builds per backend ----------------------------------------------
# NVIDIA: CUDA 13.0 wheels (supports RTX 50-series / Blackwell; needs driver 580+).
# AMD:    AMD's ROCm wheels for Windows, built per GPU family. Python <= 3.13 only.
# CPU:    the standard PyPI wheels.
# Python: the exact version installed into .\python for that backend. 3.12.16 is
# what the AMD build was tested with; 3.13.18 has wheels for every package on
# NVIDIA/CPU. Both are the builds uv $uvVersion provides.
$torchSpecs = @{
    cuda = @{ Index = "https://download.pytorch.org/whl/cu130"; Packages = @("torch>=2.14,<2.15", "torchvision>=0.29,<0.30"); Python = "3.13.18" }
    cpu  = @{ Index = "https://pypi.org/simple";                 Packages = @("torch>=2.14,<2.15", "torchvision>=0.29,<0.30"); Python = "3.13.18" }
    rocm = @{ Index = $null;                                     Packages = @("torch==2.11.0+rocm7.13.0", "torchvision==0.26.0+rocm7.13.0"); Python = "3.12.16" }
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
$reqHash = Get-Sha256 (Join-Path $PSScriptRoot "requirements.txt")
$state = "$Backend|$($spec.Index)|$($spec.Packages -join ',')|$($spec.Python)|$reqHash"
$privatePythonDir = Join-Path $PSScriptRoot "python"

function Test-VenvIsPrivate {
    # True if .venv runs the pinned Python from .\python (not one installed elsewhere on the PC).
    if (-not (Test-Path $venvPython)) { return $false }
    $cfg = Join-Path $venvDir "pyvenv.cfg"
    $home_ = (Get-Content $cfg -ErrorAction SilentlyContinue | Where-Object { $_ -match "^home\s*=" }) -replace "^home\s*=\s*", ""
    if (-not $home_ -or -not $home_.StartsWith($privatePythonDir, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    $version = (& $venvPython -c "import platform; print(platform.python_version())" 2>$null)
    return ($LASTEXITCODE -eq 0 -and "$version".Trim() -eq $spec.Python)
}

if (Test-Path $venvDir) {
    if ($previousBackend -and $previousBackend -ne $Backend) {
        Write-Host "Graphics card choice changed ($previousBackend -> $Backend); rebuilding .venv..."
        Remove-Item -Recurse -Force $venvDir
    } elseif (-not (Test-VenvIsPrivate)) {
        Write-Host "Rebuilding .venv with this folder's own Python $($spec.Python)..."
        Remove-Item -Recurse -Force $venvDir
    }
}

if (-not (Test-Path $venvPython)) {
    $uv = Get-PortableUv
    # Keep uv's Python and cache inside this folder: nothing on the PC changes,
    # and uninstalling removes everything. Ignore any uv settings on the PC
    # (UV_* variables, uv.toml) so they can't send files elsewhere.
    Get-ChildItem env: | Where-Object Name -like "UV_*" | ForEach-Object { Remove-Item "env:$($_.Name)" }
    $env:UV_NO_CONFIG = "1"
    $env:UV_PYTHON_INSTALL_DIR = $privatePythonDir
    $env:UV_CACHE_DIR = Join-Path $PSScriptRoot "tools\uv-cache"
    $env:UV_PYTHON_PREFERENCE = "only-managed"
    Write-Host "Installing Python $($spec.Python) into $privatePythonDir and creating .venv..."
    # --no-bin / --no-registry: don't add python.exe to a PATH folder or register
    # it in the Windows registry (both would be outside this folder).
    & $uv python install --no-bin --no-registry $spec.Python
    if ($LASTEXITCODE -ne 0) { throw "Couldn't download Python $($spec.Python)." }
    & $uv venv --seed --python $spec.Python $venvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-VenvIsPrivate)) { throw "Couldn't create .venv with Python $($spec.Python)." }
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

# Precompile the app's own .py files: the app runs as a normal user and can't
# write __pycache__ into Program Files itself.
& $venvPython -m compileall -q -l $PSScriptRoot | Out-Null

# --- Model -----------------------------------------------------------------------
& $venvPython download_model.py
if ($LASTEXITCODE -ne 0) { throw "Model download failed." }

Write-Host "Setup complete."
try { Stop-Transcript | Out-Null } catch { }
exit 0

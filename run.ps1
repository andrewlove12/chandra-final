# First run needs internet (setup.ps1 installs packages + downloads the model).
# Later runs need no internet; setup.ps1 has nothing to do and processing is offline.
# Any arguments are passed to process_pdfs.py, e.g.  .\run.ps1 C:\notebooks C:\annotated
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

& (Join-Path $PSScriptRoot "setup.ps1")
& (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") process_pdfs.py @args

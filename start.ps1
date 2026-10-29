$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    py -3 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:OSTROVOK_ALLOW_LOCAL_WRITE="true"
& .\.venv\Scripts\python.exe -m uvicorn intelligence.api:app --host 127.0.0.1 --port 8090

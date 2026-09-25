$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3.11 -m venv .venv
}

& .venv\Scripts\python.exe -m pip install --upgrade pip
& .venv\Scripts\python.exe -m pip install -e ".[build]"
& .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --name SP-CLP `
    --add-data "app\static;app\static" `
    --collect-all snap7 `
    app\main.py

Write-Host "Executavel criado em dist\SP-CLP.exe"
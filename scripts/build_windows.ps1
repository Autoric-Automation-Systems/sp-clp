$ErrorActionPreference = "Stop"

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCommand) {
    throw "Python nao foi encontrado no PATH. Instale Python 3.11 ou mais recente e tente novamente."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Criando ambiente virtual com $($pythonCommand.Source)..."
    & $pythonCommand.Source -m venv .venv
}

$venvPython = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Nao foi possivel criar .venv. Verifique a instalacao do Python e tente novamente."
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e ".[build]"
& $venvPython -m PyInstaller --noconfirm --clean --onefile --name SP-CLP `
    --add-data "app\static;app\static" `
    --collect-all snap7 `
    --collect-all tzdata `
    sp_clp_launcher.py

Write-Host "Executavel criado em dist\SP-CLP.exe"
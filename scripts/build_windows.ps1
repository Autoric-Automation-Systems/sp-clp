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
    --add-data "app\library;app\library" `
    --collect-all snap7 `
    --collect-all tzdata `
    sp_clp_launcher.py

Write-Host "Executavel criado em dist\SP-CLP.exe"

# O instalador e opcional: sem o Inno Setup o executavel portatil ja serve.
$version = (Select-String -Path "pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($null -eq $iscc) {
    Write-Host "Inno Setup 6 nao encontrado no PATH: o instalador nao foi gerado."
    Write-Host "Instale o Inno Setup 6, ou distribua dist\SP-CLP.exe como portatil."
    exit 0
}

Write-Host "Gerando o instalador com $($iscc.Source)..."
& $iscc.Source "scripts\sp-clp.iss" "/DAppVersion=$version"
if ($LASTEXITCODE -ne 0) {
    throw "O Inno Setup falhou com o codigo $LASTEXITCODE."
}

Write-Host "Instalador criado em dist\SP-CLP-$version-instalador.exe"
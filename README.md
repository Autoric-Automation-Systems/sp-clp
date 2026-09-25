# SP-CLP

Aplicacao local para monitoramento somente leitura de CLPs Siemens S7-1200.

## Requisitos

- Python 3.11 ou mais recente
- Windows para a distribuicao final
- Acesso de rede ao CLP e porta S7 `102` quando usar o cliente real

## Executar no desenvolvimento

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
python -m pytest
python -m uvicorn app.main:app --reload
```

Abra `http://127.0.0.1:8000` no navegador. O dashboard abre mesmo sem senha e sem máquinas cadastradas. A senha inicial deve ter pelo menos 8 caracteres e é armazenada como hash.

Para testar sem CLP, cadastre uma máquina com IP `fake` depois de autenticar. O cliente simulado usa o mesmo contrato do DB: sinais BOOL de `0.0` a `1.7` e `Count` como `UDInt` big-endian em `DBX2.0`.

## Contrato do CLP

- Rack padrão: `0`
- Slot padrão: `1`
- `AUTO`, `RUN` e `FAULT`: bits `0.0`, `0.1` e `0.2`
- Sinais adicionais: posições fixas até `1.7`, apenas renomeáveis
- `Counter`: `BOOL` em `DBX2.0`
- `Count`: `DInt` em `DB4.0`
- O contador reinicia quando o CLP reinicia; qualquer redução do valor inicia nova linha de base e não produz valor negativo
- Totais horários usam o fuso configurado para cada máquina

## Estrutura

- `app/main.py`: API FastAPI e rotas do dashboard
- `app/plc.py`: protocolo, parser, simulador e adaptador Snap7
- `app/storage.py`: SQLite, amostras e totais horários
- `app/security.py`: hash de senha PBKDF2
- `app/static/`: dashboard HTML/CSS/JavaScript
- `tests/`: testes sem equipamento físico

A comunicação real com um CLP ainda exige teste no equipamento do cliente.

## Gerar o executável Windows

Execute no Windows PowerShell, na raiz do projeto:

```powershell
.\scripts\build_windows.ps1
```

O resultado será `dist\SP-CLP.exe`. Copie esse arquivo para uma pasta de instalação e execute-o; o servidor local será iniciado e o navegador abrirá automaticamente. A pasta `data` será criada ao lado do executável para armazenar configurações e histórico.

O primeiro build gera um executável portátil. Um instalador com atalho, desinstalação e inicialização automática será adicionado depois do teste no Windows e da validação com o CLP real.

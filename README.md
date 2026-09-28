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
- Sinais fixos: `AUTO` (`0.0`), `RUN` (`0.1`), `FAULT` (`0.2`) e `SAFETY` (`0.3`). Fault e Safety são coisas diferentes: falha da máquina e cadeado de segurança. Nome e tipo são do contrato e **não podem ser alterados**.
- Sinais adicionais: posições fixas de `0.4` a `0.7` e de `1.0` a `1.7`; apenas o rótulo é editável
- Cada sinal precisa de um rótulo próprio. A comparação ignora maiúsculas e acentos, e um sinal renomeável também não pode adotar o nome de um sinal fixo
- `Counter`: `BOOL` em `DBX2.0`, com rótulo editável
- `Count`: `DInt` em `DB4.0`
- O contador reinicia quando o CLP reinicia; qualquer redução do valor inicia nova linha de base e não produz valor negativo
- Totais horários usam o fuso configurado para cada máquina

## Endereços

O dashboard mostra um único card com todas as plantas e o status de cada máquina. Cada planta responde no seu próprio endereço, derivado do nome:

- `/RioVerde`, `/Anapolis`, `/Plant-1` — página da planta, com os cards completos de cada máquina
- `/Configuracoes` e `/Ajuda` — menu
- Endereços são comparados sem diferenciar maiúsculas, e nomes reservados (`Ajuda`, `Configuracoes`, `Dashboard`) não viram endereço de planta

A página `Ajuda` traz a documentação de uso: acesso, cadastro, rótulos de sinais, leitura do painel e solução de problemas.

## Estrutura

- `app/main.py`: API FastAPI e rotas do dashboard
- `app/plc.py`: protocolo, parser, simulador e adaptador Snap7
- `app/storage.py`: SQLite, amostras e totais horários
- `app/slugs.py`: nome da planta convertido em endereço
- `app/security.py`: hash de senha PBKDF2
- `app/static/`: dashboard HTML/CSS/JavaScript
- `app/static/icons.js`: ícones do [Lucide](https://lucide.dev) gerados por `scripts/build_icons.py` e servidos localmente, sem CDN
- `tests/`: testes sem equipamento físico

A comunicação real com um CLP ainda exige teste no equipamento do cliente.

O banco fica em `data/sp-clp.sqlite3`. Defina `SP_CLP_DB` para apontar para outro arquivo; a suíte de testes usa essa variável para nunca escrever no banco de desenvolvimento.

Para regenerar os ícones é preciso internet (o script baixa uma versão fixa do Lucide):

```bash
python scripts/build_icons.py
```

## Gerar o executável Windows

Execute no Windows PowerShell, na raiz do projeto:

```powershell
.\scripts\build_windows.ps1
```

O script usa o comando `python` disponível no PATH e cria `.venv` automaticamente. O resultado será `dist\SP-CLP.exe`. Copie esse arquivo para uma pasta de instalação e execute-o; o servidor local será iniciado e o navegador abrirá automaticamente. A pasta `data` será criada ao lado do executável para armazenar configurações e histórico.

O primeiro build gera um executável portátil. Um instalador com atalho, desinstalação e inicialização automática será adicionado depois do teste no Windows e da validação com o CLP real.

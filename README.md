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
- Polaridade dos bits fixos: `AUTO` 1 = automático, 0 = manual; `RUN` 1 = produzindo, 0 = parado; `FAULT` 1 = **em falha**, 0 = normal; `SAFETY` 1 = normal, 0 = pendente
- Sinais adicionais: posições fixas de `0.4` a `0.7` e de `1.0` a `1.7`; apenas o rótulo é editável
- Cada sinal precisa de um rótulo próprio. A comparação ignora maiúsculas e acentos, e um sinal renomeável também não pode adotar o nome de um sinal fixo
- `Counter`: `BOOL` em `DBX2.0`, com rótulo editável
- `Count`: `DInt` em `DB4.0`
- O contador reinicia quando o CLP reinicia; qualquer redução do valor inicia nova linha de base e não produz valor negativo
- Totais horários usam o fuso configurado para cada máquina

## Identidade do cliente

Em **Configurações → Identidade** o cliente define o **nome da empresa** e envia o **logotipo** que aparecem no cabeçalho e no título da aba. O logo fica em `data/logo.<ext>` (ao lado do banco) e é servido por `/api/branding/logo`.

- Formatos aceitos: PNG, JPEG, GIF ou WEBP até 1000 KB
- O formato é identificado pelos **bytes de assinatura**, não pelo nome do arquivo
- **SVG não é aceito**: é um documento que pode executar script na mesma origem do painel
- Sem logo configurado, o painel usa o ícone que acompanha o aplicativo

## Contagens por hora

O botão **Contagens por hora** de cada máquina abre uma janela com o gráfico de barras de um dia, uma barra por hora. O dia mostrado é o dia atual no fuso da máquina, e a navegação (`Dia anterior` / `Próximo dia`) para no primeiro dia com histórico e em hoje.

- `GET /api/machines/{id}/hourly-counts?day=AAAA-MM-DD` devolve o dia pedido; sem o parâmetro, responde o dia atual
- A resposta traz `day`, `today`, `first_day`, `last_day` e `slots` (as horas do dia, com quantidade zero onde não houve contagem)
- Um dia com mudança de horário de verão tem 23 ou 25 horas, e o número de `slots` acompanha isso

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
- `app/branding.py`: nome da empresa e logotipo do cabeçalho
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

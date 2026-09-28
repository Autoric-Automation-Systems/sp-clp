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

O executável escuta em **todas as interfaces** (`0.0.0.0:8000`), então o painel também responde pelo nome do computador e pelo IP da rede — é o que permite salvar um favorito amigável (`http://nome-do-pc:8000`) e abrir de outra máquina da planta. A página **Ajuda** mostra os endereços válidos, lidos de `/api/access`.

### Apelido da marca (`http://sp-clp`)

O painel gosta de ser chamado de `sp-clp`, mas o nome precisa existir no Windows para virar endereço. `/api/access` informa `alias`, `alias_url` e `alias_ready`, e a Ajuda só anuncia o apelido quando ele realmente responde neste computador. Dois caminhos, os dois exigindo administrador uma única vez:

- **Renomear o computador** para `sp-clp` — qualquer máquina da rede passa a abrir `http://sp-clp:8000`
- **Registrar no `hosts`** de quem acessa, com a linha `127.0.0.1 sp-clp` (ou o IP do painel, para acesso remoto)

Com a **porta 80** o endereço fica sem sufixo, só `http://sp-clp`:

```powershell
$env:SP_CLP_PORT = "80"
```

O instalador registra o apelido no `hosts` e libera a regra de firewall automaticamente.

Para limitar de novo a este computador, ou trocar a porta, use as variáveis de ambiente:

```powershell
$env:SP_CLP_HOST = "127.0.0.1"   # só localhost
$env:SP_CLP_PORT = "8080"
```

Na primeira execução o Windows pode pedir permissão de firewall; permita em redes privadas, senão o painel responde apenas neste computador.

Para testar sem CLP, cadastre uma máquina com IP `fake` depois de autenticar. O cliente simulado usa o mesmo contrato do DB: sinais BOOL de `0.0` a `1.7`, `Counter` em `DBX2.0`, a assinatura `SPCLP` em `DBX4.0` e `Count` como `DInt` big-endian em `DB10.0`. A varredura com `fake` responde `ready`.

## Infraestrutura de rede

O computador que roda o SP-CLP precisa alcançar **cada CLP** cadastrado; os CLPs não são descobertos sozinhos.

- **Porta 102 (S7)** liberada entre o painel e cada CLP, em qualquer firewall, roteador ou VLAN do caminho
- **Rede cabeada com switch**: mais estável e a recomendada para o painel
- **Roteador Wi-Fi da própria rede local**: painel e CLP no mesmo roteador e na mesma faixa de IP
- **Access point ou repetidor**: para cobrir galpões distantes, sem isolar os clientes entre si
- **Várias plantas**: interligue as intranets (cabo, fibra, rádio enlace ou VPN site a site) com rota para a faixa de IP dos CLPs
- **VLAN separada**: precisa de rota entre a VLAN do painel e a dos CLPs

Não funciona com CLP atrás de NAT sem encaminhamento, rede de visitantes isolada, ou faixas de IP repetidas em plantas diferentes sem tradução de endereços.

A página **Ajuda** do painel traz o mesmo conteúdo para o cliente.

## Contrato do CLP

- Rack padrão: `0`
- Slot padrão: `1`
- Sinais fixos: `AUTO` (`0.0`), `RUN` (`0.1`), `FAULT` (`0.2`) e `SAFETY` (`0.3`). Fault e Safety são coisas diferentes: falha da máquina e cadeado de segurança. Nome e tipo são do contrato e **não podem ser alterados**.
- Polaridade dos bits fixos: `AUTO` 1 = automático, 0 = manual; `RUN` 1 = produzindo, 0 = parado; `FAULT` 1 = **em falha**, 0 = normal; `SAFETY` 1 = normal, 0 = pendente
- Sinais adicionais: posições fixas de `0.4` a `0.7` e de `1.0` a `1.7`; apenas o rótulo é editável
- Cada sinal precisa de um rótulo próprio. A comparação ignora maiúsculas e acentos, e um sinal renomeável também não pode adotar o nome de um sinal fixo
- `Counter`: `BOOL` em `DBX2.0`, com rótulo editável
- Assinatura: `ARRAY[0..4] OF CHAR` em `DBX4.0` com o valor `SPCLP`, usada pela varredura do cadastro
- `Count`: `DInt` big-endian em `DB10.0`
- O byte `9` é o espaço de alinhamento entre a assinatura e o contador
- Uma única leitura de 14 bytes (`0`–`13`) traz o estado, a assinatura e o contador
- `Int_1`, `Int_2` e `Int_3` em `DB14.0`, `DB18.0` e `DB22.0` existem no bloco mas **não fazem parte do contrato**; o painel não os lê
- O bloco precisa estar com o **acesso otimizado desligado**: o painel lê endereços absolutos, e num bloco otimizado essas posições não existem
- O contador reinicia quando o CLP reinicia; qualquer redução do valor inicia nova linha de base e não produz valor negativo
- Totais horários usam o fuso configurado para cada máquina

## Varredura do CLP

O botão **Verificar CLP** do formulário de máquina lê o endereço informado **antes** de salvar e responde em qual dos quatro casos o cadastro está:

| Resultado | Significado | Onde corrigir |
| --- | --- | --- |
| `ready` | A DB existe e tem a assinatura `SPCLP` | Nada |
| `missing` | O CLP respondeu, mas a leitura da DB foi recusada | Número do DB, bloco otimizado |
| `unsigned` | A DB respondeu, mas sem a assinatura do SP-CLP | DB errada, ou bloco sem a assinatura |
| `unreachable` | Não houve resposta do CLP | IP, cabo, rack `0` / slot `1` |

- `POST /api/config/plc/probe` com `{"ip": "...", "db_number": N}`; exige autenticação, como todo endpoint de configuração
- **Somente leitura**: o endpoint nunca escreve no CLP, e a conexão é aberta, usada e fechada, para não disputar a sessão do polling
- Sem `missing` e `unsigned` separados, um DB errado mostraria valores plausíveis e sem sentido no dashboard
- Com `fake` no lugar do IP, a varredura usa o simulador e serve para demonstrar o fluxo sem CLP

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
- `app/access.py`: endereços de acesso e configuração do bind
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

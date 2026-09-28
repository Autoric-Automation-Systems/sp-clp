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

Para testar sem CLP, cadastre uma máquina com IP `fake` depois de autenticar. O cliente simulado usa o mesmo contrato do DB: os 16 BOOLs em `0.0`–`1.7` com `Counter` em `DBX0.4`, a assinatura `SPCLP` em `DBX14.0` e `Count` como `DInt` big-endian em `DB20.0`. A varredura com `fake` responde `ready`.

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
- Sinais padrão: `AUTO` (`0.0`), `RUN` (`0.1`), `FAULT` (`0.2`), `SAFETY` (`0.3`) e `COUNTER` (`0.4`). Fault e Safety são coisas diferentes: falha da máquina e cadeado de segurança. Nome e tipo são do contrato e **não podem ser alterados**
- Polaridade dos bits padrão: `AUTO` 1 = automático, 0 = manual; `RUN` 1 = produzindo, 0 = parado; `FAULT` 1 = **em falha**, 0 = normal; `SAFETY` 1 = normal, 0 = pendente; `COUNTER` 1 = contando, 0 = sem contagem
- Cores do card: `AUTO` azul, `RUN` e `SAFETY` verdes, `FAULT` vermelho, `COUNTER` ciano. Bit em 0 fica cinza e sem leitura do CLP fica tracejado, que são coisas diferentes
- Sinais: os 16 BOOLs ocupam `0.0` a `1.7`, sem intervalo
- Sinais adicionais: posições fixas de `0.5` a `0.7` e de `1.0` a `1.7`; apenas o rótulo é editável
- Os sinais livres aparecem no card como `1 LIGADO` ou `0 DESLIGADO`, com o bit à vista, porque o nome é do cliente e não há vocabulário combinado
- Os sinais livres e os três inteiros aparecem no card em *Ver sinais*; os cinco sinais padrão têm bloco próprio
- O `COUNTER` é diagnóstico: o CLP conta na **borda de subida** do bit, então o bloco diz se a contagem está acontecendo agora, não quanto já foi contado
- O número grande do card é o `Count` (`DB20.0`), **não** o bit `COUNTER`: o painel só lê, quem incrementa o `Count` é o programa do CLP
- `Valor 1`, `Valor 2` e `Valor 3`: `DInt` big-endian em `DB2.0`, `DB6.0` e `DB10.0`, com rótulo editável
- Cada sinal precisa de um rótulo próprio. A comparação ignora maiúsculas e acentos, e um sinal renomeável também não pode adotar o nome de um sinal padrão
- Assinatura: `ARRAY[0..4] OF CHAR` em `DBX14.0` com o valor `SPCLP`, usada pela varredura do cadastro
- `Count`: `DInt` big-endian em `DB20.0`
- O byte `19` é o espaço de alinhamento entre a assinatura e o contador
- A leitura de 24 bytes (`0`–`23`) traz estado, inteiros, assinatura e contador numa só ida ao CLP
- Os três inteiros aparecem no card da máquina junto com os sinais livres, e não entram nos totais horários
- O bloco precisa estar com o **acesso otimizado desligado**: o painel lê endereços absolutos, e num bloco otimizado essas posições não existem
- O contador reinicia quando o CLP reinicia; qualquer redução do valor inicia nova linha de base e não produz valor negativo
- Totais horários usam o fuso configurado para cada máquina

## Varredura do CLP

**Procurar DB**, no formulário de máquina, varre o CLP informado de `DB1` a `DB1000` lendo o bloco da assinatura em cada número e lista os DBs que respondem `SPCLP`. Cada DB encontrado vira um botão: clicar nele leva o número ao campo *DB*, então ninguém digita o número à mão.

| Resultado | Significado |
| --- | --- |
| `ready` | Um ou mais DBs têm a assinatura; cada um aparece para seleção |
| `unsigned` | O CLP respondeu, mas nenhuma DB da faixa tem a assinatura no byte `14` |
| `unreachable` | Não houve resposta do CLP, ou a conexão caiu no meio da varredura |

- `POST /api/config/plc/scan` com `{"ip": "...", "first": 1, "last": 1000}`; `first` e `last` são opcionais
- Exige autenticação, como todo endpoint de configuração
- Cada DB da faixa custa uma leitura: são cerca de mil idas e voltas, alguns segundos numa rede de planta
- A varredura para sozinha aos `25 s` ou em `2000` DBs por chamada, e marca `truncated` quando não chegou ao fim da faixa
- Se a conexão cair no meio, a resposta é `unreachable` e os DBs já confirmados continuam na lista
- Quando nenhuma DB tem a assinatura mas algumas respondem à leitura, o `detail` mostra o que a primeira tem no offset — é o que separa "offset errado" de "CLP sem o bloco"
- **Somente leitura**: nenhum caminho escreve no CLP, e a conexão é aberta, usada e fechada, para não disputar a sessão do polling
- Com `fake` no lugar do IP, a varredura usa o simulador: `DB1` responde `ready`, o que demonstra o fluxo sem CLP

## Biblioteca do CLP

O bloco `FB_SP-CLP`, que declara o DB lido pelo painel, viaja junto com o aplicativo e é oferecido na página **Ajuda**, no tópico *9. Biblioteca*. Ajuda é uma página pública, e a biblioteca é parte de usar o produto, não de administrá-lo.

Um diretório por família de CLP, porque a instalação pode atender mais de uma marca:

```
app/library/
    s7/SP-CLP.zal17
    s7/SP-CLP.zal16
    mitsubishi/SP-CLP.gxw
```

- **Tudo o que estiver dentro de um diretório de família é oferecido**, com qualquer extensão, porque as ferramentas de CLP batizam o arquivo com a própria versão: o TIA Portal escreve `.zal` em versões antigas e `SP-CLP_20260928_1642.zal17` na V17, com data e hora do export. O nome do arquivo é o do pacote exportado; o prefixo `FB_` fica só no nome do bloco, dentro do pacote
- **Os arquivos são versionados no repositório**, junto com o código: o painel e o bloco precisam andar juntos, e um build sem a biblioteca gera um instalador que não entrega o bloco ao cliente. São cerca de 300 KB por versão
- A pasta entra no executável pelo `--add-data` de `scripts/build_windows.ps1`, então **trocar a biblioteca exige gerar o `.exe` de novo**
- O nome amigável de cada família está em `FAMILY_LABELS`, em `app/libraries.py`; uma pasta não listada aparece com o próprio nome
- Arquivos começando com ponto são ignorados, que é como uma pasta vazia sobrevive ao `git` (`.gitkeep`)
- `GET /api/library` lista `family`, `label`, `filename`, `size_bytes` e `url` de cada arquivo. Sem arquivo nenhum, a Ajuda avisa em vez de oferecer um link que falharia
- `GET /api/library/{familia}/{arquivo}` baixa o arquivo. A busca é por nome exato dentro das pastas já varridas, então o que o cliente digita na URL nunca vira caminho: `..%2F..%2Fsegredo.txt` responde 404
- Os diretórios não são públicos e não estão sob `/static`

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

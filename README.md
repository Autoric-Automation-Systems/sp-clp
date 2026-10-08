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

Quando a máquina tem **mais de uma placa de rede** — a rede da planta e o Wi-Fi, uma placa NAT de máquina virtual, ou um adaptador do WSL/Hyper-V — a Ajuda lista o endereço de **cada uma**, com o da rota padrão primeiro. Listar todos é o que salva o caso em que a rota padrão aponta justamente para a placa que ninguém alcança, que é o que acontece numa VM com adaptador NAT. Endereços `127.` (loopback, que aparece como `localhost`) e `169.254.` (o que sobra de cabo desconectado ou DHCP ausente) ficam de fora, porque não levam a lugar nenhum.

É o mesmo caminho para **celular ou tablet**: o endereço de IP, como `http://192.168.0.10:8000`, com o aparelho na mesma rede do painel. O nome da máquina normalmente não resolve no celular, porque ele não consulta o `hosts` do Windows. O layout se ajusta à tela pequena. Wi-Fi de visitantes, ou com clientes isolados entre si, não chega até o painel.

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

## Acesso e senha

Monitorar não exige senha; **configurar exige**. A senha é criada na primeira execução, com pelo menos 8 caracteres, e fica guardada como hash PBKDF2-SHA256 em `data/sp-clp.sqlite3` — nunca como texto, e nunca aparece em log nem em mensagem de erro.

- **A sessão termina sozinha após 5 minutos** sem uso, dos dois lados: o navegador desliga o painel e o servidor descarta o token. Alargar a janela depois não ressuscita um token vencido
- Qualquer pedido autenticado empurra o prazo. O `polling` de 5 s do card **não** conta como uso, senão o painel nunca se desligaria
- **Trocar a senha**: *Configurações → Acesso* pede a senha atual, a nova e a repetição. A repetição é conferida no navegador, porque um erro de digitação só se resolve indo até a máquina do painel
- A troca **encerra as outras sessões** e mantém a de quem trocou
- Senha atual errada responde **403**, não 401: 401 é sessão inválida, e o painel sairia da configuração por causa de um erro de digitação
- Sem senha configurada, `GET /api/setup/status` responde `password_configured: false` e o painel oferece a criação; depois disso, `POST /api/setup/password` responde `409`
- **Esqueceu a senha**: rode o comando de recuperação na máquina do painel, descrito abaixo. Não existe redefinição por rede, e-mail ou link

### Esqueceu a senha

Na máquina do painel, abra um terminal e rode:

```powershell
.\SP-CLP.exe --reset-password
```

O comando pede a senha nova **duas vezes** e encerra sem subir o painel. Depois disso, inicie o painel normalmente e entre com a senha nova. Em desenvolvimento, sem executável: `sp-clp --reset-password`.

- É um **comando local, na máquina do painel**, na mesma ideia do `grafana-cli admin reset-admin-password`: quem tem o console já pode apagar `data/sp-clp.sqlite3`, e um caminho estreito é melhor do que empurrar o cliente para essa exclusão
- Pede **só a senha nova**. A senha antiga não ajuda em nada aqui
- A senha é lida do console com eco desligado, então **não entra no histórico** do PowerShell nem na linha de comando do processo
- **Nada fica guardado** em variável de ambiente nem no registro, então reiniciar o painel não repete a redefinição. É por isso que o comando não é uma variável `SP_CLP_RESET_PASSWORD`: com `setx`, essa variável reescreveria a senha em **todo** arranque
- Falha em vez de gravar uma senha curta ou uma repetição que não confere, e sai com código `1`
- Não existe endpoint HTTP de redefinição. O painel escuta em `0.0.0.0`, e uma rota dessas seria um acesso remoto
- Se o painel estiver aberto, **feche antes**: as sessões vivem na memória do servidor e só caem no próximo arranque. O comando avisa isso ao terminar

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
- O card desenha **só o ícone**: a forma diz qual sinal e a cor diz o estado. O nome, o endereço, o bit cru e a palavra do estado ficam no `title`, que aparece ao passar o mouse
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
    s7/FB_SP-CLP.scl
    mitsubishi/SP-CLP.gxw
```

O arquivo de cada família é uma **fonte SCL**, importada no TIA Portal como *fonte externa* (*External source files*) e compilada em bloco. Serve do **TIA Portal 13 em diante**, então existe **uma versão só**: não há arquivo por versão do TIA para escolher, e o próprio nome do arquivo já é o nome do bloco.

- A fonte declara o bloco com `S7_Optimized_Access := 'FALSE'`, ou seja, o **acesso otimizado já vem desligado** do arquivo. É exatamente o que o painel precisa: ele lê endereços absolutos, e num bloco otimizado essas posições não existem
- O bloco soma na **borda de subida** de `Counter` (uma instância `R_TRIG`) e **só enquanto `RUN` está ligado**: máquina parada não soma. E o contador é zerado na primeira varredura (`FirstScan`), então o número começa do zero a cada arranque do CLP — a queda que o painel trata como nova linha de base, sem produção negativa
- **Tudo o que estiver dentro de um diretório de família é oferecido**, com qualquer extensão: o carregador não filtra por sufixo, então uma exportação antiga em `.zal`/`.zal17` ou o arquivo de outra marca entram na lista do mesmo jeito
- **Os arquivos são versionados no repositório**, junto com o código: o painel e o bloco precisam andar juntos, e um build sem a biblioteca gera um instalador que não entrega o bloco ao cliente. A fonte é texto, então pesa poucos KB
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

## Tendência dos sinais

O botão **Tendência dos sinais** abre uma janela **separada da de contagens**: quanto a máquina produziu é um número, e quanto tempo ela passou produzindo, parada ou em falha é outro assunto. São quatro barras horizontais, uma por sinal padrão — `AUTO`, `RUN`, `FAULT` e `SAFETY` —, cada uma com o dia inteiro e o tempo de cada estado em horas e em porcentagem.

```
AUTO     ████████████████████████░░░░░░░░  Automático 22h30 · 94% · Sem leitura 1h30 · 6%
RUN      ██████████░░░░░░██████░░░░░░░░░░  Produzindo 10h30 · 44% · Parado 12h · 50% · Sem leitura 1h30 · 6%
```

- **A barra cinza hachurada é "sem leitura"**, e é o ponto principal da tela: um trecho em que nenhum CLP foi lido não é a mesma coisa que a máquina parada. Sem isso o cliente leria um painel desligado como "máquina parada o dia inteiro"
- O estado só é dado como conhecido **entre duas leituras próximas** (até `20 s`, ou seja três ciclos do gravador). Entre uma leitura e a próxima distante, o trecho fica como sem leitura; a leitura mais recente ainda responde pelo instante atual, que é o que faz o dia de hoje chegar até agora
- As porcentagens são do **trecho do dia que já decorreu**: num dia passado, das 24 h; hoje, de agora até a meia-noite de hoje. Por isso o cartão mostra "14h29 decorridas" ao lado do dia
- Os estados usam as palavras do contrato e a cor do card: `AUTO` azul, `RUN` e `SAFETY` verdes, `FAULT` vermelho
- `GET /api/machines/{id}/signals-day?day=AAAA-MM-DD` devolve `day`, `today`, `first_day`, `last_day`, `elapsed_seconds`, `day_seconds` e um item por sinal, com `segments` (os trechos, em hora local da máquina), `on_seconds`, `off_seconds`, `unknown_seconds` e as três porcentagens
- O `COUNTER` fica de fora: é pulso de diagnóstico, e uma barra dele seria ruído

## Modo TV

`/tvpanel` é a tela para deixar num monitor na parede: **uma máquina por vez ocupando a tela inteira**, trocando sozinha a cada **5 segundos**. É a mesma leitura do painel normal, com outra roupa.

- Cada sinal aparece como um círculo grande **com o nome e o estado escritos embaixo**. No card normal isso vive no `title`, que só aparece passando o mouse — e na parede ninguém passa o mouse
- O cabeçalho sai de cena e o rodapé vira faixa de marca: o logotipo da Autoric **sem link**, o slogan e os endereços escritos. Numa parede ninguém clica, então os links do rodapé normal saem de cena
- O **Modo TV** tem entrada no menu, com ícone próprio, e o botão acende enquanto a rota está aberta
- Os controles ficam discretos no canto (30% de opacidade, cheios quando o mouse chega) e são os de um videocassete, na ordem da fita: **voltar, pausar, tocar e avançar**, mais **Sair do modo TV**. O botão aceso diz o estado: pausa acesa, parado; play aceso, rodando
- Uma barra fina no topo mostra o tempo até o próximo slide; na pausa ela **para e fica âmbar**. Voltar ou avançar à mão entrega o slide inteiro de novo, e chegar ao endereço da TV sempre volta a tocar
- Com uma máquina só a rotação não existe e os quatro botões ficam desabilitados; sem nenhuma, o painel explica onde cadastrar
- **Não pede senha**: é leitura, como o resto do painel, e continua funcionando com a sessão de configuração encerrada
- O endereço é **reservado**: nenhuma planta responde nele, nem uma que se chame `tvpanel`

## Histórico que o painel grava

Uma thread do próprio servidor lê cada máquina cadastrada a cada **5 segundos**, para o histórico **não depender de um navegador estar aberto**. Antes isso era efeito colateral do `GET /api/machines/{id}/status`, ou seja, fechar o painel parava de gravar; agora o endpoint de status é só leitura e quem grava é o gravador.

- **Só leitura no CLP.** O gravador lê e escreve no banco; nada nele escreve no PLC
- A contagem é gravada como delta, com a mesma política de sempre: valor que diminuiu inicia nova linha de base e não vira produção negativa
- Os sinais viram uma linha **por mudança**, não por leitura (`signal_events`): um bit que não se mexe não gera linha, então um dia inteiro custa algumas centenas de registros em vez de 17 mil
- O intervalo se troca por `SP_CLP_POLL_SECONDS` (padrão `5`, mínimo `1`); o gravador nunca morre por causa de um CLP fora do ar, ele tenta no ciclo seguinte
- `app/signals.py` monta o dia a partir dos eventos e das leituras, sem tocar em banco nem em relógio, para dar para testar sem CLP

### O que fica guardado e o que é apagado

O histórico é curto de propósito, e ele é **só o histórico**: a limpeza não encosta no cadastro nem nas configurações.

| Guardado sempre | Apagado depois de 7 dias |
| --- | --- |
| Plantas, áreas e máquinas | `count_samples` — uma linha por leitura |
| Rótulos dos sinais | `hourly_counts` — os totais por hora |
| Senha (como hash) e nome da empresa | `signal_events` — as mudanças dos quatro sinais |
| Logotipo, em `data/logo.<ext>` | — |
| Porta, apelido e o resto em `settings` | — |

- Tudo isso vive em `data/sp-clp.sqlite3`, ao lado do executável; `SP_CLP_DB` aponta para outro arquivo
- A limpeza roda **no arranque e a cada hora**. Ao atualizar uma instalação que já gravava produção, os dias anteriores aos 7 são apagados na primeira limpeza — o cadastro continua intacto
- Os 7 dias são a constante `RETENTION_DAYS`, em `app/recorder.py`. Mudar o prazo é mexer no código: trocar isso por um banco com histórico longo é assunto da versão PRO
- **Apagar o banco apaga tudo**, inclusive o cadastro. É justamente por isso que a recuperação de senha tem comando próprio (`--reset-password`) em vez de mandar apagar o arquivo

## Endereços

O dashboard mostra um único card com todas as plantas e o status de cada máquina. Cada planta responde no seu próprio endereço, derivado do nome:

- `/RioVerde`, `/Anapolis`, `/Plant-1` — página da planta, com os cards completos de cada máquina
- `/Configuracoes` e `/Ajuda` — menu
- Endereços são comparados sem diferenciar maiúsculas, e nomes reservados (`Ajuda`, `Configuracoes`, `Dashboard`) não viram endereço de planta
- **O endereço é o que identifica uma planta.** Ao adicionar uma área, a planta é escolhida na lista das que já existem, e uma grafia diferente — maiúscula, acento, espaço a mais ou hífen — cai na mesma planta, porque o endereço é o mesmo. Duas plantas nunca dividem um endereço

A página `Ajuda` traz a documentação de uso: acesso, cadastro, rótulos de sinais, leitura do painel e solução de problemas.

## Estrutura

- `app/main.py`: API FastAPI e rotas do dashboard
- `app/plc.py`: protocolo, parser, simulador e adaptador Snap7
- `app/storage.py`: SQLite, amostras, eventos dos sinais e totais horários, e onde a pasta de dados é resolvida
- `app/models.py`: modelos de entrada e saída da API (Pydantic)
- `app/timezones.py`: fuso da máquina, hora local e limites do dia
- `app/recorder.py`: a thread que lê os CLPs de 5 em 5 segundos e limpa o histórico velho
- `app/signals.py`: o dia de um sinal a partir dos eventos gravados
- `app/branding.py`: nome da empresa e logotipo do cabeçalho
- `app/libraries.py`: arquivos da biblioteca do CLP, por família
- `app/access.py`: endereços de acesso e configuração do bind
- `app/slugs.py`: nome da planta convertido em endereço
- `app/recovery.py`: os comandos de linha de comando, `--reset-password` e `--no-browser`
- `app/security.py`: hash de senha PBKDF2
- `app/static/`: dashboard HTML/CSS/JavaScript
- `app/static/icons.js`: ícones do [Lucide](https://lucide.dev) gerados por `scripts/build_icons.py` e servidos localmente, sem CDN
- `tests/`: testes sem equipamento físico
- `SP-CLP-rede.pdf`: **duas folhas A4 deitadas** para entregar ao cliente. A primeira apresenta o produto, com telas reais do painel; a segunda explica a rede, o que o cliente ganha e o que precisa existir. Sai de `scripts/rede-sp-clp.html` por `scripts/build_rede_pdf.py`, que injeta os ícones, as marcas e as telas e imprime no Chrome. As telas são capturadas do painel rodando com o simulador por `scripts/capture_painel.py`

A comunicação real com um CLP ainda exige teste no equipamento do cliente.

O banco fica em `data/sp-clp.sqlite3`, **ao lado do executável**, e o logotipo do cliente na mesma pasta. A pasta é resolvida a partir do executável e **não** do diretório de onde o painel foi iniciado: um atalho sem "Iniciar em", uma tarefa agendada ou um serviço começam em `C:\Windows\System32`, e um caminho relativo gravaria os dados do cliente ali — ou falharia por falta de permissão.

Defina `SP_CLP_DB` para apontar para outro arquivo. Um caminho relativo nessa variável é lido da pasta do aplicativo, e um caminho absoluto é usado como está; a suíte de testes usa essa variável para nunca escrever no banco de desenvolvimento.

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

O primeiro build gera um executável portátil, e o mesmo script gera o instalador quando o [Inno Setup 6](https://jrsoftware.org/isinfo.php) estiver instalado.

### Instalador

Com o `ISCC.exe` no PATH, o script acima compila `scripts\sp-clp.iss` e deixa `dist\SP-CLP-<versão>-instalador.exe`. Sem o Inno Setup ele avisa e para no executável portátil, que continua funcionando.

Além de copiar o executável para `C:\SP-CLP`, o instalador:

- **Pergunta a porta** (padrão `8000`) e **libera essa porta no firewall**, guardando a escolha como variável de ambiente da máquina. Com a porta `80` o endereço fica sem sufixo, só `http://sp-clp`
- **Registra `127.0.0.1 sp-clp` no arquivo `hosts`** quando o apelido ainda não está lá
- Cria os atalhos do menu Iniciar e, se marcado, da área de trabalho. Ao terminar, mostra os endereços para passar à equipe
- Opcionalmente cria uma **tarefa agendada** que sobe o painel junto com o Windows, **sem precisar de ninguém logado**, passando `--no-browser`: no arranque não há tela para abrir o navegador
- **Fecha um painel aberto antes de substituir o executável**, senão o arquivo está em uso
- Na desinstalação, tira a regra de firewall, a tarefa agendada e a linha do `hosts`, e **preserva a pasta `data`**: cadastro, senha, histórico e logotipo continuam ali e uma instalação nova os reutiliza

A instalação fica fora de `Arquivos de Programas` de propósito: o painel grava `data\` ao lado do próprio executável, e ali um usuário comum não teria permissão de escrita.

**Nenhuma instalação real foi executada ainda.** O script está escrito e revisado linha a linha, mas a primeira instalação no Windows precisa confirmar o `tzdata`, a pasta `app/library` empacotada, o registro no `hosts`, a regra de firewall e a tarefa agendada.

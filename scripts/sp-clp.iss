; Instalador do SP-CLP para Windows, compilado pelo Inno Setup 6:
;
;   ISCC.exe scripts\sp-clp.iss /DAppVersion=0.1.0
;
; O que ele faz, alem de copiar o executavel: registra o apelido sp-clp no arquivo
; hosts, libera a porta escolhida no firewall, cria os atalhos e, se pedido, uma
; tarefa agendada que sobe o painel junto com o Windows.
;
; A pasta de dados (data\, ao lado do executavel) nao entra na lista de arquivos,
; entao desinstalar preserva o cadastro, a senha e o historico. Instalar de novo por
; cima reusa os mesmos dados, porque o AppId e o mesmo.
;
; NUNCA mude o AppId: e ele que faz o Windows Installer reconhecer uma instalacao
; existente em vez de criar uma segunda.

#define AppName "SP-CLP"
#define AppExe "SP-CLP.exe"
#define AppAlias "sp-clp"
#define AppPublisher "Autoric Automação e Sistemas"
#define AppURL "https://www.autoric.com.br"
#define AppRepo "https://github.com/Autoric-Automation-Systems/sp-clp"

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

#ifndef DefaultPort
  #define DefaultPort "8000"
#endif

; As propriedades do executável querem quatro números.
#define AppVersionInfo AppVersion + ".0"

[Setup]
AppId={{5cb6903f-470d-4ee6-87db-b1d339b3a671}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppRepo}
AppUpdatesURL={#AppRepo}
; Fora de "Arquivos de Programas" de proposito: o painel grava data\ ao lado do
; executavel, e ali um usuario comum nao tem permissao de escrita.
DefaultDirName=C:\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir=..\dist
OutputBaseFilename={#AppName}-{#AppVersion}-instalador
SetupIconFile=..\app\static\assets\favicon_io\favicon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; A porta escolhida vira variavel de ambiente da maquina; o Inno avisa o Explorer.
ChangesEnvironment=yes
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Instalador do {#AppName}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersionInfo}
VersionInfoVersion={#AppVersionInfo}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[CustomMessages]
brazilianportuguese.StartupTask=Iniciar o painel junto com o Windows (recomendado)
brazilianportuguese.DesktopIcon=Criar atalho na área de trabalho
brazilianportuguese.OpenPanel=Abrir o painel agora
brazilianportuguese.PortPageTitle=Porta do painel
brazilianportuguese.PortPageSubtitle=Por qual porta o painel deve responder?
brazilianportuguese.PortPageText=O painel responde em http://sp-clp (porta 80) ou http://sp-clp:PORTA. A regra de firewall é criada para a porta escolhida.%n%nCom 8000 o endereço fica http://nome-do-pc:8000.

[Tasks]
Name: "startup"; Description: "{cm:StartupTask}"; GroupDescription: "Início automático:"
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "Atalhos:"

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Painel de monitoramento"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; A porta fica guardada aqui para a desinstalação saber qual regra remover.
Root: HKLM; Subkey: "Software\Autoric\{#AppName}"; ValueType: string; ValueName: "Port"; ValueData: "{code:GetPort}"; Flags: uninsdeletekey
; 8000 é o padrão do aplicativo, então só faz diferença quando o cliente escolhe outra.
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: string; ValueName: "SP_CLP_PORT"; ValueData: "{code:GetPort}"; Flags: uninsdeletevalue

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""{#AppName} ({code:GetPort})"" dir=in action=allow protocol=TCP localport={code:GetPort}"; Flags: runhidden; StatusMsg: "Liberando a porta {code:GetPort} no firewall..."
Filename: "{sys}\schtasks.exe"; Parameters: "/Create /TN ""{#AppName}"" /TR ""\""{app}\{#AppExe}\"" --no-browser"" /SC ONSTART /RU SYSTEM /F"; Tasks: startup; Flags: runhidden; StatusMsg: "Agendando a inicialização com o Windows..."
Filename: "{app}\{#AppExe}"; Description: "{cm:OpenPanel}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""{#AppName}"" /F"; Flags: runhidden; RunOnceId: "RemoveStartupTask"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#AppName} ({code:GetPort})"""; Flags: runhidden; RunOnceId: "RemoveFirewallRule"

[Code]
var
  PortPage: TInputQueryWizardPage;

function StoredPort(): String;
begin
  if not RegQueryStringValue(HKLM, 'Software\Autoric\{#AppName}', 'Port', Result) then
    Result := '{#DefaultPort}';
end;

function GetPort(Param: String): String;
begin
  { Durante a instalação vale a escolha da tela; na desinstalação, o que ficou guardado. }
  if PortPage <> nil then
    Result := PortPage.Values[0]
  else
    Result := StoredPort();
end;

function PanelUrl(): String;
begin
  if GetPort('') = '80' then
    Result := 'http://{#AppAlias}'
  else
    Result := 'http://{#AppAlias}:' + GetPort('');
end;

procedure InitializeWizard();
begin
  PortPage := CreateInputQueryPage(wpSelectTasks, ExpandConstant('{cm:PortPageTitle}'),
    ExpandConstant('{cm:PortPageSubtitle}'), ExpandConstant('{cm:PortPageText}'));
  PortPage.Add('Porta:', False);
  PortPage.Values[0] := StoredPort();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Port: Integer;
begin
  Result := True;
  if (PortPage <> nil) and (CurPageID = PortPage.ID) then
  begin
    if not TryStrToInt(Trim(PortPage.Values[0]), Port) or (Port < 1) or (Port > 65535) then
    begin
      MsgBox('Informe uma porta entre 1 e 65535.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function HostsPath(): String;
begin
  Result := ExpandConstant('{sys}\drivers\etc\hosts');
end;

function IsAliasLine(const Line: String): Boolean;
var
  Clean: String;
begin
  Clean := Trim(Line);
  { Uma linha comentada que cite o apelido não é o registro dele. }
  Result := (Clean <> '') and (Copy(Clean, 1, 1) <> '#') and (Pos('{#AppAlias}', Clean) > 0);
end;

function HostsHasAlias(): Boolean;
var
  Lines: TArrayOfString;
  I: Integer;
begin
  Result := False;
  if not LoadStringsFromFile(HostsPath(), Lines) then
    Exit;
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    if IsAliasLine(Lines[I]) then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

procedure AddAliasToHosts();
var
  Lines: TArrayOfString;
  Count: Integer;
begin
  if HostsHasAlias() then
    Exit;
  if not LoadStringsFromFile(HostsPath(), Lines) then
    SetArrayLength(Lines, 0);
  Count := GetArrayLength(Lines);
  SetArrayLength(Lines, Count + 1);
  Lines[Count] := '127.0.0.1 {#AppAlias}';
  if not SaveStringsToFile(HostsPath(), Lines, False) then
    MsgBox('Não foi possível registrar o apelido {#AppAlias} no arquivo hosts.' + #13#10 +
      'O painel responde pelo IP ou pelo nome do computador de qualquer forma.',
      mbError, MB_OK);
end;

procedure RemoveAliasFromHosts();
var
  Lines: TArrayOfString;
  Kept: TArrayOfString;
  I, KeptCount: Integer;
begin
  if not LoadStringsFromFile(HostsPath(), Lines) then
    Exit;
  SetArrayLength(Kept, GetArrayLength(Lines));
  KeptCount := 0;
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    if not IsAliasLine(Lines[I]) then
    begin
      Kept[KeptCount] := Lines[I];
      KeptCount := KeptCount + 1;
    end;
  end;
  SetArrayLength(Kept, KeptCount);
  SaveStringsToFile(HostsPath(), Kept, False);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  { O executável não pode ser substituído com o painel aberto. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#AppExe} /F', '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    AddAliasToHosts();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveAliasFromHosts();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { Quem instala precisa sair daqui sabendo qual endereço passar para a equipe. }
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption :=
      'O painel está instalado em ' + ExpandConstant('{app}') + '.' + #13#10 + #13#10 +
      'Neste computador, abra http://127.0.0.1:' + GetPort('') + #13#10 +
      'Na rede da planta, ' + PanelUrl() + ' ou o IP desta máquina.' + #13#10 + #13#10 +
      'Os dados (cadastro, senha, histórico e logotipo) ficam na pasta data, ao lado do ' +
      'executável, e continuam aí se o painel for desinstalado ou atualizado.';
end;

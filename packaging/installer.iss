#ifndef MyAppVersion
  #error MyAppVersion must be supplied by the build script
#endif
#define MyAppName "每日经营数据本地助手"
#define MyAppPublisher "CTJFyrdian"
#define MyAppExeName "DailyBusinessAgent.exe"
#define MyAppProcessName "DailyBusinessAgent"
#define MyPackageProgId "DailyBusinessAgent.ConnectionPackage"

[Setup]
AppId={{D911E479-4920-4F7F-857B-6BD88419BD73}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\CTJFyrdian\DailyBusinessAgentApp
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=DailyBusinessAgent-Setup-{#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
AllowCancelDuringInstall=no
CloseApplications=force
CloseApplicationsFilter=*.*
RestartApplications=no
SetupLogging=yes
ChangesAssociations=yes
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："; Flags: unchecked
Name: "autostart"; Description: "Windows 登录后自动启动并检查领星更新"; GroupDescription: "自动同步："; Flags: checkedonce

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\{#MyAppExeName}"

[Files]
Source: "..\dist\DailyBusinessAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{autodesktop}\导入每日经营连接包"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "把 .dba 文件拖到这里，或直接双击 .dba 文件"
Name: "{userstartup}\{#MyAppName} 后台同步"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--no-browser"; WorkingDir: "{app}"; Tasks: autostart

[Registry]
Root: HKCU; Subkey: "Software\Classes\.dba"; ValueType: string; ValueName: ""; ValueData: "{#MyPackageProgId}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\{#MyPackageProgId}"; ValueType: string; ValueName: ""; ValueData: "Daily Business Agent 私人连接包"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyPackageProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKCU; Subkey: "Software\Classes\{#MyPackageProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "安装完成后启动本地助手"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#MyAppExeName} /T /F >NUL 2>&1"; Flags: runhidden; RunOnceId: "StopAgent"

[Code]
var
  RuntimeBackupPrepared: Boolean;
  InstallSucceeded: Boolean;
  RuntimeBackupDir: String;

function PowerShellPath: String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

function QuotePowerShell(const Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, Chr(39), Chr(39) + Chr(39), True);
  Result := Chr(39) + Result + Chr(39);
end;

function RunPowerShell(const Script: String; var ExitCode: Integer): Boolean;
begin
  Result := Exec(
    PowerShellPath,
    '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + Script + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ExitCode
  );
end;

function WaitForPowerShellSuccess(const Script: String; Attempts: Integer): Boolean;
var
  Attempt: Integer;
  ExitCode: Integer;
begin
  Result := False;
  for Attempt := 1 to Attempts do
  begin
    ExitCode := -1;
    if RunPowerShell(Script, ExitCode) and (ExitCode = 0) then
    begin
      Result := True;
      Exit;
    end;
    Sleep(250);
  end;
end;

function StopAgentAndReleaseRuntime: String;
var
  ResultCode: Integer;
  ProcessCheck: String;
  PortCheck: String;
  RuntimeCheck: String;
  RuntimeFile: String;
begin
  Result := '';
  Exec(
    ExpandConstant('{cmd}'),
    '/C taskkill /IM {#MyAppExeName} /T /F >NUL 2>&1',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  );

  ProcessCheck :=
    'if (Get-Process -Name ''{#MyAppProcessName}'' -ErrorAction SilentlyContinue) ' +
    '{ exit 1 } else { exit 0 }';
  if not WaitForPowerShellSuccess(ProcessCheck, 40) then
  begin
    Result := '无法关闭正在运行的 Daily Business Agent。旧版本未被修改，请关闭程序后重试。';
    Exit;
  end;

  PortCheck :=
    '$busy=[Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().' +
    'GetActiveTcpListeners() | Where-Object { $_.Port -eq 8766 }; ' +
    'if ($busy) { exit 1 } else { exit 0 }';
  if not WaitForPowerShellSuccess(PortCheck, 40) then
  begin
    Result := '本地端口 8766 尚未释放。旧版本未被修改，请稍后重试。';
    Exit;
  end;

  RuntimeFile := ExpandConstant('{app}\_internal\base_library.zip');
  RuntimeCheck :=
    '$p=' + QuotePowerShell(RuntimeFile) + '; ' +
    'if (-not (Test-Path -LiteralPath $p)) { exit 0 }; ' +
    'try { $s=[IO.File]::Open($p,[IO.FileMode]::Open,[IO.FileAccess]::Read,' +
    '[IO.FileShare]::None); $s.Dispose(); exit 0 } catch { exit 1 }';
  if not WaitForPowerShellSuccess(RuntimeCheck, 40) then
  begin
    Result := 'Agent 运行文件仍被其他程序占用。安装尚未修改旧版本，请关闭占用程序后重试。';
    Exit;
  end;
end;

procedure RestoreRuntimeBackup;
var
  ResultCode: Integer;
  CurrentInternal: String;
  CurrentExe: String;
  CurrentVersion: String;
begin
  if not RuntimeBackupPrepared then
    Exit;

  Exec(
    ExpandConstant('{cmd}'),
    '/C taskkill /IM {#MyAppExeName} /T /F >NUL 2>&1',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  );
  Sleep(500);

  CurrentInternal := ExpandConstant('{app}\_internal');
  CurrentExe := ExpandConstant('{app}\{#MyAppExeName}');
  CurrentVersion := ExpandConstant('{app}\VERSION');

  DelTree(CurrentInternal, True, True, True);
  DeleteFile(CurrentExe);
  DeleteFile(CurrentVersion);

  if DirExists(RuntimeBackupDir + '\_internal') then
    RenameFile(RuntimeBackupDir + '\_internal', CurrentInternal);
  if FileExists(RuntimeBackupDir + '\{#MyAppExeName}') then
    RenameFile(RuntimeBackupDir + '\{#MyAppExeName}', CurrentExe);
  if FileExists(RuntimeBackupDir + '\VERSION') then
    RenameFile(RuntimeBackupDir + '\VERSION', CurrentVersion);

  DelTree(RuntimeBackupDir, True, True, True);
  RuntimeBackupPrepared := False;
  Log('Previous Agent runtime restored after incomplete upgrade.');
end;

function PrepareRuntimeBackup: Boolean;
var
  CurrentInternal: String;
  CurrentExe: String;
  CurrentVersion: String;
  MovedSomething: Boolean;
begin
  Result := False;
  RuntimeBackupPrepared := False;
  RuntimeBackupDir := ExpandConstant('{app}\.upgrade-backup');
  CurrentInternal := ExpandConstant('{app}\_internal');
  CurrentExe := ExpandConstant('{app}\{#MyAppExeName}');
  CurrentVersion := ExpandConstant('{app}\VERSION');

  if DirExists(RuntimeBackupDir) then
  begin
    if (not DirExists(CurrentInternal)) and DirExists(RuntimeBackupDir + '\_internal') then
      RenameFile(RuntimeBackupDir + '\_internal', CurrentInternal);
    if (not FileExists(CurrentExe)) and FileExists(RuntimeBackupDir + '\{#MyAppExeName}') then
      RenameFile(RuntimeBackupDir + '\{#MyAppExeName}', CurrentExe);
    if (not FileExists(CurrentVersion)) and FileExists(RuntimeBackupDir + '\VERSION') then
      RenameFile(RuntimeBackupDir + '\VERSION', CurrentVersion);
    if not DelTree(RuntimeBackupDir, True, True, True) then
      Exit;
  end;

  if not ForceDirectories(RuntimeBackupDir) then
    Exit;

  MovedSomething := False;
  if DirExists(CurrentInternal) then
  begin
    if not RenameFile(CurrentInternal, RuntimeBackupDir + '\_internal') then
      Exit;
    MovedSomething := True;
  end;

  if FileExists(CurrentExe) then
  begin
    if not RenameFile(CurrentExe, RuntimeBackupDir + '\{#MyAppExeName}') then
    begin
      RuntimeBackupPrepared := MovedSomething;
      RestoreRuntimeBackup;
      Exit;
    end;
    MovedSomething := True;
  end;

  if FileExists(CurrentVersion) then
  begin
    if not RenameFile(CurrentVersion, RuntimeBackupDir + '\VERSION') then
    begin
      RuntimeBackupPrepared := MovedSomething;
      RestoreRuntimeBackup;
      Exit;
    end;
    MovedSomething := True;
  end;

  RuntimeBackupPrepared := MovedSomething;
  if not MovedSomething then
    DelTree(RuntimeBackupDir, True, True, True);
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;
  Result := StopAgentAndReleaseRuntime;
  if Result <> '' then
    Exit;

  if not PrepareRuntimeBackup then
    Result := '无法建立安全升级回滚点。旧版本未被修改，请稍后重试。';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssDone then
  begin
    InstallSucceeded := True;
    if RuntimeBackupPrepared then
    begin
      DelTree(RuntimeBackupDir, True, True, True);
      RuntimeBackupPrepared := False;
    end;
  end;
end;

procedure DeinitializeSetup;
begin
  if RuntimeBackupPrepared and (not InstallSucceeded) then
    RestoreRuntimeBackup;
end;

; 用户数据保存在 %LOCALAPPDATA%\CTJFyrdian\DailyBusinessAgent，升级和卸载都不会删除。
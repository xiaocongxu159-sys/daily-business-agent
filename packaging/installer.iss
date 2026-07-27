#ifndef MyAppVersion
  #error MyAppVersion must be supplied by the build script
#endif
#define MyAppName "每日经营数据本地助手"
#define MyAppPublisher "CTJFyrdian"
#define MyAppExeName "DailyBusinessAgent.exe"
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
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
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
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#MyAppExeName} /F >NUL 2>&1"; Flags: runhidden; RunOnceId: "StopAgent"

; 用户数据保存在 %LOCALAPPDATA%\CTJFyrdian\DailyBusinessAgent，升级和卸载都不会删除。

#ifndef MyAppVersion
  #error MyAppVersion must be supplied by the build script
#endif
#define MyAppName "每日经营数据本地助手"
#define MyAppPublisher "CTJFyrdian"
#define MyAppExeName "DailyBusinessAgent.exe"

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
RestartApplications=no
SetupLogging=yes
ChangesAssociations=no
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："; Flags: unchecked
Name: "autostart"; Description: "Windows 登录后自动启动并检查领星更新"; GroupDescription: "自动同步："; Flags: checkedonce

[Files]
Source: "..\dist\DailyBusinessAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName} 后台同步"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--no-browser"; WorkingDir: "{app}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "安装完成后启动本地助手"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#MyAppExeName} /F >NUL 2>&1"; Flags: runhidden; RunOnceId: "StopAgent"

; 用户数据保存在 %LOCALAPPDATA%\CTJFyrdian\DailyBusinessAgent，卸载程序不会删除。

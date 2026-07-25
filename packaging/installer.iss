#define MyAppName "每日经营数据本地助手"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "xiaocongxu159-sys"
#define MyAppExeName "DailyBusinessAgent.exe"

[Setup]
AppId={{A02B2384-797D-4BF0-9012-CDDBB9894897}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\DailyBusinessAgent
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
Name: "startup"; Description: "Windows 登录后自动启动"; GroupDescription: "附加选项："; Flags: checkedonce

[Files]
Source: "..\dist\DailyBusinessAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "安装完成后启动本地助手"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#MyAppExeName} /F >NUL 2>&1"; Flags: runhidden; RunOnceId: "StopAgent"

; 用户数据保存在 %LOCALAPPDATA%\DailyBusinessAgent。
; 卸载程序只删除应用文件，不删除用户原始文件、任务历史、加密配置或生成结果。

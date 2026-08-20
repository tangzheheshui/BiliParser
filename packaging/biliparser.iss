; BiliParser Windows 安装包（Inno Setup 6）
; 前置：先跑 packaging/build-windows.sh 生成 dist/BiliParser/（onedir）
; 产物：dist/BiliParser-Setup-0.1.0.exe
; 说明：per-user 安装到 %LOCALAPPDATA%\Programs\BiliParser，无需管理员/UAC

#define MyAppName "BiliParser"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "BiliParser"
#define MyAppExeName "BiliParser.exe"

[Setup]
AppId={{8C3F4A1E-2D7B-4C6E-9A0F-5E7B2D1C9A4F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=BiliParser-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
; 有图标后取消下面注释（放在 packaging/ 下）
; SetupIconFile=..\packaging\biliparser.ico

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "..\dist\BiliParser\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

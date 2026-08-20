; BiliParser Windows 安装器（Inno Setup）
; 由 GitHub Actions（packaging 调 iscc）或本地 Windows 机器编译：
;   iscc packaging/biliparser.iss    （需先跑 pyinstaller 出 dist\BiliParser\）
; 产物：dist/BiliParser-Setup-Windows.exe

[Setup]
AppName=BiliParser
AppVersion=0.1.0
AppPublisher=BiliParser
DefaultDirName={autopf}\BiliParser
DefaultGroupName=BiliParser
UninstallDisplayName=BiliParser
OutputDir=dist
OutputBaseFilename=BiliParser-Setup-Windows
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
WizardStyle=modern

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "dist\BiliParser\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\BiliParser"; Filename: "{app}\BiliParser.exe"
Name: "{autodesktop}\BiliParser"; Filename: "{app}\BiliParser.exe"

[Run]
Filename: "{app}\BiliParser.exe"; Description: "启动 BiliParser"; Flags: nowait postinstall skipifsilent

; BiliParser Windows 安装器（Inno Setup）
; 由 GitHub Actions（packaging 调 iscc）或本地 Windows 机器编译：
;   iscc packaging/biliparser.iss    （需先跑 pyinstaller 出 dist\BiliParser\）
; 产物：dist/BiliParser-Setup-Windows.exe
; 注：刻意用最保守语法（无语言包覆盖、无架构指令）——Inno 7 的目录
; 布局变动曾导致编译失败（2026-08 实测踩坑），先保证能出包。

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

[Files]
Source: "dist\BiliParser\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\BiliParser"; Filename: "{app}\BiliParser.exe"
Name: "{autodesktop}\BiliParser"; Filename: "{app}\BiliParser.exe"

[Run]
Filename: "{app}\BiliParser.exe"; Description: "Launch BiliParser"; Flags: nowait postinstall skipifsilent

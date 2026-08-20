; BiliParser Windows 安装器（Inno Setup）
; 由 GitHub Actions（packaging 调 iscc）或本地 Windows 机器编译：
;   iscc packaging/biliparser.iss    （需先跑 pyinstaller 出 dist\BiliParser\）
; 产物：dist/BiliParser-Setup-Windows.exe
; 注意：ISCC 以本文件所在目录解析相对路径（实测踩坑：写成 dist\... 会
; 去找 packaging\dist\），故统一用 {#SourcePath} 锚定仓库根。

#define RepoRoot "{#SourcePath}\.."

[Setup]
AppName=BiliParser
AppVersion=0.1.0
AppPublisher=BiliParser
DefaultDirName={autopf}\BiliParser
DefaultGroupName=BiliParser
UninstallDisplayName=BiliParser
OutputDir={#RepoRoot}\dist
OutputBaseFilename=BiliParser-Setup-Windows
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
DisableProgramGroupPage=yes

[Files]
Source: "{#RepoRoot}\dist\BiliParser\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\BiliParser"; Filename: "{app}\BiliParser.exe"
Name: "{autodesktop}\BiliParser"; Filename: "{app}\BiliParser.exe"

[Run]
Filename: "{app}\BiliParser.exe"; Description: "Launch BiliParser"; Flags: nowait postinstall skipifsilent

; Inno Setup 打包脚本框架（M0 人工验收用）
; 安装 Inno Setup 后编译：ISCC.exe scripts/installer.iss
; 注意：M0 为框架，需在 W6 人工阶段按实际前端 dist 与后端打包方式调整

[Setup]
AppName=法律助手 Demo
AppVersion=0.1.0
DefaultDirName={autopf}\LegalAssistantDemo
DefaultGroupName=法律助手 Demo
OutputDir=..\dist
OutputBaseFilename=LegalAssistantDemo-Setup-0.1.0
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible

[Files]
; 后端代码与前端构建产物（人工阶段确认目录后启用）
; Source: "..\server\*"; DestDir: "{app}\server"; Flags: recursesubdirs
; Source: "..\frontend\dist\*"; DestDir: "{app}\frontend\dist"; Flags: recursesubdirs
; Source: "..\.venv\*"; DestDir: "{app}\.venv"; Flags: recursesubdirs
; Source: "..\data\indices\法律\qdrant\*"; DestDir: "{app}\data\indices\法律\qdrant"; Flags: recursesubdirs

[Icons]
Name: "{group}\法律助手 Demo"; Filename: "{app}\start_all.bat"
Name: "{group}\停止服务"; Filename: "{app}\stop_all.bat"

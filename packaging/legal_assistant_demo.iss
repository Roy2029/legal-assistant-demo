; Inno Setup 6 打包脚本（草案）
; 编译前请确认：
; 1) 已执行 frontend\npm run build 生成 frontend\dist
; 2) 已把 bge-base-zh 模型复制到 项目根\models\bge-base-zh
; 3) 目标机已安装 Python 3.12（安装包不含 Python/wheelhouse）

#define MyAppName "法律助手 Demo"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "Legal Assistant Demo"
#define MyAppExeName "start_legal_assistant.bat"

[Setup]
AppId={{8A4F2C1E-6F2D-4E5A-9B7C-1D3E5F7A9B2C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\LegalAssistantDemo
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=LegalAssistantDemo_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
; 后端与核心代码（运行时必需）
Source: "..\server\*"; DestDir: "{app}\server"; Flags: recursesubdirs createallsubdirs
Source: "..\online_core\*"; DestDir: "{app}\online_core"; Flags: recursesubdirs createallsubdirs
Source: "..\offline_core\*"; DestDir: "{app}\offline_core"; Flags: recursesubdirs createallsubdirs
Source: "..\utils\*"; DestDir: "{app}\utils"; Flags: recursesubdirs createallsubdirs
Source: "..\skills\*"; DestDir: "{app}\skills"; Flags: recursesubdirs createallsubdirs
Source: "..\prompts\*"; DestDir: "{app}\prompts"; Flags: recursesubdirs createallsubdirs

; 前端构建产物（不包含 node_modules 与源码）
Source: "..\frontend\dist\*"; DestDir: "{app}\frontend\dist"; Flags: recursesubdirs createallsubdirs

; 本地 embedding 模型（必打包）
Source: "..\models\bge-base-zh\*"; DestDir: "{app}\models\bge-base-zh"; Flags: recursesubdirs createallsubdirs

; 法律库索引（运行时检索必需）
Source: "..\data\indices\法律\qdrant\*"; DestDir: "{app}\data\indices\法律\qdrant"; Flags: recursesubdirs createallsubdirs
Source: "..\data\indices\法律\manifest.json"; DestDir: "{app}\data\indices\法律"; Flags: ignoreversion

; 安装/启动脚本
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\install_deps.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\start_legal_assistant.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\scripts\start_prod.py"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\scripts\run_rebuild_managed.py"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\scripts\rebuild_index_from_intermediate.py"; DestDir: "{app}\scripts"; Flags: ignoreversion

; 明确不打包：data\sqlite.db / data\config.json / data\logs / data\uploads /
; data\agent_workspace / data\contracts / data\keys / data\anonymization /
; data\indices\法律\qdrant_old_* / chunk_v2_intermediate / chunks_v2.jsonl

[Dirs]
Name: "{app}\data\logs"
Name: "{app}\data\uploads"
Name: "{app}\data\parsed"
Name: "{app}\data\agent_workspace"
Name: "{app}\data\contracts"
Name: "{app}\data\keys"
Name: "{app}\data\anonymization"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\安装依赖"; Filename: "{app}\install_deps.bat"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: postinstall nowait skipifsilent

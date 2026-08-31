; Inno Setup 6 打包脚本（v0.3.0）
; 编译前请确认：
; 1) 已执行 frontend\npm run build 生成 frontend\dist
; 2) 已把 bge-base-zh 模型复制到 项目根\models\bge-base-zh
; 3) 目标机已安装 Python 3.12（安装包不含 Python/wheelhouse）
; 4) 案例检索内核已随包内置（vendor\wenshumcp，v0.3.1 起）；替换用
;    WENSHU_MCP_PROJECT 指向其他 WenshuMCP 工作副本

#define MyAppName "法律助手 Demo"
#define MyAppVersion "0.3.1"
#define MyAppPublisher "Legal Assistant Demo"
#define MyAppExeName "start_legal_assistant.bat"

[Setup]
AppId={{8A4F2C1E-6F2D-4E5A-9B7C-1D3E5F7A9B2C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; per-user 安装到 LocalAppData：主应用以 PROJECT_ROOT 相对定位，data/、.venv、
; MCP 临时目录均需安装目录整体可写，故不用 Program Files，也无需管理员权限
DefaultDirName={localappdata}\LegalAssistantDemo
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=LegalAssistantDemo_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Files]
; 后端与核心代码（运行时必需）
Source: "..\server\*"; DestDir: "{app}\server"; Flags: recursesubdirs createallsubdirs
Source: "..\online_core\*"; DestDir: "{app}\online_core"; Flags: recursesubdirs createallsubdirs
Source: "..\offline_core\*"; DestDir: "{app}\offline_core"; Flags: recursesubdirs createallsubdirs
Source: "..\skills\*"; DestDir: "{app}\skills"; Flags: recursesubdirs createallsubdirs
Source: "..\prompts\*"; DestDir: "{app}\prompts"; Flags: recursesubdirs createallsubdirs

; 随包内置的裁判文书检索 MCP 内核（Case Agent 强依赖）
Source: "..\vendor\wenshumcp\*"; DestDir: "{app}\vendor\wenshumcp"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs createallsubdirs

; 前端构建产物（不包含 node_modules 与源码）
Source: "..\frontend\dist\*"; DestDir: "{app}\frontend\dist"; Flags: recursesubdirs createallsubdirs

; 本地 embedding 模型（必打包）
Source: "..\models\bge-base-zh\*"; DestDir: "{app}\models\bge-base-zh"; Flags: recursesubdirs createallsubdirs

; 法律库索引（运行时检索必需；排除运行期锁文件）
Source: "..\data\indices\法律\qdrant\*"; DestDir: "{app}\data\indices\法律\qdrant"; Excludes: "*.lock"; Flags: recursesubdirs createallsubdirs
Source: "..\data\indices\法律\manifest.json"; DestDir: "{app}\data\indices\法律"; Flags: ignoreversion

; 安装/启动脚本
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\install_deps.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\start_legal_assistant.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\scripts\start_prod.py"; DestDir: "{app}\scripts"; Flags: ignoreversion

; 明确不打包：data\sqlite.db / data\config.json / data\logs(含 traces) / data\uploads /
; data\agent_workspace / data\contracts / data\keys / data\anonymization /
; data\indices\法律\qdrant_old_* / chunk_v2_intermediate / chunks_v2.jsonl（重建已下线）
; Wenshu/LLM 凭据（.env / data\config.json / ~\.wenshu 会话快照）

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

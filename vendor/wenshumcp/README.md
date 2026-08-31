# vendor/wenshumcp

随包分发的裁判文书检索 MCP 内核（Case Agent 强依赖）。

- **来源**：WenshuMCP 工作副本（默认 `C:/Users/Roy/WorkBuddy/WenshuMCP`，可 `WENSHU_MCP_PROJECT` 覆盖）
- **内容**：`wenshu_mcp/`（FastMCP server + 会话编排）+ `wenshu_api/`（协议内核、`algo_config.json` 出厂默认值）
- **排除**：`__pycache__`、`_tmp_dl`（临时下载）、`captcha_samples`（验证码样本）等运行态产物
- **版本追踪**：见 `VENDOR.json`（源目录非 git 仓库，以内容指纹为准）
- **刷新**：`python packaging/vendor_wenshumcp.py`（重新同步并更新指纹）

## 运行时说明

- MCP 子进程由 `online_core/mcp/wenshu_adapter.py` 以 `python -m wenshu_mcp.server` 启动，cwd 指向本目录；解释器默认与应用同环境（依赖已入 requirements.txt）
- 项目位置解析顺序：`WENSHU_MCP_PROJECT` 环境变量 → 本目录 → 开发机工作副本（历史默认）
- `algo_config.json` 的 `page_id` 随站点发布轮换：运行时可通过 `WENSHU_ALGO_CONFIG` 环境变量指向替换文件（优先级高于本目录出厂值），**无需重装/重打包**
- 会话快照（免重复过验证码）落在 `~/.wenshu/browser_session.json`，可用 `WENSHU_SESSION_STATE` 覆盖；含登录态，属敏感数据，不入安装包

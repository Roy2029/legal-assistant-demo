"""rag-eval CLI — RAG 检索评估命令行工具。

命令:
    init <name>         创建实验目录和模板 YAML
    list                列出所有实验及状态
    show <name>         显示实验配置和 Run 状态
    config <name>       在编辑器中打开 experiment.yaml
    run <name>          执行实验
    report <name>       生成报告 (summary.md + comparison.json + per_query.csv)
    table <name>        打印 ASCII 对比表
    dashboard <name>    启动 Streamlit 仪表板（后续实现）
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import click

from evaluation.config import ExperimentConfig

EXPERIMENTS_DIR = Path("experiments")

_LOGGING_SETUP = False


def _ensure_logging() -> None:
    """确保库代码的 logger 有 handler，避免日志被静默吞掉。"""
    global _LOGGING_SETUP
    if not _LOGGING_SETUP:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        _LOGGING_SETUP = True


# ══════════════════════════════════════════════════════════════════════════════
# 命令组
# ══════════════════════════════════════════════════════════════════════════════


@click.group()
@click.version_option(version="0.1.0", prog_name="rag-eval")
def cli() -> None:
    """rag-eval — RAG 检索评估框架。

    管理实验、执行检索评估、生成报告和对比表格。
    """
    _ensure_logging()


# ══════════════════════════════════════════════════════════════════════════════
# init
# ══════════════════════════════════════════════════════════════════════════════


@cli.command("init")
@click.argument("name")
@click.option("--template", default=None, help="自定义 Jinja2 模板路径")
def init_cmd(name: str, template: str | None) -> None:
    """创建实验目录和模板 YAML。

    NAME: 实验名称，也是 experiments/ 下的目录名。
    """
    exp_dir = EXPERIMENTS_DIR / name

    if exp_dir.exists():
        click.echo(f"错误: 实验目录已存在: {exp_dir}", err=True)
        sys.exit(1)

    # 创建目录结构
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "results").mkdir(exist_ok=True)
    (exp_dir / "reports").mkdir(exist_ok=True)
    (exp_dir / "cache").mkdir(exist_ok=True)

    # 尝试从 Jinja2 模板生成 YAML
    try:
        content = _render_template(name, template)
    except ImportError:
        content = _default_yaml(name)
        click.echo("提示: 未安装 Jinja2，使用内置默认模板")

    yaml_path = exp_dir / "experiment.yaml"
    yaml_path.write_text(content, encoding="utf-8")
    click.echo(f"✓ 实验 '{name}' 已创建: {exp_dir}")
    click.echo(f"  配置文件: {yaml_path}")
    click.echo(f"  下一步: 编辑 dataset/index 路径，然后运行 'rag-eval run {name}'")


def _render_template(name: str, template_path: str | None) -> str:
    """使用 Jinja2 渲染模板。"""
    from jinja2 import Template

    if template_path:
        tmpl = Template(Path(template_path).read_text(encoding="utf-8"))
    else:
        default_tmpl_path = Path(__file__).parent / "templates" / "experiment.yaml.j2"
        if default_tmpl_path.exists():
            tmpl = Template(default_tmpl_path.read_text(encoding="utf-8"))
        else:
            return _default_yaml(name)

    return tmpl.render(name=name)


def _default_yaml(name: str) -> str:
    """内置默认 YAML 内容。"""
    return f"""# RAG 检索评估实验配置
# 实验名称: {name}

name: "{name}"
description: ""

# 数据集
dataset:
  queries_path: "data/test_10docs_output/queries.json"
  qrels_path: "data/test_10docs_output/qrels.json"

# 索引
index:
  path: "./qdrant_data"
  db_name: "default"

# Pipeline 基线配置（各 Run 默认继承）
pipeline:
  prefilter:
    enabled: false
  router:
    enabled: false
  recall:
    mode: "hybrid"           # dense | sparse | hybrid
    top_k: 20
    fusion: "rrf"            # rrf | dbsf
  rerank:
    enabled: false
    model_path: "local_model/bge-reranker-v2-m3"
    top_k: 10
    device: "cuda"
    batch_size: 32

# 指标配置
metrics:
  ks: [1, 3, 5, 10, 20]
  group_by: ["query_type", "difficulty"]

# 参数化 Run 列表
runs:
  - name: "dense_only"
    description: "纯 dense 检索（基线）"
    pipeline:
      recall:
        mode: "dense"
        top_k: 20

  - name: "hybrid_only"
    description: "纯 hybrid 检索"
    pipeline:
      recall:
        mode: "hybrid"
        top_k: 20

  - name: "hybrid_rerank"
    description: "Hybrid + CrossEncoder 精排"
    pipeline:
      recall:
        mode: "hybrid"
        top_k: 20
      rerank:
        enabled: true
        top_k: 10
"""


# ══════════════════════════════════════════════════════════════════════════════
# list
# ══════════════════════════════════════════════════════════════════════════════


@cli.command("list")
def list_cmd() -> None:
    """列出所有实验及其状态（从注册表读取）。"""
    from evaluation.registry import ExperimentsRegistry

    registry = ExperimentsRegistry()
    experiments = registry.list_experiments()

    if not experiments:
        # Fallback: 扫描目录
        if not EXPERIMENTS_DIR.exists():
            click.echo("暂无实验（experiments/ 目录不存在）")
            return

        dirs = sorted([d for d in EXPERIMENTS_DIR.iterdir() if d.is_dir()])
        if not dirs:
            click.echo("暂无实验")
            return

        click.echo("注册表为空，以下是根据目录结构检测到的实验：")
        click.echo(f"{'实验名称':<30} {'状态':<12}")
        click.echo("-" * 42)
        for d in dirs:
            yaml_path = d / "experiment.yaml"
            if yaml_path.exists():
                click.echo(f"{d.name:<30} {'○ 未注册':<12}")
        click.echo()
        click.echo("提示: 运行 'rag-eval run <name>' 后会自动注册到数据库")
        return

    status_icon = {
        "pending": "○", "running": "▶",
        "completed": "✓", "error": "✗",
    }

    click.echo(f"{'':<2} {'实验名称':<24} {'状态':<12} {'Run 数':<8} {'总 query':<10} {'耗时':<12}")
    click.echo("-" * 68)
    for exp in experiments:
        icon = status_icon.get(exp.get("status", ""), "?")
        name = exp.get("name", "?")
        status = exp.get("status", "?")
        dur = _fmt_duration(exp.get("duration_seconds"))
        queries = exp.get("num_queries", 0)

        cursor = registry.conn.execute(
            "SELECT COUNT(*) FROM runs WHERE experiment_id = ?", (exp["id"],)
        )
        run_count = cursor.fetchone()[0]

        click.echo(
            f" {icon} {name:<22} {status:<12} "
            f"{run_count:<8} {queries:<10} {dur:<12}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# show
# ══════════════════════════════════════════════════════════════════════════════


@cli.command("show")
@click.argument("name")
def show_cmd(name: str) -> None:
    """显示实验配置和 Run 完成状态。

    NAME: 实验名称。
    """
    yaml_path = EXPERIMENTS_DIR / name / "experiment.yaml"
    if not yaml_path.exists():
        click.echo(f"错误: 实验 '{name}' 不存在", err=True)
        sys.exit(1)

    config = ExperimentConfig.from_yaml(yaml_path)
    click.echo(config.summary())

    # Run 状态
    results_dir = EXPERIMENTS_DIR / name / "results"
    if results_dir.exists():
        completed = {f.stem for f in results_dir.glob("*.json")}
        click.echo("\nRun 执行状态:")
        for run in config.runs:
            status = "✓ 已完成" if run.name in completed else "○ 未执行"
            click.echo(f"  [{run.name}] {status}")
    else:
        click.echo("\n结果目录不存在，尚未执行任何 Run")


# ══════════════════════════════════════════════════════════════════════════════
# config
# ══════════════════════════════════════════════════════════════════════════════


@cli.command("config")
@click.argument("name")
def config_cmd(name: str) -> None:
    """在编辑器中打开 experiment.yaml。

    NAME: 实验名称。
    """
    yaml_path = EXPERIMENTS_DIR / name / "experiment.yaml"
    if not yaml_path.exists():
        click.echo(f"错误: 实验 '{name}' 不存在", err=True)
        sys.exit(1)

    click.edit(filename=str(yaml_path))


# ══════════════════════════════════════════════════════════════════════════════
# status
# ══════════════════════════════════════════════════════════════════════════════


@cli.command("status")
@click.argument("name")
@click.argument("run_name", required=False)
def status_cmd(name: str, run_name: str | None) -> None:
    """显示实验的 Run 执行状态和进度。

    NAME: 实验名称。
    RUN_NAME: 可选的 Run 名称，指定后显示单个 Run 详情。
    """
    from evaluation.registry import ExperimentsRegistry

    registry = ExperimentsRegistry()
    experiment = registry.get_experiment_with_runs(name)

    if experiment is None:
        click.echo(f"错误: 实验 '{name}' 在注册表中不存在", err=True)
        click.echo("提示: 先运行 'rag-eval run' 执行实验，会自动注册")
        sys.exit(1)

    # 单 Run 详情
    if run_name:
        run = registry.get_run_status(name, run_name)
        if run is None:
            click.echo(f"错误: Run '{run_name}' 在实验 '{name}' 中不存在", err=True)
            sys.exit(1)
        _display_run_detail(run)
        return

    # 实验概要
    exp = experiment
    status_icons = {
        "pending": "○",
        "running": "▶",
        "completed": "✓",
        "error": "✗",
    }
    exp_icon = status_icons.get(exp.get("status", ""), "?")

    click.echo(f"\n{'='*60}")
    click.echo(f" {exp_icon} 实验: {exp.get('name', name)}")
    click.echo(f"   描述: {exp.get('description', '')}")
    click.echo(f"   状态: {exp.get('status', 'unknown')}")
    click.echo(f"   数据集: {exp.get('dataset_queries', '-')}")
    click.echo(f"   索引: {exp.get('index_path', '')}/{exp.get('index_db_name', '')}")
    click.echo(f"   启动时间: {exp.get('started_at', '-')}")
    click.echo(f"   完成时间: {exp.get('completed_at', '-')}")
    if exp.get("duration_seconds"):
        click.echo(f"   总耗时: {_fmt_duration(exp['duration_seconds'])}")
    click.echo(f"   模型: {exp.get('embedding_model', '-')} / LLM={exp.get('llm_model', '-')}")
    click.echo(f"   代码版本: {exp.get('code_version', '-')}")
    click.echo()

    # Run 列表
    runs = experiment.get("runs", [])
    if not runs:
        click.echo("   尚未注册任何 Run")
        return

    click.echo(f"   Run 列表 ({len(runs)} 个):")
    click.echo(f"   {'名称':<22} {'状态':<12} {'进度':<10} {'缓存':<14} {'耗时':<10}")
    click.echo(f"   {'-'*70}")
    for run_rec in runs:
        icon = status_icons.get(run_rec.get("status", ""), "?")
        name_str = run_rec.get("run_name", "?")
        status_str = run_rec.get("status", "?")
        progress = f"{run_rec.get('num_queries', 0)}/{run_rec.get('num_queries_total', '?')}"
        cache_str = f"H:{run_rec.get('cache_hits', 0)} M:{run_rec.get('cache_misses', 0)}"
        dur = _fmt_duration(run_rec.get("duration_seconds", 0))
        click.echo(f"   {icon} {name_str:<20} {status_str:<12} {progress:<10} {cache_str:<14} {dur:<10}")

    click.echo()


def _display_run_detail(run: dict) -> None:
    """显示单个 Run 的详细信息。"""
    status_icon = {
        "pending": "○", "running": "▶",
        "completed": "✓", "error": "✗",
    }.get(run.get("status", ""), "?")

    click.echo(f"\n{'='*60}")
    click.echo(f" {status_icon} Run: {run.get('run_name', '?')}")
    click.echo(f"   描述: {run.get('run_description', '-')}")
    click.echo(f"   状态: {run.get('status', 'unknown')}")
    click.echo(f"   启动时间: {run.get('started_at', '-')}")
    click.echo(f"   完成时间: {run.get('completed_at', '-')}")
    if run.get("duration_seconds"):
        click.echo(f"   耗时: {_fmt_duration(run['duration_seconds'])}")
    click.echo(f"   进度: {run.get('num_queries', 0)}/{run.get('num_queries_total', '?')}")
    pct = run.get("progress_pct", 0)
    bar_len = 30
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    click.echo(f"   进度: [{bar}] {pct:.1f}%")
    click.echo(f"   缓存: {run.get('cache_hits', 0)} hits / {run.get('cache_misses', 0)} misses")
    if run.get("error_message"):
        click.echo(f"   错误: {run['error_message']}")

    # 聚合指标
    summary_json = run.get("summary_json")
    if summary_json:
        try:
            import json
            summary = json.loads(summary_json) if isinstance(summary_json, str) else summary_json
            ks_keys = [k for k in summary if k.startswith(("Recall@", "Precision@"))]
            if ks_keys:
                click.echo(f"\n   聚合指标 (部分):")
                for k in ks_keys[:3]:
                    v = summary[k]
                    if isinstance(v, dict):
                        click.echo(f"     {k}: mean={v.get('mean', '-'):.4f}")
                click.echo(f"     ... (共 {len(ks_keys)} 个 K 指标)")
            other_keys = ["MRR", "NDCG@10", "AP"]
            for k in other_keys:
                if k in summary:
                    v = summary[k]
                    if isinstance(v, dict):
                        click.echo(f"     {k}: mean={v.get('mean', '-'):.4f}")
        except Exception:
            pass

    click.echo()


def _fmt_duration(seconds: float | None) -> str:
    """将秒格式化为可读字符串。"""
    if seconds is None or seconds <= 0:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}min"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h{mins}min"


# ══════════════════════════════════════════════════════════════════════════════
# history
# ══════════════════════════════════════════════════════════════════════════════


@cli.command("history")
@click.option("--json", "json_output", is_flag=True, default=False,
              help="JSON 格式输出")
@click.option("--name", "name_filter", default=None,
              help="按实验名称过滤")
def history_cmd(json_output: bool, name_filter: str | None) -> None:
    """列出所有实验的注册记录。"""
    from evaluation.registry import ExperimentsRegistry

    registry = ExperimentsRegistry()
    experiments = registry.list_experiments(name_filter=name_filter)

    if not experiments:
        click.echo("暂无实验记录（experiments.db 为空）")
        click.echo("提示: 运行 'rag-eval run <name>' 后会自动注册")
        return

    if json_output:
        import json as _json
        click.echo(_json.dumps(experiments, ensure_ascii=False, indent=2))
        return

    status_icon = {
        "pending": "○", "running": "▶",
        "completed": "✓", "error": "✗",
    }

    click.echo(f"\n{'='*60}")
    click.echo(f" 实验历史 ({len(experiments)} 条)")
    click.echo(f" {'名称':<22} {'状态':<10} {'Run 数':<8} {'有效 query':<11} {'耗时':<12} {'时间'}")
    click.echo(f" {'-'*80}")
    for exp in experiments:
        icon = status_icon.get(exp.get("status", ""), "?")
        name = exp.get("name", "?")
        status = exp.get("status", "?")
        dur = _fmt_duration(exp.get("duration_seconds"))
        queries = exp.get("num_queries", 0)
        created = exp.get("started_at", "")[:16] if exp.get("started_at") else "-"

        # Count runs
        cursor = registry.conn.execute(
            "SELECT COUNT(*) FROM runs WHERE experiment_id = ?", (exp["id"],)
        )
        run_count = cursor.fetchone()[0]

        click.echo(
            f" {icon} {name:<20} {status:<10} {run_count:<8} "
            f"{queries:<11} {dur:<12} {created}"
        )
    click.echo()


# ══════════════════════════════════════════════════════════════════════════════
# run
# ══════════════════════════════════════════════════════════════════════════════


@cli.command("run")
@click.argument("name")
@click.option(
    "--only",
    "only_runs",
    default=None,
    help="执行指定的 Run（逗号分隔），如 'dense_only,hybrid_only'",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="仅验证配置并打印计划，不实际执行",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="禁用检索缓存，强制重新执行所有检索",
)
@click.option(
    "--model-path",
    default="local_model/bge-base-zh",
    help="Embedding 模型路径（默认: local_model/bge-base-zh）",
    envvar="RAG_EVAL_MODEL_PATH",
)
@click.option(
    "--device",
    default="cuda",
    help="推理设备: cpu | cuda（默认: cuda）",
)
@click.option(
    "--llm-model",
    default=None,
    help="Router LLM 模型名称（如 deepseek-v4-flash）",
    envvar="RAG_EVAL_LLM_MODEL",
)
@click.option(
    "--llm-api-key",
    default=None,
    help="Router LLM API Key（也可通过环境变量 DS_API_KEY 设置）",
    envvar="RAG_EVAL_LLM_API_KEY",
)
@click.option(
    "--llm-base-url",
    default="https://api.deepseek.com/v1",
    help="Router LLM Base URL（默认: DeepSeek API）",
    envvar="RAG_EVAL_LLM_BASE_URL",
)
@click.option(
    "--kb-vocab-path",
    default=None,
    help="PreFilter 词表路径（默认使用内置路径）",
)
def run_cmd(
    name: str,
    only_runs: str | None,
    dry_run: bool,
    no_cache: bool,
    model_path: str,
    device: str,
    llm_model: str | None,
    llm_api_key: str | None,
    llm_base_url: str | None,
    kb_vocab_path: str | None,
) -> None:
    """执行实验的全部或指定 Run。

    NAME: 实验名称。

    启用 Router 的 Run 需要 LLM，可通过 --llm-model 指定。
    API Key 可从环境变量（如 DS_API_KEY）自动读取。
    """
    yaml_path = EXPERIMENTS_DIR / name / "experiment.yaml"
    if not yaml_path.exists():
        click.echo(f"错误: 实验 '{name}' 不存在", err=True)
        sys.exit(1)

    # 加载配置（含路径校验）
    try:
        config = ExperimentConfig.from_yaml(yaml_path, validate_paths=True)
    except Exception as e:
        click.echo(f"配置加载失败: {e}", err=True)
        sys.exit(1)

    # 解析 --only
    run_names = None
    if only_runs:
        run_names = [r.strip() for r in only_runs.split(",") if r.strip()]

    # 加载 Embedding 模型
    from offline_core.embedder import HuggingFaceEmbeddingModel

    try:
        click.echo(f"加载 Embedding 模型: {model_path} (device={device})")
        embedder = HuggingFaceEmbeddingModel(model_path, device=device)
    except Exception as e:
        click.echo(f"Embedding 模型加载失败: {e}", err=True)
        sys.exit(1)

    # LLM 初始化（Router 启用时需要）
    llm = None
    fallback_llm = None
    needs_llm = any(r.pipeline.router.enabled for r in config.runs)
    if needs_llm:
        if llm_model:
            click.echo(f"初始化 Router LLM: {llm_model}")
            llm = _init_router_llm(llm_model, llm_api_key, llm_base_url)
            # 默认 fallback: qwen3.5-flash (阿里云 DashScope)
            fallback_llm = _init_fallback_llm()
            if fallback_llm:
                click.echo(f"  备用 Router LLM: qwen3.5-flash")
        else:
            click.echo(
                "⚠️  检测到 router=true 的 Run，但未指定 --llm-model。\n"
                "   Router 将被禁用，所有 Run 使用 fallback (simple strategy)。\n"
                "   如需启用 Router，请指定: --llm-model deepseek-v4-flash"
            )

    # 创建 Runner 并执行
    from evaluation.runner import ExperimentRunner

    exp_dir = EXPERIMENTS_DIR / name
    runner = ExperimentRunner(
        config, embedder, llm=llm, fallback_llm=fallback_llm,
        exp_dir=exp_dir, kb_vocab_path=kb_vocab_path,
    )

    results = runner.run(
        run_names=run_names,
        dry_run=dry_run,
        use_cache=not no_cache,
    )

    if not dry_run and results:
        # 自动生成报告
        click.echo(f"\n所有 Run 执行完毕，生成报告...")
        from evaluation.reporter import Reporter
        reporter = Reporter(config, exp_dir)
        reporter.generate_all()
        click.echo(f"报告已保存: {exp_dir / 'reports'}")


def _init_router_llm(
    model: str,
    api_key: str | None = None,
    base_url: str = "https://api.deepseek.com/v1",
) -> object:
    """初始化 Router LLM 实例。

    支持从环境变量自动读取 API Key:
      - DS_API_KEY (DeepSeek)
      - OPENAI_API_KEY (OpenAI 兼容)

    Args:
        model: 模型名称
        api_key: API Key（None 时从环境变量读取）
        base_url: API Base URL

    Returns:
        OpenAI_LLM 实例

    Raises:
        click.ClickException: 无法找到有效的 API Key
    """
    import os

    # 确保 .env 文件中的变量已加载
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # API Key 优先级：参数 > 环境变量
    if not api_key:
        for env_var in ("DS_API_KEY", "OPENAI_API_KEY"):
            api_key = os.getenv(env_var)
            if api_key:
                click.echo(f"  使用环境变量 {env_var} 的 API Key")
                break

    if not api_key:
        raise click.ClickException(
            "未找到 Router LLM 的 API Key。请通过 --llm-api-key 指定，"
            "或设置环境变量 DS_API_KEY / OPENAI_API_KEY"
        )

    # 构造 LLM 配置
    llm_config = {
        "apikey": "",          # 直接传 key，不通过 env var 名
        "baseurl": base_url,
        "model": model,
    }

    import logging
    from online_core.llm import OpenAI_LLM

    llm_logger = logging.getLogger("router_llm")

    # OpenAI_LLM.__init__ 从 os.getenv(config['apikey']) 读 key
    # 如果 apikey 为空字符串，则 os.getenv('') 返回 None → 报错
    # 所以需要直接把 key 放入环境变量或直接修改逻辑
    # 方案：直接用客户端，绕过 OpenAI_LLM 的 env var 机制
    from openai import OpenAI

    class DirectLLM:
        """直接使用 OpenAI 客户端，避免 env var 查找问题。"""

        def __init__(self, model: str, api_key: str, base_url: str,
                     logger_obj: logging.Logger):
            self.model = model
            self.logger = logger_obj
            self.client = OpenAI(api_key=api_key, base_url=base_url)
            self.logger.info("Router LLM 已初始化: model=%s, base_url=%s",
                           model, base_url)

        def generate(self, messages: list, response_format=None):
            """调用 LLM（带超时和重试由上层 QueryRouter 处理）。"""
            import time as _time
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=False,
                response_format=response_format,
                timeout=120,
            )
            self.logger.debug("Router LLM response: model=%s", self.model)
            return resp

    return DirectLLM(model, api_key, base_url, llm_logger)


def _init_fallback_llm() -> object | None:
    """初始化备用 Router LLM (qwen3.5-flash)。

    从环境变量 ALI_API_KEY 读取阿里云 API Key。

    Returns:
        DirectLLM 实例，如果无法初始化则返回 None
    """
    import os
    import logging

    api_key = os.getenv("ALI_API_KEY")
    if not api_key:
        click.echo("  未找到 ALI_API_KEY，跳过备用 LLM 初始化")
        return None

    try:
        from openai import OpenAI
    except ImportError:
        click.echo("  未安装 openai 库，跳过备用 LLM 初始化")
        return None

    llm_logger = logging.getLogger("router_fallback_llm")

    class DirectLLM:
        def __init__(self, model, api_key, base_url, logger_obj):
            self.model = model
            self.logger = logger_obj
            self.client = OpenAI(api_key=api_key, base_url=base_url)
            self.logger.info("Fallback LLM 已初始化: model=%s, base_url=%s",
                           model, base_url)

        def generate(self, messages, response_format=None):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=False,
                response_format=response_format,
                timeout=60,
            )
            self.logger.debug("Fallback LLM response: model=%s", self.model)
            return resp

    return DirectLLM(
        "qwen3.5-flash",
        api_key,
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        llm_logger,
    )


# ══════════════════════════════════════════════════════════════════════════════
# report
# ══════════════════════════════════════════════════════════════════════════════


@cli.command("report")
@click.argument("name")
def report_cmd(name: str) -> None:
    """生成报告: summary.md + comparison.json + per_query.csv。

    NAME: 实验名称。
    """
    yaml_path = EXPERIMENTS_DIR / name / "experiment.yaml"
    if not yaml_path.exists():
        click.echo(f"错误: 实验 '{name}' 不存在", err=True)
        sys.exit(1)

    config = ExperimentConfig.from_yaml(yaml_path)
    exp_dir = EXPERIMENTS_DIR / name
    from evaluation.reporter import Reporter

    reporter = Reporter(config, exp_dir)
    outputs = reporter.generate_all()

    click.echo(f"报告已生成 ({len(outputs)} 个文件):")
    for fname, fpath in outputs.items():
        click.echo(f"  • {fname}: {fpath}")


# ══════════════════════════════════════════════════════════════════════════════
# table
# ══════════════════════════════════════════════════════════════════════════════


@cli.command("table")
@click.argument("name")
@click.option(
    "--metric",
    default=None,
    help="仅显示指定指标，如 'Recall@10'",
)
@click.option(
    "--by",
    "group_by",
    default=None,
    help="按维度分组，如 'query_type'",
)
def table_cmd(name: str, metric: str | None, group_by: str | None) -> None:
    """打印 ASCII 对比表。

    NAME: 实验名称。
    """
    yaml_path = EXPERIMENTS_DIR / name / "experiment.yaml"
    if not yaml_path.exists():
        click.echo(f"错误: 实验 '{name}' 不存在", err=True)
        sys.exit(1)

    config = ExperimentConfig.from_yaml(yaml_path)
    exp_dir = EXPERIMENTS_DIR / name
    from evaluation.reporter import Reporter

    reporter = Reporter(config, exp_dir)
    table = reporter.print_ascii_table(metric=metric, group_by=group_by)
    click.echo(table)


# ══════════════════════════════════════════════════════════════════════════════
# dashboard (占位)
# ══════════════════════════════════════════════════════════════════════════════


@cli.command("dashboard")
@click.argument("name")
def dashboard_cmd(name: str) -> None:
    """启动 Streamlit 交互仪表板（占位，后续实现）。

    NAME: 实验名称。
    """
    click.echo("Dashboard 功能尚未实现，将在后续版本中添加。")
    click.echo("预计功能：")
    click.echo("  • 交互式指标对比图表")
    click.echo("  • Run 配置差异可视化")
    click.echo("  • 逐 query 结果浏览和排错")
    click.echo(f"  数据位置: {EXPERIMENTS_DIR / name / 'results'}")


# ══════════════════════════════════════════════════════════════════════════════
# analyze-topk — TopK 实验跨方法对比分析
# ══════════════════════════════════════════════════════════════════════════════


@cli.command("analyze-topk")
@click.option(
    "--exps",
    default=None,
    help="逗号分隔的实验名列表（默认全部 topk 实验）",
)
@click.option(
    "--metric", default=None,
    help="指定指标（如 Recall@20）",
)
@click.option(
    "--by", default=None,
    help="分组维度（query_type / difficulty）",
)
@click.option(
    "--pairwise", "pairwise_opts", multiple=True,
    default=None,
    help="配对对比，格式 A,B（可多次指定）",
)
@click.option(
    "--elbow", is_flag=True, default=False,
    help="执行 elbow 分析",
)
@click.option(
    "--latency", is_flag=True, default=False,
    help="输出延迟分析",
)
@click.option(
    "--json", "json_output", is_flag=True, default=False,
    help="输出 JSON 格式",
)
def analyze_topk_cmd(
    exps: str | None = None,
    metric: str | None = None,
    by: str | None = None,
    pairwise_opts: tuple[str, ...] | None = None,
    elbow: bool = False,
    latency: bool = False,
    json_output: bool = False,
) -> None:
    """跨实验对比 TopK 参数实验的结果。

    加载多个 topk-* 实验的结果 JSON，产出指标矩阵、Elbow 分析、
    Latency 拆解、分组对比和逐 Query 配对分析。
    """
    # 延迟导入，避免加载耗时依赖
    from evaluation.analyze_topk import run_analysis, ALL_TOPK_EXPS

    exp_names = None
    if exps:
        exp_names = [e.strip() for e in exps.split(",") if e.strip()]

    pairwise_pairs = None
    if pairwise_opts:
        pairwise_pairs = []
        for pair_str in pairwise_opts:
            parts = [p.strip() for p in pair_str.split(",")]
            if len(parts) == 2:
                pairwise_pairs.append((parts[0], parts[1]))

    result = run_analysis(
        exp_names=exp_names or ALL_TOPK_EXPS,
        metric=metric,
        group_by=by,
        pairwise=pairwise_pairs,
        elbow=elbow,
        latency=latency,
        json_output=json_output,
    )

    if json_output and result is not None:
        import json
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))

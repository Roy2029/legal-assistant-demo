"""Chunk 导出分析工具

将 Store 中存储的 Chunk 对象导出为 CSV（Excel 浏览）、JSONL（pandas 分析）、
或 HTML（交互式浏览），并提供简要的统计概览。

用法:
    # 作为模块运行（推荐）
    python -m offline_core.chunk_export <store_path> [--format csv|jsonl|html] [--output out]

    # 作为脚本直接运行
    python offline_core/chunk_export.py <store_path> [--format csv|jsonl|html] [--output out]
"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中，使直接运行脚本时也能正确引入
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import argparse
import csv
import html as html_module
import json
import pickle
from itertools import groupby
from typing import Any, Dict, List, Optional

from offline_core.data_model import Chunk


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, "Microsoft YaHei", "PingFang SC", sans-serif; color: #333; background: #f5f5f5; }}
header {{ position: sticky; top: 0; z-index: 100; background: #fff; padding: 12px 24px; border-bottom: 1px solid #ddd; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
header h1 {{ font-size: 1.2em; white-space: nowrap; }}
header .stats {{ font-size: 0.85em; color: #666; white-space: nowrap; }}
#search {{ flex: 1; min-width: 200px; padding: 8px 12px; border: 1px solid #ccc; border-radius: 4px; font-size: 0.9em; }}
#search:focus {{ outline: none; border-color: #0066cc; box-shadow: 0 0 0 2px rgba(0,102,204,0.15); }}
.layout {{ display: flex; min-height: calc(100vh - 52px); }}
.sidebar {{ width: 280px; flex-shrink: 0; background: #fff; border-right: 1px solid #eee; padding: 12px 0; position: sticky; top: 52px; height: calc(100vh - 52px); overflow-y: auto; }}
.sidebar ul {{ list-style: none; padding-left: 16px; }}
.sidebar > ul {{ padding-left: 0; }}
.sidebar li {{ margin: 2px 0; }}
.sidebar .doc-link {{ display: block; padding: 6px 16px; font-weight: 600; color: #222; text-decoration: none; font-size: 0.9em; border-left: 3px solid transparent; }}
.sidebar .doc-link:hover, .sidebar .doc-link.active {{ background: #e8f0fe; border-left-color: #0066cc; color: #0066cc; }}
.sidebar .chunk-link {{ display: block; padding: 3px 16px 3px 32px; color: #555; text-decoration: none; font-size: 0.82em; border-left: 3px solid transparent; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.sidebar .chunk-link:hover, .sidebar .chunk-link.active {{ background: #f0f7ff; border-left-color: #66aaff; color: #0066cc; }}
.content {{ flex: 1; padding: 24px; max-width: 960px; }}
.doc-section {{ margin-bottom: 40px; }}
.doc-header {{ display: flex; align-items: baseline; gap: 12px; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid #333; }}
.doc-header h2 {{ font-size: 1.25em; }}
.doc-header .doc-chunk-count {{ font-size: 0.85em; color: #888; }}
.chunk-card {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px 20px; margin-bottom: 16px; transition: box-shadow 0.15s; }}
.chunk-card:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
.chunk-card.search-hidden {{ display: none; }}
.chunk-heading-path {{ font-size: 0.85em; color: #888; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid #f0f0f0; }}
.chunk-heading-path .sep {{ color: #ccc; margin: 0 4px; }}
.chunk-meta-badge {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }}
.chunk-meta-badge .badge {{ font-size: 0.75em; padding: 2px 8px; border-radius: 10px; background: #f0f0f0; color: #666; }}
.chunk-text {{ white-space: pre-wrap; word-break: break-word; line-height: 1.7; font-size: 0.95em; }}
.chunk-text.code {{ font-family: "SF Mono", "Fira Code", "Consolas", monospace; background: #f8f9fa; padding: 12px; border-radius: 4px; font-size: 0.85em; line-height: 1.5; }}
.chunk-text.table {{ background: #fafbfc; padding: 12px; border-radius: 4px; }}
details.chunk-metadata {{ margin-top: 12px; }}
details.chunk-metadata summary {{ cursor: pointer; font-size: 0.85em; color: #888; user-select: none; }}
details.chunk-metadata summary:hover {{ color: #333; }}
details.chunk-metadata table {{ width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 0.85em; }}
details.chunk-metadata td {{ padding: 4px 10px; border: 1px solid #eee; vertical-align: top; }}
details.chunk-metadata td:first-child {{ font-weight: 600; color: #555; width: 120px; white-space: nowrap; }}
details.chunk-metadata td.mono {{ font-family: "SF Mono", "Consolas", monospace; font-size: 0.9em; word-break: break-all; }}
.no-results {{ text-align: center; padding: 60px 20px; color: #999; display: none; }}
@media (max-width: 768px) {{ .sidebar {{ display: none; }} .content {{ padding: 16px; }} }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <span class="stats">{total_chunks} chunks · {total_docs} 文档</span>
  <input id="search" type="text" placeholder="搜索 chunk 内容或元数据…" autocomplete="off">
</header>
<div class="layout">
<nav class="sidebar">
<ul>
{sidebar_items}
</ul>
</nav>
<main class="content">
{content_sections}
<div class="no-results" id="no-results">没有匹配的 chunk</div>
</main>
</div>
<script>
(function() {{
  var search = document.getElementById('search');
  var cards = document.querySelectorAll('.chunk-card');
  var sections = document.querySelectorAll('.doc-section');
  var noResults = document.getElementById('no-results');
  var sidebarLinks = document.querySelectorAll('.sidebar a');

  function filterCards(q) {{
    var anyVisible = false;
    cards.forEach(function(card) {{
      var text = card.textContent.toLowerCase();
      var match = !q || text.indexOf(q) !== -1;
      card.classList.toggle('search-hidden', !match);
      if (match) anyVisible = true;
    }});
    sections.forEach(function(section) {{
      var visible = Array.from(section.querySelectorAll('.chunk-card')).some(function(c) {{
        return !c.classList.contains('search-hidden');
      }});
      section.style.display = visible ? '' : 'none';
    }});
    noResults.style.display = anyVisible ? 'none' : 'block';
  }}

  search.addEventListener('input', function() {{
    filterCards(this.value.toLowerCase().trim());
  }});

  document.addEventListener('click', function(e) {{
    var link = e.target.closest('.sidebar a');
    if (link) {{
      sidebarLinks.forEach(function(l) {{ l.classList.remove('active'); }});
      link.classList.add('active');
    }}
  }});

  // 展开第一个文档的所有元数据（可选：默认折叠）
}})();
</script>
</body>
</html>"""


def _detect_store_type(store_path: str) -> str:
    """根据 store_path 目录下的文件自动检测 Store 类型。"""
    path = Path(store_path)
    # 优先检测 Qdrant 嵌入式存储（meta.json 或 qdrant 子目录）
    if (path / "meta.json").exists() or (path / ".qdrant").exists():
        return "qdrant"
    # 向下兼容旧格式
    if (path / "bm25_data.pkl").exists():
        return "bm25"
    elif (path / "faiss.index").exists():
        return "faiss"
    elif (path / "records.pkl").exists():
        return "in_memory"
    # 如果 store_path 本身是 qdrant 子目录（如 index_store/db/qdrant）
    if path.name == "qdrant" and path.is_dir():
        return "qdrant"
    raise FileNotFoundError(
        f"无法识别 Store 类型：{store_path} 下未找到 "
        f"qdrant meta、bm25_data.pkl、faiss.index 或 records.pkl"
    )


def load_chunks(store_path: str, store_type: Optional[str] = None) -> List[Chunk]:
    """从已保存的 Store 路径中加载所有 Chunk 对象。

    Args:
        store_path: Store.save() 时保存的目录路径。
        store_type: Store 类型（bm25 / in_memory / faiss），为 None 时自动检测。

    Returns:
        所有 Chunk 对象的列表。
    """
    if store_type is None:
        store_type = _detect_store_type(store_path)

    path = Path(store_path)

    if store_type == "qdrant":
        from offline_core.store import QdrantStore, QdrantConfig

        # 自动探测 collection name
        config = QdrantConfig(
            mode="embedded",
            path=str(path),
            collection_name="chunks",
        )
        store = QdrantStore(config)
        return store.scroll()

    elif store_type == "bm25":
        from offline_core.store import BM25Store

        store = BM25Store()
        store.load(str(path))
        return list(store.chunks.values())

    elif store_type == "in_memory":
        from offline_core.store import InMemoryStore

        store = InMemoryStore()
        store.load(str(path))
        return [r.chunk for r in store.data]

    elif store_type == "faiss":
        # 只读 records.pkl 提取 chunk，避免依赖 FAISS 和 dimension 参数
        with open(path / "records.pkl", "rb") as f:
            data = pickle.load(f)
        return [record.chunk for record in data["records"].values()]

    else:
        raise ValueError(f"不支持的 Store 类型: {store_type}")


def _collect_metadata_keys(chunks: List[Chunk]) -> List[str]:
    """扫描所有 chunk 的 metadata，收集所有出现的 key。"""
    keys: set[str] = set()
    for c in chunks:
        keys.update(c.metadata.keys())
    return sorted(keys)


def _serialize_value(v: Any) -> str:
    """将 metadata 中的值序列化为 CSV 单元格可读的字符串。"""
    if isinstance(v, (str, int, float, bool)):
        return str(v)
    if v is None:
        return ""
    return json.dumps(v, ensure_ascii=False)


def export_to_csv(
    chunks: List[Chunk],
    output_path: str,
    flatten_metadata: bool = True,
) -> str:
    """将 Chunk 列表导出为 CSV 文件（UTF-8 BOM，Excel 可直接打开）。

    Args:
        chunks: Chunk 对象列表。
        output_path: 输出的 CSV 文件路径。
        flatten_metadata: 是否将 metadata 展平为独立列。

    Returns:
        输出的文件路径。
    """
    meta_keys = _collect_metadata_keys(chunks) if flatten_metadata else []

    base_fields = [
        "chunk_id",
        "doc_id",
        "order",
        "token_count",
        "text_length",
        "heading_path",
        "block_ids",
        "text_preview",
        "text",
    ]
    if flatten_metadata:
        base_fields.extend(meta_keys)
    base_fields.append("metadata_json")

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(base_fields)

        for c in chunks:
            row = [
                c.chunk_id,
                c.doc_id,
                c.order,
                c.token_count if c.token_count is not None else "",
                len(c.text),
                " > ".join(c.heading_path),
                ", ".join(c.block_ids),
                c.text[:200],
                c.text,
            ]
            if flatten_metadata:
                for key in meta_keys:
                    row.append(_serialize_value(c.metadata.get(key)))
            row.append(json.dumps(c.metadata, ensure_ascii=False))
            writer.writerow(row)

    return output_path


def export_to_jsonl(chunks: List[Chunk], output_path: str) -> str:
    """将 Chunk 列表导出为 JSONL 文件（每行一个完整 Chunk 的 JSON）。

    保留完整的嵌套结构（metadata dict、heading_path list 不变），
    pandas 读取方式:

        import pandas as pd
        df = pd.read_json("output.jsonl", lines=True)

    Args:
        chunks: Chunk 对象列表。
        output_path: 输出的 JSONL 文件路径。

    Returns:
        输出的文件路径。
    """
    with open(output_path, "w", encoding="utf-8") as f:
        for c in chunks:
            obj = {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "text": c.text,
                "metadata": c.metadata,
                "block_ids": c.block_ids,
                "heading_path": c.heading_path,
                "order": c.order,
                "token_count": c.token_count,
            }
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return output_path


def _html_escape(obj: Any) -> str:
    """将任意值转为 HTML 安全的字符串。"""
    if isinstance(obj, str):
        return html_module.escape(obj)
    if obj is None:
        return ""
    return html_module.escape(json.dumps(obj, ensure_ascii=False))


def _group_chunks_by_doc(chunks: List[Chunk]) -> List[tuple]:
    """将 chunks 按文档分组，返回 [(doc_label, doc_id, [chunks])]。

    优先使用 metadata 中的 source/file_name 作为文档标签，
    回退到 doc_id。
    """
    def doc_key(c: Chunk) -> str:
        return c.metadata.get("source") or c.metadata.get("file_name") or c.doc_id

    def doc_label(c: Chunk) -> str:
        src = c.metadata.get("source") or ""
        fname = c.metadata.get("file_name") or ""
        return fname or Path(src).name if src else c.doc_id[:16] + "…"

    # 按 doc_key 分组（保持 doc_key 排序以便一致）
    sorted_chunks = sorted(chunks, key=lambda c: (doc_key(c), c.order))
    groups = []
    for _, group in groupby(sorted_chunks, key=doc_key):
        group_list = list(group)
        groups.append((doc_label(group_list[0]), group_list[0].doc_id, group_list))
    return groups


def export_to_html(
    chunks: List[Chunk],
    output_path: str,
    title: str = "Chunk 浏览",
) -> str:
    """将 Chunk 列表导出为自包含的 HTML 交互式浏览页面。

    HTML 页面包含：
    - 左侧导航树：按文档分组，展示 heading 层级，可点击跳转
    - 右侧内容区：展示每个 chunk 的全文、heading_path 面包屑、元数据
    - 搜索框：实时过滤所有 chunk 的文本和元数据

    Args:
        chunks: Chunk 对象列表。
        output_path: 输出的 HTML 文件路径（需以 .html 结尾）。
        title: 页面标题。

    Returns:
        输出的文件路径。
    """
    groups = _group_chunks_by_doc(chunks)

    # ── 构建侧边栏导航树 ──
    sidebar_items: List[str] = []
    for gi, (doc_label, _doc_id, doc_chunks) in enumerate(groups):
        doc_anchor = f"doc-{gi}"
        sidebar_items.append(
            f'<li><a class="doc-link" href="#{doc_anchor}">{_html_escape(doc_label)}'
            f' <span style="font-weight:400;color:#999;">({len(doc_chunks)})</span></a></li>'
        )
        # 每个 chunk 的导航项
        ci_items: List[str] = []
        for ci, c in enumerate(doc_chunks):
            heading_label = " > ".join(c.heading_path) if c.heading_path else f"Chunk #{c.order}"
            chunk_anchor = f"{doc_anchor}-{ci}"
            ci_items.append(
                f'<li><a class="chunk-link" href="#{chunk_anchor}">'
                f'{_html_escape(heading_label)}</a></li>'
            )
        if ci_items:
            sidebar_items.append("<ul>\n" + "\n".join(ci_items) + "\n</ul>")

    sidebar_html = "\n".join(sidebar_items)

    # ── 构建内容区 ──
    content_sections: List[str] = []
    for gi, (doc_label, doc_id, doc_chunks) in enumerate(groups):
        doc_anchor = f"doc-{gi}"
        sections_inner: List[str] = []
        sections_inner.append(
            f'<section class="doc-section" id="{doc_anchor}">'
            f'<div class="doc-header">'
            f'<h2>{_html_escape(doc_label)}</h2>'
            f'<span class="doc-chunk-count">{len(doc_chunks)} chunks</span>'
            f'</div>'
        )

        for ci, c in enumerate(doc_chunks):
            chunk_anchor = f"{doc_anchor}-{ci}"

            # heading_path 面包屑
            if c.heading_path:
                heading_html = _html_escape(" > ".join(c.heading_path))
                heading_breadcrumb = f'<div class="chunk-heading-path">{heading_html}</div>'
            else:
                heading_breadcrumb = ""

            # 元数据徽标
            badges = [
                f'<span class="badge">#{c.order}</span>',
            ]
            if c.token_count is not None:
                badges.append(f'<span class="badge">{c.token_count} tokens</span>')
            badges.append(f'<span class="badge">{len(c.text)} chars</span>')
            if c.metadata.get("type") == "code":
                lang = c.metadata.get("language") or ""
                badges.append(f'<span class="badge" style="background:#d4edda;">code{(" · " + lang) if lang else ""}</span>')
            elif c.metadata.get("type") == "table":
                badges.append(f'<span class="badge" style="background:#d1ecf1;">table ({c.metadata.get("num_rows", "?")} rows)</span>')
            elif c.metadata.get("type") == "image":
                badges.append(f'<span class="badge" style="background:#fff3cd;">image</span>')

            badges_html = f'<div class="chunk-meta-badge">{"".join(badges)}</div>'

            # 文本内容
            text_class = "chunk-text"
            if c.metadata.get("type") == "code":
                text_class += " code"
            elif c.metadata.get("type") == "table":
                text_class += " table"
            text_html = f'<div class="{text_class}">{_html_escape(c.text)}</div>'

            # 元数据折叠表
            meta_rows: List[str] = [
                f"<tr><td>chunk_id</td><td class=\"mono\">{_html_escape(c.chunk_id)}</td></tr>",
                f"<tr><td>doc_id</td><td class=\"mono\">{_html_escape(c.doc_id)}</td></tr>",
                f"<tr><td>order</td><td>{c.order}</td></tr>",
                f"<tr><td>token_count</td><td>{c.token_count if c.token_count is not None else '—'}</td></tr>",
                f"<tr><td>text_length</td><td>{len(c.text)}</td></tr>",
                f"<tr><td>block_ids</td><td class=\"mono\">{_html_escape(json.dumps(c.block_ids, ensure_ascii=False))}</td></tr>",
                f"<tr><td>heading_path</td><td>{_html_escape(json.dumps(c.heading_path, ensure_ascii=False))}</td></tr>",
            ]
            for key, value in c.metadata.items():
                meta_rows.append(
                    f"<tr><td>{_html_escape(key)}</td><td>{_html_escape(value)}</td></tr>"
                )

            metadata_html = (
                f'<details class="chunk-metadata">'
                f'<summary>元数据 ({len(meta_rows)} 项)</summary>'
                f'<table>{"".join(meta_rows)}</table>'
                f'</details>'
            )

            sections_inner.append(
                f'<div class="chunk-card" id="{chunk_anchor}">'
                f'{heading_breadcrumb}'
                f'{badges_html}'
                f'{text_html}'
                f'{metadata_html}'
                f'</div>'
            )

        sections_inner.append("</section>")
        content_sections.append("\n".join(sections_inner))

    content_html = "\n".join(content_sections)

    # ── 填充模板 ──
    full_html = _HTML_TEMPLATE.format(
        title=_html_escape(title),
        total_chunks=len(chunks),
        total_docs=len(groups),
        sidebar_items=sidebar_html,
        content_sections=content_html,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_html)

    return output_path


def print_stats(chunks: List[Chunk]) -> None:
    """在控制台输出 Chunk 统计概览。"""
    # 按文档分组
    doc_chunks: Dict[str, List[Chunk]] = {}
    for c in chunks:
        doc_chunks.setdefault(c.doc_id, []).append(c)

    n_chunks = len(chunks)
    n_docs = len(doc_chunks)
    chunks_per_doc = [len(clist) for clist in doc_chunks.values()]

    # token / 文本长度
    token_counts = [c.token_count for c in chunks if c.token_count is not None]
    text_lengths = [len(c.text) for c in chunks]

    # 文档列表（按 doc_id 排序）
    doc_info = sorted(
        (doc_id, len(clist)) for doc_id, clist in doc_chunks.items()
    )

    print("=" * 60)
    print("  Chunk 统计概览")
    print("=" * 60)
    print(f"  总 Chunk 数           {n_chunks}")
    print(f"  总文档数              {n_docs}")
    print(f"  每文档 Chunk 数       "
          f"min={min(chunks_per_doc)}, "
          f"max={max(chunks_per_doc)}, "
          f"avg={sum(chunks_per_doc) / n_docs:.1f}")
    if token_counts:
        print(f"  Token 数              "
              f"min={min(token_counts)}, "
              f"max={max(token_counts)}, "
              f"avg={sum(token_counts) / len(token_counts):.1f}")
    print(f"  文本长度（字符）       "
          f"min={min(text_lengths)}, "
          f"max={max(text_lengths)}, "
          f"avg={sum(text_lengths) / len(text_lengths):.0f}")
    print()

    print(f"{'文档 ID':<40} {'Chunk 数':>10}")
    print("-" * 50)
    for doc_id, count in doc_info:
        display_id = doc_id if len(doc_id) <= 38 else doc_id[:35] + "..."
        print(f"  {display_id:<38} {count:>10}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将 Store 中的 Chunk 导出为 CSV、JSONL 或 HTML，方便浏览分析。"
    )
    parser.add_argument(
        "store_path", help="Store.save() 的输出目录路径"
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["csv", "jsonl", "html", "both"],
        default="csv",
        help='导出格式（默认 csv；both 同时导出 csv+jsonl）',
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="输出文件路径（默认: store_path/chunks.csv 或 chunks.jsonl）",
    )
    parser.add_argument(
        "--type",
        "-t",
        choices=["bm25", "in_memory", "faiss", "qdrant", "auto"],
        default="auto",
        dest="store_type",
        help="Store 类型（默认自动检测）",
    )
    parser.add_argument(
        "--no-stats",
        action="store_true",
        help="不显示统计概览",
    )

    args = parser.parse_args()

    # 加载
    store_type = None if args.store_type == "auto" else args.store_type
    chunks = load_chunks(args.store_path, store_type)
    print(f"已加载 {len(chunks)} 个 Chunk\n")

    # 统计
    if not args.no_stats:
        print_stats(chunks)
        print()

    # 导出
    output_base = args.output or str(Path(args.store_path) / "chunks")

    formats = ["csv", "jsonl", "html"] if args.format == "both" else [args.format]

    if "csv" in formats:
        path = export_to_csv(chunks, f"{output_base}.csv")
        print(f"CSV  导出完成: {path}")

    if "jsonl" in formats:
        path = export_to_jsonl(chunks, f"{output_base}.jsonl")
        print(f"JSONL 导出完成: {path}")

    if "html" in formats:
        path = export_to_html(chunks, f"{output_base}.html")
        print(f"HTML 导出完成: {path}")


if __name__ == "__main__":
    main()

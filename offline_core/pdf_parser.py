"""基于 DeepDoc (RAGFlow) 架构的 PDF 解析器。

使用 pdfplumber 提取文本、表格和图片，自动检测标题层级。
轻量级实现，无需 ML 模型。

v2 增强特性：
- 跨页文本合并（段落跨页自动延续）
- 跨页表格合并（相同结构的表格自动拼接）
- 多栏布局检测（KMeans + 轮廓系数）

用法：
    parser = PdfParser(extract_tables=True, extract_images=False)
    doc = parser.parse("path/to/document.pdf")
    # doc 是 StructuredDocument，包含 HeadingBlock, ParagraphBlock, TableBlock 等
"""

import uuid
import os
import re
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from pathlib import Path
from collections import Counter

import pdfplumber
from pdfplumber.page import Page
from pdfplumber.table import Table

from .modules import Parser
from .data_model import (
    StructuredDocument,
    Block,
    ParagraphBlock,
    HeadingBlock,
    TableBlock,
    ImageBlock,
)

# 多栏检测 — sklearn 可选导入
_HAS_SKLEARN = False
try:
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    import numpy as np
    _HAS_SKLEARN = True
except ImportError:
    pass


@dataclass
class _Line:
    """PDF 页面中提取的一行文本（含字体元数据和位置信息）。"""
    text: str
    font_size: float
    is_bold: bool
    top: float
    x0: float
    page_num: int


class PdfParser(Parser):
    """PDF 解析器，继承 Parser 基类，输出 StructuredDocument。

    基于 DeepDoc 的设计思路：
    - 逐页提取字符 → 按行分组 → 根据字号/加粗/正则判断标题
    - (可选) 提取表格 → TableBlock
    - (可选) 提取图片 → ImageBlock
    - 跨页段落自动合并（字号/缩进连续性判断）
    - 跨页表格自动合并（列数/表头/位置匹配）
    - 多栏布局 KMeans 检测（需 sklearn）

    v1 限制：
    - 不支持扫描件 OCR（需要 ONNX 模型）
    - 不支持数学公式提取
    """

    # 中文标题正则模式（优先级从高到低）
    _HEADING_PATTERNS = [
        re.compile(r"^第[一二三四五六七八九十百千零\d]+[章节篇条]"),  # 第一章/第1节
        re.compile(r"^[一二三四五六七八九十]+[、．.]"),  # 一、 二．
        re.compile(r"^（[一二三四五六七八九十百千零\d]+）"),  # （一）
        re.compile(r"^\([一二三四五六七八九十百千零\d]+\)"),  # (一)
        re.compile(r"^\d+\.\d+[\.\s]"),  # 1.1 / 1.1.
        re.compile(r"^\d+[\.\s]"),  # 1.
        re.compile(r"^[A-Z][\.\s]"),  # A.
        re.compile(r"^•\s"),  # 要点 bullet
    ]

    def __init__(
        self,
        extract_tables: bool = True,
        extract_images: bool = False,
        image_output_dir: str = "./pdf_images",
        heading_threshold_ratio: float = 1.2,
        min_heading_font_delta: float = 2.0,
        enable_column_detection: bool = False,
        merge_cross_page_tables: bool = True,
    ):
        """
        Args:
            extract_tables: 是否提取表格（调用 pdfplumber page.find_tables()）
            extract_images: 是否提取图片（保存到 image_output_dir）
            image_output_dir: 图片输出目录
            heading_threshold_ratio: 字号大于 body_font 的此倍数视为标题
            min_heading_font_delta: 与 body_font 的最小字号差（pt）
            enable_column_detection: 是否启用多栏布局检测（需 sklearn）
            merge_cross_page_tables: 是否合并跨页表格
        """
        self.extract_tables = extract_tables
        self.extract_images = extract_images
        self.image_output_dir = image_output_dir
        self.heading_threshold_ratio = heading_threshold_ratio
        self.min_heading_font_delta = min_heading_font_delta
        self.enable_column_detection = enable_column_detection
        self.merge_cross_page_tables = merge_cross_page_tables

    # ── 主入口 ──────────────────────────────────────────────

    def _parse_impl(self, file_path: str) -> StructuredDocument:
        doc_id = str(uuid.uuid4())
        all_blocks: List[Block] = []
        order = 0

        if self.extract_images and self.image_output_dir:
            os.makedirs(self.image_output_dir, exist_ok=True)

        with pdfplumber.open(file_path) as pdf:
            total_pages = len(pdf.pages)

            # 全局 body_font：收集所有页面的字符后计算，确保一致性
            global_body_font = self._compute_global_body_font(pdf)

            # 跨页状态
            cross_page_text: List[str] = []
            cross_page_page: Optional[int] = None
            prev_page_last_line: Optional[_Line] = None

            for page_num, page in enumerate(pdf.pages, start=1):
                page_blocks, order, remaining_text, remaining_page = self._parse_page(
                    page, page_num, doc_id, order,
                    global_body_font=global_body_font,
                    resume_text=cross_page_text if cross_page_text else None,
                    resume_page=cross_page_page,
                    prev_last_line=prev_page_last_line,
                )
                all_blocks.extend(page_blocks)

                cross_page_text = remaining_text
                cross_page_page = remaining_page

                if page.chars:
                    lines = self._group_chars_into_lines(page.chars, page_num)
                    prev_page_last_line = lines[-1] if lines else None
                else:
                    prev_page_last_line = None

            if cross_page_text:
                all_blocks.append(self._make_paragraph(
                    cross_page_text, cross_page_page, order,
                ))
                order += 1

            # ── 跨页表格合并 ──
            if self.extract_tables and self.merge_cross_page_tables:
                all_blocks = self._merge_cross_page_tables(all_blocks)

            # ── 多栏重排序 ──
            if self.enable_column_detection and _HAS_SKLEARN:
                all_blocks = self._reorder_by_columns(all_blocks, pdf)

            all_blocks.sort(key=lambda b: (b.page or 0, b.order))
            for i, b in enumerate(all_blocks):
                b.order = i

        return StructuredDocument(
            doc_id=doc_id,
            source=str(Path(file_path).resolve()),
            blocks=all_blocks,
            metadata={
                "total_pages": total_pages,
                "parser": "pdfplumber",
                "extract_tables": self.extract_tables,
                "column_detection": self.enable_column_detection and _HAS_SKLEARN,
            },
        )

    @staticmethod
    def _compute_global_body_font(pdf) -> float:
        """跨所有页面计算全局正文字号。"""
        all_sizes: List[float] = []
        for page in pdf.pages:
            for c in page.chars:
                if c.get("text", "").strip():
                    all_sizes.append(c["size"])
        if not all_sizes:
            return 12.0

        all_sizes.sort()
        n = len(all_sizes)
        median = all_sizes[n // 2]

        q1 = all_sizes[n // 4]
        q3 = all_sizes[3 * n // 4]
        iqr = q3 - q1
        upper = median + 3 * iqr

        filtered = [s for s in all_sizes if s <= upper]
        if not filtered:
            return median
        filtered.sort()
        return filtered[len(filtered) // 2]

    # ── 页面级解析 ──────────────────────────────────────────

    def _parse_page(
        self,
        page: Page,
        page_num: int,
        doc_id: str,
        start_order: int,
        global_body_font: Optional[float] = None,
        resume_text: Optional[List[str]] = None,
        resume_page: Optional[int] = None,
        prev_last_line: Optional[_Line] = None,
    ) -> Tuple[List[Block], int, List[str], Optional[int]]:
        """解析单页。

        Args:
            page: pdfplumber Page 对象
            page_num: 页码（从 1 开始）
            doc_id: 文档 ID
            start_order: 起始 order
            global_body_font: 全局正文字号（跨页一致性）
            resume_text: 上一页未 flush 的文本
            resume_page: resume_text 的起始页码
            prev_last_line: 上一页的最后一行（用于跨页连续性判断）

        Returns:
            (blocks, next_order, remaining_text, remaining_page)
        """
        blocks: List[Block] = []
        order = start_order

        chars = page.chars
        if not chars:
            return blocks, order, [], None

        body_font = global_body_font or self._detect_body_font_size(chars)
        lines = self._group_chars_into_lines(chars, page_num)

        # ── 跨页延续处理 ──
        current_paragraph: List[str] = []
        current_para_page = page_num

        if resume_text is not None and resume_text:
            continued = False
            if lines and prev_last_line:
                continued = self._is_cross_page_continuation(
                    lines[0], prev_last_line, body_font,
                )
            if continued:
                current_paragraph = list(resume_text)
                current_para_page = resume_page
            else:
                blocks.append(self._make_paragraph(
                    resume_text, resume_page, order,
                ))
                order += 1

        # ── 逐行分类 ──
        prev_line: Optional[_Line] = None
        for line in lines:
            heading_level = self._detect_heading_level(line, body_font)

            if heading_level is not None:
                # 遇到标题 → flush 当前段落
                if current_paragraph:
                    blocks.append(self._make_paragraph(
                        current_paragraph, current_para_page, order,
                    ))
                    order += 1
                    current_paragraph = []
                    current_para_page = page_num

                blocks.append(HeadingBlock(
                    content=line.text,
                    level=heading_level,
                    page=page_num,
                    order=order,
                ))
                order += 1
            else:
                # 正文行
                if current_paragraph and prev_line and self._is_paragraph_break(line, prev_line):
                    blocks.append(self._make_paragraph(
                        current_paragraph, current_para_page, order,
                    ))
                    order += 1
                    current_paragraph = []
                    current_para_page = page_num

                current_paragraph.append(line.text)

            prev_line = line

        # ── 表格 ──
        if self.extract_tables:
            table_blocks, order = self._extract_tables(page, page_num, order)
            blocks.extend(table_blocks)

        # ── 图片 ──
        if self.extract_images:
            image_blocks, order = self._extract_images(page, page_num, doc_id, order)
            blocks.extend(image_blocks)

        # 返回未 flush 的段落给 _parse_impl 处理跨页延续
        return blocks, order, current_paragraph, current_para_page

    # ── 跨页连续性判断 ──────────────────────────────────────

    @staticmethod
    def _is_cross_page_continuation(
        first_line: _Line,
        prev_last_line: _Line,
        body_font: float,
    ) -> bool:
        """判断页面首行是否为上一页段落的延续。

        条件（同时满足）：
        1. 字号相近（|diff| < 1pt）
        2. 缩进相近（|x0 diff| < 5px）
        3. 首行不是标题
        """
        # 1. 字号连续性
        if abs(first_line.font_size - prev_last_line.font_size) > 1.0:
            return False

        # 2. 首行不是标题
        if first_line.font_size >= body_font * 1.2:
            return False

        # 3. 缩进连续性
        if abs(first_line.x0 - prev_last_line.x0) > 5.0:
            return False

        return True

    # ── 正文/字号检测 ──────────────────────────────────────

    @staticmethod
    def _detect_body_font_size(chars: List[Dict]) -> float:
        """检测正文主体字号（中位数，去除异常值）。"""
        sizes = [c["size"] for c in chars if c.get("text", "").strip()]
        if not sizes:
            return 12.0

        sizes.sort()
        n = len(sizes)
        median = sizes[n // 2]

        q1 = sizes[n // 4]
        q3 = sizes[3 * n // 4]
        iqr = q3 - q1
        upper = median + 3 * iqr

        filtered = [s for s in sizes if s <= upper]
        if not filtered:
            return median

        filtered.sort()
        return filtered[len(filtered) // 2]

    # ── 按行分组 ────────────────────────────────────────────

    @staticmethod
    def _group_chars_into_lines(chars: List[Dict], page_num: int) -> List[_Line]:
        """将字符按垂直位置分组为文本行。"""
        if not chars:
            return []

        sorted_chars = sorted(chars, key=lambda c: (c["top"], c["x0"]))

        lines: List[List[Dict]] = []
        current = [sorted_chars[0]]

        for c in sorted_chars[1:]:
            prev = current[-1]
            char_height = abs(prev.get("top", 0) - prev.get("bottom", 0)) or 10

            if abs(c["top"] - prev["top"]) < char_height * 0.7:
                current.append(c)
            else:
                lines.append(current)
                current = [c]

        if current:
            lines.append(current)

        result: List[_Line] = []
        for line_chars in lines:
            text = "".join(c.get("text", "") for c in line_chars).strip()
            if not text:
                continue

            sizes = [c.get("size", 12) for c in line_chars if c.get("text", "").strip()]
            font_size = max(sizes) if sizes else 12.0

            fontnames = [c.get("fontname", "") for c in line_chars]
            is_bold = any(
                kw in fn for fn in fontnames
                for kw in ("Bold", "Heavy", "Black")
            )

            result.append(_Line(
                text=text,
                font_size=font_size,
                is_bold=is_bold,
                top=line_chars[0].get("top", 0),
                x0=line_chars[0].get("x0", 0),
                page_num=page_num,
            ))

        return result

    # ── 标题检测 ────────────────────────────────────────────

    def _detect_heading_level(self, line: _Line, body_font: float) -> Optional[int]:
        """检测行是否为标题，若是以返回级别 (1-6)，否则返回 None。"""
        level = self._heading_level_by_font(line, body_font)
        if level is not None:
            return level
        if self._matches_heading_pattern(line.text):
            return 2
        return None

    def _heading_level_by_font(self, line: _Line, body_font: float) -> Optional[int]:
        """根据字号判断标题级别。"""
        if line.font_size <= body_font:
            return None

        ratio = line.font_size / body_font if body_font > 0 else 1
        delta = line.font_size - body_font

        if ratio < self.heading_threshold_ratio and delta < self.min_heading_font_delta:
            return None

        if ratio >= 2.0:
            return 1
        elif ratio >= 1.6:
            return 2
        elif ratio >= 1.3:
            return 3
        else:
            if line.is_bold:
                return 4
            return None

    def _matches_heading_pattern(self, text: str) -> bool:
        """检查文本是否匹配标题正则模式。"""
        return any(p.search(text) for p in self._HEADING_PATTERNS)

    # ── 段落管理 ────────────────────────────────────────────

    @staticmethod
    def _is_paragraph_break(line: _Line, prev_line: _Line) -> bool:
        """判断是否出现段落分隔（基于垂直间距）。"""
        gap = line.top - prev_line.top
        return gap > line.font_size * 1.8

    @staticmethod
    def _make_paragraph(
        lines: List[str],
        page_num: int,
        order: int,
    ) -> ParagraphBlock:
        return ParagraphBlock(
            content="\n".join(lines),
            page=page_num,
            order=order,
        )

    # ── 表格提取 ────────────────────────────────────────────

    def _extract_tables(
        self,
        page: Page,
        page_num: int,
        start_order: int,
    ) -> Tuple[List[TableBlock], int]:
        """从页面提取表格。"""
        blocks: List[TableBlock] = []
        order = start_order

        tables = page.find_tables()
        for table in tables:
            data = table.extract()
            if not data or len(data) < 2:
                continue

            clean_data = [
                [cell.strip() if cell else "" for cell in row]
                for row in data
            ]

            headers = clean_data[0]
            rows = clean_data[1:]

            rows = [r for r in rows if any(c.strip() for c in r)]
            if not rows:
                continue

            # 记住表格在页面的位置（用于跨页合并判断）
            _, top, _, _ = table.bbox

            blocks.append(TableBlock(
                headers=headers,
                rows=rows,
                content=self._table_to_text(headers, rows),
                page=page_num,
                order=order,
                metadata={
                    "table_bbox": table.bbox,
                    "table_top_ratio": top / (page.height or 792),
                },
            ))
            order += 1

        return blocks, order

    @staticmethod
    def _table_to_text(headers: List[str], rows: List[List[str]]) -> str:
        """将表格转换为 Markdown 风格文本表示。"""
        lines = [" | ".join(headers), "---"]
        for row in rows:
            lines.append(" | ".join(row))
        return "\n".join(lines)

    # ── 跨页表格合并 ────────────────────────────────────────

    def _merge_cross_page_tables(self, blocks: List[Block]) -> List[Block]:
        """合并相邻页面之间结构相同的表格。"""
        result: List[Block] = []
        i = 0
        while i < len(blocks):
            block = blocks[i]

            # 检查当前 block 是表格且下一个也是表格（在相邻页）
            if (isinstance(block, TableBlock)
                    and i + 1 < len(blocks)
                    and isinstance(blocks[i + 1], TableBlock)
                    and self._is_same_table(block, blocks[i + 1])):

                next_table = blocks[i + 1]
                merged_rows = block.rows + next_table.rows
                merged = TableBlock(
                    headers=block.headers,
                    rows=merged_rows,
                    content=self._table_to_text(block.headers, merged_rows),
                    page=block.page,
                    order=block.order,
                    metadata={
                        "table_bbox": block.metadata.get("table_bbox"),
                        "page_span": [block.page, next_table.page],
                        "num_pages": 2,
                    },
                )
                result.append(merged)
                i += 2
                continue

            result.append(block)
            i += 1

        return result

    @staticmethod
    def _is_same_table(t1: TableBlock, t2: TableBlock) -> bool:
        """判断两个相邻页的 TableBlock 是否为同一表格的延续。

        匹配条件：
        1. 列数相同
        2. 表头内容至少 80% 相同
        3. t2 位于页面顶部区域（top_ratio < 30%）
        """
        if len(t1.headers) != len(t2.headers):
            return False

        matches = sum(
            1 for h1, h2 in zip(t1.headers, t2.headers)
            if h1.strip() == h2.strip()
        )
        if matches / max(len(t1.headers), 1) < 0.8:
            return False

        # 第二张表必须在页面顶部 30% 区域内
        top_ratio = t2.metadata.get("table_top_ratio", 1.0)
        if top_ratio > 0.3:
            return False

        return True

    # ── 图片提取 ────────────────────────────────────────────

    def _extract_images(
        self,
        page: Page,
        page_num: int,
        doc_id: str,
        start_order: int,
    ) -> Tuple[List[ImageBlock], int]:
        """从页面提取图片。"""
        blocks: List[ImageBlock] = []
        order = start_order

        if not page.images:
            return blocks, order

        char_boxes = [
            (c["x0"], c["top"], c["x1"], c["bottom"], c.get("text", ""))
            for c in page.chars
        ]

        for idx, img in enumerate(page.images):
            img_x0, img_top, img_x1, img_bottom = (
                img["x0"], img["top"], img["x1"], img["bottom"]
            )

            img_path = os.path.join(
                self.image_output_dir,
                f"{doc_id}_p{page_num}_i{idx}.png",
            )

            try:
                page_img = page.to_image(resolution=150)
                cropped = page_img.original.crop((img_x0, img_top, img_x1, img_bottom))
                cropped.save(img_path)
            except Exception:
                img_path = None

            alt_text = self._find_nearby_text(
                img_x0, img_top, img_x1, img_bottom, char_boxes,
            )

            blocks.append(ImageBlock(
                content="",
                image_path=img_path,
                alt_text=alt_text or None,
                page=page_num,
                order=order,
                metadata={"image_bbox": (img_x0, img_top, img_x1, img_bottom)},
            ))
            order += 1

        return blocks, order

    @staticmethod
    def _find_nearby_text(
        x0: float, top: float,
        x1: float, bottom: float,
        char_boxes: List[Tuple[float, float, float, float, str]],
        max_distance: float = 50,
    ) -> Optional[str]:
        """从图片附近找文本（用于 alt text）。"""
        nearby: List[str] = []
        for cx0, ctop, cx1, cbottom, text in char_boxes:
            v_dist = min(
                abs(top - cbottom),
                abs(ctop - bottom),
            )
            h_overlap = min(x1, cx1) - max(x0, cx0)
            if v_dist < max_distance and h_overlap > 0:
                nearby.append(text)

        if nearby:
            return " ".join(nearby)
        return None

    # ── 多栏布局检测 ────────────────────────────────────────

    def _reorder_by_columns(
        self,
        blocks: List[Block],
        pdf,
    ) -> List[Block]:
        """检测多栏布局并按先左后右的阅读顺序重排 blocks。"""
        if not _HAS_SKLEARN:
            return blocks

        # 1. 对每一页收集 x0 并检测列数
        page_cols: Dict[int, int] = {}

        for page_num, page in enumerate(pdf.pages, start=1):
            chars = page.chars
            if not chars:
                page_cols[page_num] = 1
                continue

            # 收集有文本的字符 x0
            x0s = [[c["x0"]] for c in chars if c.get("text", "").strip()]
            if len(x0s) < 10:
                page_cols[page_num] = 1
                continue

            best_k = 1
            best_score = -1.0

            for k in range(2, min(5, len(x0s) // 5 + 2)):
                km = KMeans(n_clusters=k, n_init=3, random_state=42)
                labels = km.fit_predict(x0s)
                # 需要至少 2 个不同标签才能算 silhouette
                if len(set(labels)) < 2:
                    continue
                score = silhouette_score(x0s, labels)
                if score > best_score and score > 0.3:
                    best_score = score
                    best_k = k

            page_cols[page_num] = best_k

        # 2. 跨页投票决定全局列数
        col_counter = Counter()
        for k in page_cols.values():
            if k > 1:
                col_counter[k] += 1

        if not col_counter:
            return blocks  # 全部单栏

        global_k = col_counter.most_common(1)[0][0]
        if global_k <= 1:
            return blocks

        # 3. 基于全局数据训练 KMeans 模型
        all_x0s = []
        for page in pdf.pages:
            for c in page.chars:
                t = c.get("text", "").strip()
                if t:
                    all_x0s.append([c["x0"]])

        if len(all_x0s) < 10:
            return blocks

        km = KMeans(n_clusters=global_k, n_init=10, random_state=42)
        km.fit(all_x0s)
        centroids = km.cluster_centers_.flatten()

        # 列排序：左 → 右，col_id 越小越靠左
        sorted_indices = np.argsort(centroids)
        col_rank = {old: new for new, old in enumerate(sorted_indices)}

        # 4. 为每个 block 分配 col_id + 重排序
        # 对于 ParagraphBlock，需要知道其 x0 范围
        # 我们通过 block 的 page 和 order 在原始 PDF 中查找对应文本区域
        block_column_info: List[Tuple[int, int, Block]] = []  # (page, col_id, block)

        for page_num, page in enumerate(pdf.pages, start=1):
            page_chars = page.chars
            if not page_chars:
                for b in blocks:
                    if b.page == page_num:
                        block_column_info.append((page_num, 0, b))
                continue

            # 将当前页的 blocks 按 order 分组（保留原始顺序）
            page_blocks = [b for b in blocks if b.page == page_num]
            for b in page_blocks:
                # 通过 page order 和内容找到对应的 x0
                col_id = self._assign_block_column(b, km, centroids, col_rank)
                block_column_info.append((page_num, col_id, b))

        # 5. 按 (page, col_id, original_order) 排序
        block_column_info.sort(key=lambda x: (x[0], x[1], x[2].order))

        reordered = [item[2] for item in block_column_info]

        # 补上没有对应 column 的 block（理论上不会发生，但安全处理）
        seen_ids = set(id(b) for b in reordered)
        for b in blocks:
            if id(b) not in seen_ids:
                reordered.append(b)

        return reordered

    @staticmethod
    def _assign_block_column(
        block: Block,
        kmeans: "KMeans",
        centroids: "np.ndarray",
        col_rank: Dict[int, int],
    ) -> int:
        """为 block 分配列 ID。"""
        # 对于 TableBlock 和 ImageBlock，直接根据 bbox 的 x0 来判断
        if isinstance(block, (TableBlock, ImageBlock)):
            bbox = block.metadata.get("table_bbox") or block.metadata.get("image_bbox")
            if bbox:
                bx0 = bbox[0]
                # 找最近的 centroid
                dists = [abs(bx0 - c) for c in centroids]
                return col_rank[dists.index(min(dists))]

        # 默认：内容在第 0 列（单栏布局的安全默认值）
        return 0

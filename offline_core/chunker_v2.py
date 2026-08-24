"""D01 目标分块器：节为最小结构单位 + 首部保留 + 均分 + 父子索引。

与旧 StructureAwareChunker 的区别：
- 旧：第X条原子单元，max_chars 硬限，无父子。
- 新：节为 parent 单元，超长节按列表/段落/句/硬切分递归均分，
      分点条款携带首部引导语，短块合并，parent/child 双层入库。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .data_model import (
    Chunk,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ParagraphBlock,
    StructuredDocument,
    TableBlock,
)

# ── 分点条款识别 ──────────────────────────────────────────────────
GUIDE_RE = re.compile(
    r"第[一二三四五六七八九十百零\d]+条[^（(]*?(?:有下列情形之一的|符合下列条件之一的|符合下列情形之一的|应当认定|按照下列)[^（(]*[：:]"
)
ITEM_RE = re.compile(r"[（(]\s*([一二三四五六七八九十百零\d]+)\s*[)）]")
ARTICLE_CN_RE = re.compile(r"第([一二三四五六七八九十百零千]+)条")
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


@dataclass
class Unit:
    """结构单元（节/章/文档级）。"""
    heading_path: List[str]
    blocks: List = field(default_factory=list)
    unit_type: str = "section"  # section | chapter | document

    @property
    def text(self) -> str:
        parts = []
        for b in self.blocks:
            if isinstance(b, HeadingBlock):
                parts.append(b.content)
            else:
                parts.append(self._block_text(b))
        return "\n\n".join(p for p in parts if p)

    @property
    def body_text(self) -> str:
        parts = []
        for b in self.blocks:
            if not isinstance(b, HeadingBlock):
                parts.append(self._block_text(b))
        return "\n\n".join(p for p in parts if p)

    @staticmethod
    def _block_text(b) -> str:
        if isinstance(b, ParagraphBlock):
            return b.content
        if isinstance(b, CodeBlock):
            return b.content
        if isinstance(b, TableBlock):
            lines = [" | ".join(b.headers)]
            lines += [" | ".join(row) for row in b.rows]
            return "\n".join(lines)
        if isinstance(b, ImageBlock):
            return f"[图片：{b.alt_text or b.image_path or '未命名'}]"
        return getattr(b, "content", "")


class LegalStructureChunker:
    """目标分块器（D01 §3.3）。"""

    def __init__(
        self,
        tokenizer=None,
        L_child: int = 512,
        L_min: int = 128,
        overlap: int = 50,
    ):
        self.tokenizer = tokenizer
        self.L_child = L_child
        self.L_min = L_min
        self.overlap = overlap

    # ── token 计算 ────────────────────────────────────────────────
    def _token_len(self, text: str) -> int:
        if self.tokenizer is None:
            return len(text)  # 字符估算 fallback
        try:
            return len(self.tokenizer.encode(text, add_special_tokens=False))
        except Exception:
            return len(text)

    # ── 主入口 ────────────────────────────────────────────────────
    def chunk(self, doc: StructuredDocument, metadata_extra: dict | None = None):
        self._metadata_extra = dict(metadata_extra or {})
        units = self._build_units(doc.blocks)
        parents: List[Chunk] = []
        children: List[Chunk] = []
        order = 0

        for unit in units:
            text = unit.text
            body = unit.body_text or text
            if not body.strip():
                continue
            unit_len = self._token_len(body)

            # 生成 parent chunk
            parent = self._make_chunk(
                doc_id=doc.doc_id,
                text=text,
                heading_path=unit.heading_path,
                order=order,
                chunk_level="parent",
                metadata_extra=self._metadata_extra,
            )
            order += 1

            # 生成 child 块
            if unit_len <= self.L_child:
                child_texts = [body]
            else:
                child_texts = self._split_long_unit(body)
                child_texts = self._merge_short(child_texts)

            # 去重：相邻相同文本合并
            child_texts = self._dedupe_adjacent(child_texts)

            child_chunks: List[Chunk] = []
            for i, ct in enumerate(child_texts):
                child = self._make_chunk(
                    doc_id=doc.doc_id,
                    text=ct,
                    heading_path=unit.heading_path,
                    order=order,
                    chunk_level="child",
                    metadata_extra=self._metadata_extra,
                )
                order += 1
                child_chunks.append(child)

            # 父子关联 + 邻接
            parent.child_chunk_ids = [c.chunk_id for c in child_chunks]
            for c in child_chunks:
                c.parent_chunk_id = parent.chunk_id
            for prev, nxt in zip(child_chunks, child_chunks[1:]):
                prev.next_chunk_id = nxt.chunk_id
                nxt.prev_chunk_id = prev.chunk_id

            parents.append(parent)
            children.extend(child_chunks)

        return parents, children

    # ── 结构单元构建 ──────────────────────────────────────────────
    def _build_units(self, blocks: List) -> List[Unit]:
        """按 节（h2）> 章（h1）> 文档 三级划分结构单元。"""
        # 统计可用 heading 层级
        levels = {b.level for b in blocks if isinstance(b, HeadingBlock)}
        section_level = 2 if 2 in levels else (1 if 1 in levels else None)

        units: List[Unit] = []
        current = None
        path_stack: List[tuple[int, str]] = []  # (level, content)

        def push_unit(heading_path: List[str], unit_type: str):
            nonlocal current
            current = Unit(heading_path=list(heading_path), unit_type=unit_type)
            units.append(current)

        for b in blocks:
            if isinstance(b, HeadingBlock):
                # 弹出 >= 当前 level 的旧 heading
                while path_stack and path_stack[-1][0] >= b.level:
                    path_stack.pop()
                path_stack.append((b.level, b.content))
                heading_path = [c for _, c in path_stack]

                if section_level is not None and b.level == section_level:
                    # 新节/章单元
                    push_unit(heading_path, "section" if b.level == 2 else "chapter")
                    current.blocks.append(b)
                elif current is None:
                    push_unit(heading_path, "document")
                    current.blocks.append(b)
                else:
                    # 更低级标题：归入当前单元
                    current.blocks.append(b)
                continue

            if current is None:
                push_unit([], "document")
            current.blocks.append(b)

        return units

    # ── 超长单元切分 ──────────────────────────────────────────────
    def _split_long_unit(self, text: str) -> List[str]:
        # 1) 表格文本：按行切，每块重复表头（简易版）
        if self._is_table_text(text):
            return self._split_table(text)
        # 2) 分点条款：截断 + 补首部引导语
        guide, items = self._extract_items(text)
        if guide and len(items) >= 2:
            return self._split_items(guide, items)
        # 3) 递归切分：自然段落 → 自然句 → 硬切分
        return self._split_recursive(text)

    def _is_table_text(self, text: str) -> bool:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return sum(1 for ln in lines if TABLE_LINE_RE.match(ln)) >= 3

    def _split_table(self, text: str) -> List[str]:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        header = lines[0]
        body = lines[1:]
        chunks: List[str] = []
        cur = [header]
        cur_len = self._token_len(header)
        for ln in body:
            ln_len = self._token_len(ln)
            if cur_len + ln_len > self.L_child and len(cur) > 1:
                chunks.append("\n".join(cur))
                cur = [header, ln]
                cur_len = self._token_len(header) + ln_len
            else:
                cur.append(ln)
                cur_len += ln_len
        if len(cur) > 1:
            chunks.append("\n".join(cur))
        return chunks or [text]

    def _extract_items(self, text: str):
        """识别"引导句 + 分点列表"。返回 (guide, items)。"""
        m = GUIDE_RE.search(text)
        if not m:
            return None, []
        guide_end = m.end()
        guide = text[:guide_end]
        rest = text[guide_end:]
        # 找分点
        matches = list(ITEM_RE.finditer(rest))
        if len(matches) < 2:
            return None, []
        items = []
        for i, mm in enumerate(matches):
            start = mm.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(rest)
            items.append(rest[start:end].strip())
        return guide, items

    def _split_items(self, guide: str, items: List[str]) -> List[str]:
        """按分点均分成若干块，每块携带首部引导语。"""
        guide_len = self._token_len(guide)
        chunks: List[str] = []
        cur_items: List[str] = []
        cur_len = guide_len
        for it in items:
            it_len = self._token_len(it)
            if cur_items and cur_len + it_len > self.L_child:
                chunks.append(guide + "".join(cur_items))
                cur_items = [it]
                cur_len = guide_len + it_len
            else:
                cur_items.append(it)
                cur_len += it_len
        if cur_items:
            chunks.append(guide + "".join(cur_items))
        return chunks or [guide]

    def _split_recursive(self, text: str, depth: int = 0) -> List[str]:
        """自然段落 → 自然句 → 硬切分，选最接近中点，均分原则。"""
        if depth > 10:
            return self._hard_split(text)
        paras = [c for c in re.split(r"\n\s*\n", text.strip()) if c.strip()]
        if len(paras) > 1:
            return self._split_by_candidates(text, paras, self._split_recursive, depth + 1)
        sents = [c for c in re.split(r"(?<=[。；;])(?![^《]*》)", text) if c.strip()]
        if len(sents) > 1:
            return self._split_by_candidates(text, sents, self._split_recursive, depth + 1)
        return self._hard_split(text)

    def _split_by_candidates(self, text, candidates, recursive_fn, depth: int = 0) -> List[str]:
        """把候选片段均分成 N 块，选最接近中点的切点。"""
        total = self._token_len(text)
        n = max(1, -(-total // self.L_child))
        if n <= 1 or len(candidates) < 2:
            return self._hard_split(text)
        target = total / n
        lens = [self._token_len(c) for c in candidates]
        prefix = [0]
        for ln in lens:
            prefix.append(prefix[-1] + ln)
        chunks = []
        start_idx = 0
        for k in range(1, n):
            ideal = target * k
            best = start_idx + 1
            if best >= len(candidates):
                break
            best_diff = abs(prefix[best] - ideal)
            for j in range(start_idx + 1, len(candidates)):
                diff = abs(prefix[j] - ideal)
                if diff < best_diff:
                    best_diff = diff
                    best = j
                else:
                    break
            if best <= start_idx:
                best = start_idx + 1
            piece = "".join(candidates[start_idx:best])
            if not piece.strip():
                start_idx = best
                continue
            if self._token_len(piece) > self.L_child:
                if piece == text:
                    chunks.extend(self._hard_split(piece))
                else:
                    chunks.extend(recursive_fn(piece, depth))
            else:
                chunks.append(piece)
            start_idx = best
        last = "".join(candidates[start_idx:])
        if last.strip():
            if self._token_len(last) > self.L_child:
                if last == text:
                    chunks.extend(self._hard_split(last))
                else:
                    chunks.extend(recursive_fn(last, depth))
            else:
                chunks.append(last)
        return [c for c in chunks if c.strip()]

    def _hard_split(self, text: str) -> List[str]:
        text = text.strip()
        total = self._token_len(text)
        if total <= self.L_child:
            return [text]
        n = -(-total // self.L_child)
        char_target = len(text) / n
        chunks = []
        start = 0
        for k in range(1, n):
            ideal = int(char_target * k)
            cut = ideal
            for delta in range(0, 80):
                for pos in (ideal + delta, ideal - delta):
                    if 0 < pos < len(text) and text[pos] in "。；;！？\n":
                        cut = pos + 1
                        break
                else:
                    continue
                break
            chunks.append(text[start:cut])
            start = cut
        chunks.append(text[start:])
        return [c for c in chunks if c.strip()]

    # ── 短块合并与去重 ────────────────────────────────────────────
    def _merge_short(self, texts: List[str]) -> List[str]:
        merged: List[str] = []
        for t in texts:
            if merged and self._token_len(merged[-1]) < self.L_min:
                cand = merged[-1] + "\n" + t
                if self._token_len(cand) <= self.L_child:
                    merged[-1] = cand
                    continue
            merged.append(t)
        if len(merged) >= 2 and self._token_len(merged[-1]) < self.L_min:
            cand = merged[-2] + "\n" + merged[-1]
            if self._token_len(cand) <= self.L_child:
                merged[-2] = cand
                merged.pop()
        return merged

    def _dedupe_adjacent(self, texts: List[str]) -> List[str]:
        out = []
        for t in texts:
            if out and out[-1] == t:
                continue
            out.append(t)
        return out

    # ── Chunk 构造 ────────────────────────────────────────────────
    def _make_chunk(
        self,
        doc_id: str,
        text: str,
        heading_path: List[str],
        order: int,
        chunk_level: str,
        metadata_extra: dict | None = None,
    ) -> Chunk:
        cid = "chunk:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        meta = dict(metadata_extra or {})
        m = ARTICLE_CN_RE.search(text)
        if m:
            try:
                from .docx_parser import chinese_to_arabic
                meta["article_no"] = str(chinese_to_arabic(m.group(1)))
            except Exception:
                pass
        return Chunk(
            chunk_id=cid,
            doc_id=doc_id,
            text=text,
            metadata=meta,
            block_ids=[],
            heading_path=list(heading_path),
            order=order,
            token_count=self._token_len(text),
            chunk_level=chunk_level,
        )

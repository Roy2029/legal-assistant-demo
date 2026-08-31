from typing import List

from pydantic import BaseModel
from .data_model import Chunk, StructuredDocument, HeadingBlock, ParagraphBlock, CodeBlock, TableBlock, ImageBlock, ParentChildChunks
from .manifest import compute_chunk_id
from .modules import Chunker,log_module

class StructureAwareChunker:
    """
    基于结构的chunker，核心是维护一个heading stack，遇到heading就更新stack，
    遇到内容块就根据当前stack生成chunk，这样每个chunk就有了明确的上下文结构信息，方便后续的检索和生成
    另外对于code块，单独成chunk，并在metadata里标记类型和语言，方便后续特殊处理
    """
    def __init__(self, max_chars=1000, token_estimator=None):
        self.max_chars = max_chars
        self.token_estimator = token_estimator

    def update_heading_stack(self, stack, heading_block):

        level = heading_block.level
        # 截断到上一层
        stack = stack[:level - 1]
        stack.append(heading_block.content)
        return stack
    """
    遇到 H3 时，level=3，stack[:2] 保留前两个元素（H1+H2），再追加当前的 H3
    遇到 H2 时，level=2，stack[:1] 只保留 H1，再追加当前的 H2
    """

    def _build_atomic_units(self, blocks) -> List[List]:
        """将 blocks 按 article 边界分组为原子单元。

        原子单元定义：
          article 单元 = 一个 article ParagraphBlock + 其后所有普通 ParagraphBlock
            （直到下一个 article 或 heading/special block）
          非法律文档：每个 ParagraphBlock 单独成单元，保留原积累行为
          HeadingBlock / CodeBlock / TableBlock / ImageBlock → 各自独立单元

        文章条款（第X条）是一个不可分割的原子单元，
        同一个 chunk 可以包含多个原子单元，但不会切分单元内部。
        """
        # 先扫描有无 article 标记（决定如何分组）
        has_article = any(
            isinstance(b, ParagraphBlock) and b.metadata.get("type") == "article"
            for b in blocks
        )

        units = []
        preamble = []   # 第一个 article 之前的段落（会归入第一个 article 单元）
        current = []    # 当前 article 单元

        for block in blocks:
            # ── heading / code / table / image：各自独立成单元 ──
            if not isinstance(block, ParagraphBlock):
                # 完成当前积累的单元
                if current:
                    units.append(current)
                    current = []
                elif preamble:
                    units.append(preamble)
                    preamble = []
                units.append([block])
                continue

            # ── ParagraphBlock ──
            if has_article and block.metadata.get("type") == "article":
                # 新 article → 前一个 article 单元完成
                if current:
                    units.append(current)
                elif preamble:
                    # 第一个 article：前导段落归入此单元
                    current = preamble
                    preamble = []
                current = [block]
            elif current:
                # 当前 article 单元的附属段落
                current.append(block)
            elif has_article:
                # 第一个 article 之前的段落，暂存
                preamble.append(block)
            else:
                # 无 article 标记：每个段落单独成单元（等价于原行为）
                units.append([block])

        # 收尾
        if current:
            units.append(current)
        elif preamble:
            units.append(preamble)

        return units

    def chunk(self, doc: StructuredDocument):

        chunks = []
        heading_stack = []
        current_text = []
        current_block_ids = []
        order = 0

        def flush_chunk():

            nonlocal current_text
            nonlocal current_block_ids
            nonlocal order

            if not current_text:
                return

            text = "\n\n".join(current_text)

            # 计算 token_count
            tc = None
            if self.token_estimator:
                try:
                    tc = self.token_estimator.estimate_text(text)
                except Exception:
                    tc = len(text)
            else:
                tc = len(text)

            chunks.append(
                Chunk(
                    chunk_id=compute_chunk_id(doc.doc_id, order),
                    doc_id=doc.doc_id,
                    text=text,
                    block_ids=current_block_ids.copy(),
                    heading_path=heading_stack.copy(),
                    order=order,
                    token_count=tc,
                )
            )

            order += 1

            current_text = []
            current_block_ids = []

        # 预处理：将 blocks 分组为原子单元
        units = self._build_atomic_units(doc.blocks)

        for unit in units:
            first = unit[0]

            # ── heading ──
            if isinstance(first, HeadingBlock):

                flush_chunk()

                heading_stack = self.update_heading_stack(
                    heading_stack,
                    first
                )

                continue

            # ── code / table / image：单独成 chunk ──
            if isinstance(first, (CodeBlock, TableBlock, ImageBlock)):

                flush_chunk()

                meta: dict = {"type": type(first).__name__.replace("Block", "").lower()}
                if isinstance(first, CodeBlock) and first.language:
                    meta["language"] = first.language
                elif isinstance(first, TableBlock):
                    meta["headers"] = first.headers
                    meta["num_rows"] = len(first.rows)
                    if first.page is not None:
                        meta["page"] = first.page
                elif isinstance(first, ImageBlock):
                    if first.image_path:
                        meta["image_path"] = first.image_path
                    if first.page is not None:
                        meta["page"] = first.page

                # 计算 token_count
                tc = None
                if self.token_estimator:
                    try:
                        tc = self.token_estimator.estimate_text(first.content)
                    except Exception:
                        tc = len(first.content)
                else:
                    tc = len(first.content)

                chunks.append(
                    Chunk(
                        chunk_id=compute_chunk_id(doc.doc_id, order),
                        doc_id=doc.doc_id,
                        text=first.content,
                        block_ids=[first.block_id],
                        heading_path=heading_stack.copy(),
                        order=order,
                        metadata=meta,
                        token_count=tc,
                    )
                )

                order += 1

                continue

            # ── paragraph 单元（article 或普通段落，不可分割） ──
            # 先将单元中的所有 block 文本合并为一个整体
            unit_text = "\n\n".join(b.content for b in unit)

            future_text = "\n\n".join(current_text + [unit_text])
            if len(future_text) > self.max_chars and current_text:
                flush_chunk()

            current_text.append(unit_text)
            current_block_ids.extend(b.block_id for b in unit)

        flush_chunk()

        return chunks
    

class ParentChildChunker:

    def __init__(
        self,
        parent_size: int = 1000,
        parent_overlap: int = 200,
        child_size: int = 250,
        child_overlap: int = 50,
        token_estimator=None,
    ):
        self.parent_size = parent_size
        self.parent_overlap = parent_overlap

        self.child_size = child_size
        self.child_overlap = child_overlap

        self.token_estimator = token_estimator

    # =========================
    # public
    # =========================

    def build(
        self,
        doc: StructuredDocument,
    ) -> ParentChildChunks:

        # 1. blocks -> full text
        full_text = self._build_document_text(doc)

        # 2. build parent chunks
        parent_chunks = self._split_parents(
            text=full_text,
            doc=doc,
        )

        # 3. build child chunks
        child_chunks = []

        child_order = 0

        for parent in parent_chunks:

            children = self._split_children(
                parent_chunk=parent,
                order_start=child_order,
            )

            child_order += len(children)

            child_chunks.extend(children)

            # 建立 parent -> child
            parent.child_chunk_ids.extend(
                [c.chunk_id for c in children]
            )

        # 4. 建立 child 前后关系
        self._link_neighbors(child_chunks)

        # 5. 建立 parent 前后关系
        self._link_neighbors(parent_chunks)

        return ParentChildChunks(
            parent_chunks=parent_chunks,
            child_chunks=child_chunks,
        )

    # =========================
    # document
    # =========================

    def _build_document_text(
        self,
        doc: StructuredDocument,
    ) -> str:

        texts = []

        for block in doc.blocks:
            texts.append(block.content)

        return "\n".join(texts)

    # =========================
    # parent split
    # =========================

    def _split_parents(
        self,
        text: str,
        doc: StructuredDocument,
    ) -> List[Chunk]:

        chunks = []

        start = 0
        order = 0

        while start < len(text):

            end = min(
                start + self.parent_size,
                len(text)
            )

            chunk_text = text[start:end]

            tc = (
                self.token_estimator.estimate_text(chunk_text)
                if self.token_estimator
                else len(chunk_text)
            )

            chunk = Chunk(
                chunk_id=compute_chunk_id(doc.doc_id, order),

                doc_id=doc.doc_id,

                text=chunk_text,

                metadata=doc.metadata.copy(),

                block_ids=[],

                heading_path=[],

                order=order,

                token_count=tc,

                chunk_level="parent",
            )

            chunks.append(chunk)

            order += 1

            start += (
                self.parent_size
                - self.parent_overlap
            )

        return chunks

    # =========================
    # child split
    # =========================

    def _split_children(
        self,
        parent_chunk: Chunk,
        order_start: int,
    ) -> List[Chunk]:

        text = parent_chunk.text

        children = []

        start = 0
        order = order_start

        while start < len(text):

            end = min(
                start + self.child_size,
                len(text)
            )

            child_text = text[start:end]

            tc = (
                self.token_estimator.estimate_text(child_text)
                if self.token_estimator
                else len(child_text)
            )

            child = Chunk(
                chunk_id=compute_chunk_id(parent_chunk.doc_id, order),

                doc_id=parent_chunk.doc_id,

                text=child_text,

                metadata=parent_chunk.metadata.copy(),

                block_ids=parent_chunk.block_ids,

                heading_path=parent_chunk.heading_path,

                order=order,

                token_count=tc,

                chunk_level="child",

                parent_chunk_id=parent_chunk.chunk_id,
            )

            children.append(child)

            order += 1

            start += (
                self.child_size
                - self.child_overlap
            )

        return children

    # =========================
    # graph linking
    # =========================

    def _link_neighbors(
        self,
        chunks: List[Chunk],
    ):

        for i in range(len(chunks)):

            if i > 0:
                chunks[i].prev_chunk_id = (
                    chunks[i - 1].chunk_id
                )

            if i < len(chunks) - 1:
                chunks[i].next_chunk_id = (
                    chunks[i + 1].chunk_id
                )



"""
class FixedChunker(Chunker):
    def __init__(self, chunk_size=500, overlap=50):#配置归配置，参数归参数，数据对象归数据对象
        self.chunk_size = chunk_size
        self.overlap = overlap

    @log_module()
    def chunk(self, doc: Document) -> List[Chunk]:
        chunks = []
        text = doc.content

        i = 0
        chunk_id = 0

        while i < len(text):
            chunk_text = text[i:i+self.chunk_size]

            chunks.append(Chunk(
                chunk_id=f"{doc.doc_id}_{chunk_id}",
                doc_id=doc.doc_id,
                text=chunk_text,
                start_pos=i,
                end_pos=i + self.chunk_size
            ))

            i += self.chunk_size - self.overlap
            chunk_id += 1

        return chunks
"""
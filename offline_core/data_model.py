from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Literal, Union
from datetime import datetime
from uuid import uuid4
from dataclasses import dataclass

# chunker模块：块级结构化信息
class Chunk(BaseModel):
    # |chunk_id|doc_id|text|metadata|block_ids|heading_path|order|token_count|chunk_level|parent_chunk_id|child_chunk_ids|prev_chunk_id|next_chunk_id|
    chunk_id: str
    doc_id: str
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    block_ids: List[str]
    heading_path: List[str] = Field(default_factory=list)
    order: int
    token_count: Optional[int] = None

    # ===== parent-child =====
    chunk_level: str = "single"
    # single / parent / child / section / document
    parent_chunk_id: Optional[str] = None
    child_chunk_ids: List[str] = Field(default_factory=list)

    # ===== graph relation =====
    prev_chunk_id: Optional[str] = None
    next_chunk_id: Optional[str] = None

# embedder模块：embedding记录结构
class EmbeddingRecord(BaseModel):
    # |chunk|vector|embedding_model|dimension|content_hash|vector_type|
    chunk: Chunk
    vector: List[float]

    # embedding模型信息
    embedding_model: str
    # vector维度
    dimension: int
    # cache key
    content_hash: str
    # future:
    # dense / sparse / multi-vector
    vector_type: str = "dense"

# 暂未使用
class VectorStoreRecord(BaseModel):
    # |id|vector|text|
    id: str
    vector: List[float]
    payload: Dict[str, Any]
# 暂未使用
class IndexedDocument(BaseModel):
    # |doc_id|source|file_hash|indexed_at|chunk_ids|
    doc_id: str
    source: str
    file_hash: str
    indexed_at: datetime
    chunk_ids: list[str]

@dataclass
class SearchQuery:
    """统一的检索查询对象，封装 dense/sparse/hybrid 三种检索模式。

    供 QdrantStore.search() 使用，替代原来 FAISSStore（只接受向量）
    和 BM25Store（只接受字符串）的不统一接口。
    """

    text: str
    """原始查询文本，用于 BM25/sparse 编码。"""

    dense_vector: Optional[List[float]] = None
    """预计算的 dense embedding 向量（dense/hybrid 模式需要）。"""

    mode: Literal["dense", "sparse", "hybrid"] = "hybrid"
    """检索模式。"""

    filters: Optional[dict] = None
    """Qdrant payload 过滤条件（预留元数据过滤接口）。

    格式示例：
        {"must": [{"key": "doc_id", "match": {"value": "doc_001"}}]}
    """

    fusion: Literal["rrf", "dbsf"] = "rrf"
    """Hybrid 模式下的融合策略：RRF 或 DBSF（仅 Qdrant 使用）。"""

    query_id: Optional[str] = None
    """可选：评估脚本传入的 query 标识（兼容 run_recall_eval 等）。"""


# 检索召回结果结构
class RetrievalResult(BaseModel):
    # |score|chunk|
    chunk: Chunk
    score: float
    retrieval_type: str  # dense/bm25/hybrid/rerank/graph

# 文档解析单元结构
class BaseBlock(BaseModel):
    # |block_id|type|content|metadata|page|order|parent_id|
    block_id: str = Field(default_factory=lambda: str(uuid4()))
    type: str  # e.g., "heading", "paragraph", "image"
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # 位置信息（极重要）
    page: Optional[int] = None

    # 文档内位置
    order: int

    # 父级结构
    parent_id: Optional[str] = None

class HeadingBlock(BaseBlock):
    type: Literal["heading"] = "heading"

    level: int

class ParagraphBlock(BaseBlock):
    type: Literal["paragraph"] = "paragraph"

class CodeBlock(BaseBlock):
    type: Literal["code"] = "code"

    language: Optional[str] = None

class TableBlock(BaseBlock):
    type: Literal["table"] = "table"

    headers: List[str]

    rows: List[List[str]]

class ImageBlock(BaseBlock):
    type: Literal["image"] = "image"

    image_path: Optional[str] = None
    alt_text: Optional[str] = None

Block = Union[
    HeadingBlock,
    ParagraphBlock,
    CodeBlock,
    TableBlock,
    ImageBlock
]

class StructuredDocument(BaseModel):
    # |doc_id|blocks|metadata|source|
    doc_id: str
    source: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    blocks: List[Block]



class ParentChildChunks(BaseModel):
    parent_chunks: list[Chunk]
    child_chunks: list[Chunk]

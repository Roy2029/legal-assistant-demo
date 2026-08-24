"""Manifest 模块 — 文件 ↔ 索引映射追踪 + 变更检测。

提供：
- compute_doc_id / compute_chunk_id：确定性 ID 生成
- Manifest 数据模型：文件映射的持久化
- scan()：扫描文件夹并检测新增/修改/删除
"""

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── 确定性 ID 生成（2.1） ────────────────────────────────────────


def compute_doc_id(content: bytes) -> str:
    """基于文件内容的确定性 doc_id。

    用于替代 uuid4()，保证同一内容始终产生相同 ID。
    格式：sha256:<hex_hash>
    """
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def compute_chunk_id(doc_id: str, order: int) -> str:
    """基于 doc_id + 文档内顺序的确定性 chunk_id。

    Args:
        doc_id: 所属文档的 doc_id
        order: chunk 在文档内的顺序号

    Returns:
        格式：chunk:<16 位 hex>
    """
    seed = f"{doc_id}:{order}"
    return f"chunk:{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


# ── 数据模型 ─────────────────────────────────────────────────────


@dataclass
class FileEntry:
    """文件在 manifest 中的条目信息。"""
    doc_id: str
    content_hash: str
    size: int
    indexed_at: str
    chunk_count: int


@dataclass
class Bm25State:
    """BM25 状态追踪。"""
    dirty: bool = False
    dirty_ratio: float = 0.0
    last_rebuild_at: Optional[str] = None


@dataclass
class Manifest:
    """索引 Manifest — 文件 ↔ 索引的映射关系。

    存储位置：data/indices/<kb_name>/manifest.json
    """
    version: int = 2
    kb_name: str = ""
    source_folder: str = ""
    updated_at: str = ""
    total_documents: int = 0
    total_chunks: int = 0
    vocab_size: int = 0
    embedding_model: str = ""        # 向后兼容：首个/默认 embedding 模型
    embedding_dimension: int = 0     # 向后兼容：首个/默认向量维度
    indices: dict[str, dict] = field(default_factory=dict)  # model_name → index metadata
    bm25: Bm25State = field(default_factory=Bm25State)
    files: dict[str, FileEntry] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        """将 manifest 序列化写入 JSON 文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.version,
            "kb_name": self.kb_name,
            "source_folder": self.source_folder,
            "updated_at": self.updated_at,
            "total_documents": self.total_documents,
            "total_chunks": self.total_chunks,
            "vocab_size": self.vocab_size,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "indices": self.indices,
            "bm25": asdict(self.bm25),
            "files": {
                relpath: {
                    "doc_id": entry.doc_id,
                    "content_hash": entry.content_hash,
                    "size": entry.size,
                    "indexed_at": entry.indexed_at,
                    "chunk_count": entry.chunk_count,
                }
                for relpath, entry in self.files.items()
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def load(path: Path) -> "Manifest":
        """从 JSON 文件加载 manifest。"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        bm25_data = data.get("bm25", {})
        files_data = data.get("files", {})

        manifest = Manifest(
            version=data.get("version", 1),
            kb_name=data.get("kb_name", ""),
            source_folder=data.get("source_folder", ""),
            updated_at=data.get("updated_at", ""),
            total_documents=data.get("total_documents", 0),
            total_chunks=data.get("total_chunks", 0),
            vocab_size=data.get("vocab_size", 0),
            embedding_model=data.get("embedding_model", ""),
            embedding_dimension=data.get("embedding_dimension", 0),
            indices=data.get("indices", {}),
            bm25=Bm25State(
                dirty=bm25_data.get("dirty", False),
                dirty_ratio=bm25_data.get("dirty_ratio", 0.0),
                last_rebuild_at=bm25_data.get("last_rebuild_at"),
            ),
            files={
                relpath: FileEntry(
                    doc_id=entry["doc_id"],
                    content_hash=entry["content_hash"],
                    size=entry["size"],
                    indexed_at=entry["indexed_at"],
                    chunk_count=entry["chunk_count"],
                )
                for relpath, entry in files_data.items()
            },
        )
        return manifest

    @staticmethod
    def create(
        kb_name: str,
        source_folder: str,
        total_documents: int = 0,
        total_chunks: int = 0,
        vocab_size: int = 0,
        embedding_model: str = "",
        embedding_dimension: int = 0,
    ) -> "Manifest":
        """创建一个新的空 Manifest。"""
        return Manifest(
            kb_name=kb_name,
            source_folder=source_folder,
            updated_at=datetime.now().isoformat(),
            total_documents=total_documents,
            total_chunks=total_chunks,
            vocab_size=vocab_size,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
        )

    def upsert_index(self, model_name: str, qdrant_subdir: str, dimension: int,
                     total_chunks: int = 0) -> None:
        """注册或更新一个模型索引条目。"""
        self.embedding_model = self.embedding_model or model_name
        self.embedding_dimension = self.embedding_dimension or dimension
        self.indices[model_name] = {
            "qdrant_subdir": qdrant_subdir,
            "dimension": dimension,
            "total_chunks": total_chunks,
        }
        self.updated_at = datetime.now().isoformat()

    def get_available_indices(self) -> dict[str, dict]:
        """返回所有已注册的模型索引（model_name → metadata）。

        如果 indices 为空但 legacy embedding_model 有值，自动构造兼容条目。
        """
        if self.indices:
            return dict(self.indices)
        # 向后兼容：旧 manifest（无 indices 字段）
        if self.embedding_model:
            return {
                self.embedding_model: {
                    "qdrant_subdir": "qdrant",
                    "dimension": self.embedding_dimension,
                    "total_chunks": self.total_chunks,
                }
            }
        return {}

    @staticmethod
    def load_or_create(kb_dir: Path, kb_name: str = "", source_folder: str = "") -> "Manifest":
        """如果存在则加载 manifest，否则创建空 manifest。"""
        manifest_path = kb_dir / "manifest.json"
        if manifest_path.exists():
            return Manifest.load(manifest_path)
        return Manifest.create(kb_name=kb_name, source_folder=source_folder)

    @staticmethod
    def manifest_path(index_dir: Path) -> Path:
        """返回 manifest.json 在索引目录中的完整路径。"""
        return index_dir / "manifest.json"


# ── 变更检测 ────────────────────────────────────────────────────


@dataclass
class ChangeSet:
    """变更检测结果。"""
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted)

    @property
    def total_changed(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted)


def compute_content_hash(file_path: Path) -> str:
    """计算文件内容的 sha256 哈希值（带 'sha256:' 前缀）。"""
    return f"sha256:{hashlib.sha256(file_path.read_bytes()).hexdigest()}"


def scan(source_folder: Path, manifest: Manifest) -> ChangeSet:
    """扫描源文件夹并与 manifest 比对，输出变更集。

    Args:
        source_folder: 源文档文件夹（绝对路径）
        manifest: 当前知识的 Manifest

    Returns:
        ChangeSet: 包含 added / modified / deleted 三类变更路径列表
    """
    if not source_folder.exists():
        return ChangeSet(deleted=list(sorted(manifest.files.keys())))

    # 收集当前所有文件的 content_hash
    current: dict[str, str] = {}  # 相对路径 → content_hash
    SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}
    for p in source_folder.rglob("*"):
        if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file():
            rel = str(p.relative_to(source_folder))
            current[rel] = compute_content_hash(p)

    current_paths = set(current.keys())
    known_paths = set(manifest.files.keys())

    added = current_paths - known_paths
    deleted = known_paths - current_paths
    modified = {
        path for path in current_paths & known_paths
        if current[path] != manifest.files[path].content_hash
    }

    return ChangeSet(
        added=sorted(added),
        modified=sorted(modified),
        deleted=sorted(deleted),
    )

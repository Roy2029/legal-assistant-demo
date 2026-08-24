from abc import ABC, abstractmethod
from typing import List, Generic, TypeVar

from .data_model import StructuredDocument,Chunk,EmbeddingRecord,RetrievalResult

Q = TypeVar("Q")
R = TypeVar("R")

import time
import functools
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def log_module(module_name=None):
    """
    装饰器：记录流水线模块的开始、结束及运行时长。

    Args:
        module_name (str, optional): 模块名称，默认为被装饰函数的名称。
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            name = module_name if module_name else func.__name__
            logger.info(f"[{name}] 开始执行")
            start_time = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                end_time = time.perf_counter()
                elapsed = end_time - start_time
                logger.info(f"[{name}] 执行结束，耗时 {elapsed:.4f} 秒")
        return wrapper
    return decorator

class Parser(ABC):
    @log_module()
    def parse(self, file_path: str) -> StructuredDocument:
        return self._parse_impl(file_path)

    @abstractmethod
    def _parse_impl(self, file_path: str) -> StructuredDocument:
        pass

class Chunker(ABC):
    @log_module()
    def chunk(self, doc: StructuredDocument) -> List[Chunk]:
        return self._chunk_impl(doc)

    @abstractmethod
    def _chunk_impl(self, doc: StructuredDocument) -> List[Chunk]:
        pass

class MetadataEnricher(ABC):
    @log_module()
    def enrich(self, chunk: Chunk, document: StructuredDocument) -> Chunk:
        return self._enrich_impl(chunk, document)

    @abstractmethod
    def _enrich_impl(self, chunk: Chunk, document: StructuredDocument) -> Chunk:
        pass

class Embedder(ABC):
    @log_module()
    def embed(self, chunks: List[Chunk]) -> List[EmbeddingRecord]:
        return self._embed_impl(chunks)

    @abstractmethod
    def _embed_impl(self, chunks: List[Chunk]) -> List[EmbeddingRecord]:
        pass

class BaseEmbeddingModel(ABC):

    @abstractmethod
    def embed_texts(
        self,
        texts: List[str]
    ) -> List[List[float]]:
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """返回 embedding 向量的维度。"""
        pass

class EmbeddingCache(ABC):

    @abstractmethod
    def get(self, key: str):
        pass

    @abstractmethod
    def set(self, key: str, vector):
        pass

class BaseStore(ABC, Generic[Q, R]):

    @log_module()
    def upsert(self, records: list[R]) -> None:
        return self._upsert_impl(records)
    @abstractmethod
    def _upsert_impl(self, records: list[R]) -> None:
        pass

    @log_module()
    def search(self, query: Q, top_k: int = 5) -> list[RetrievalResult]:
        return self._search_impl(query, top_k)
    @abstractmethod
    def _search_impl(self, query: Q, top_k: int = 5) -> list[RetrievalResult]:
        pass

    @log_module()
    def save(self, path: str) -> None:
        return self._save_impl(path)
    @abstractmethod
    def _save_impl(self, path: str) -> None:
        pass

    @log_module()
    def load(self, path: str) -> None:
        return self._load_impl(path)
    @abstractmethod
    def _load_impl(self, path: str) -> None:
        pass

class BaseRetriever(ABC):
    @log_module()
    def retrieve(self,query: str,top_k: int = 5) -> list[RetrievalResult]:
        return self._retrieve_impl(query, top_k)
    @abstractmethod
    def _retrieve_impl(self,query: str, top_k: int = 5) -> list[RetrievalResult]:
        pass


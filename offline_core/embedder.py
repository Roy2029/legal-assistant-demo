from typing import List
from .data_model import Chunk,EmbeddingRecord
from .modules import BaseEmbeddingModel, Embedder, EmbeddingCache,log_module
import random
import hashlib
    

class Embedder:
    def __init__(
        self,
        model: BaseEmbeddingModel,
        cache: EmbeddingCache = None,
        batch_size=32
    ):

        self.model = model
        self.cache = cache#可选的embedding cache
        self.batch_size = batch_size

    def compute_content_hash(self,text: str,model_name: str):
        content = f"{model_name}:{text}"
        return hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()

    @log_module("embed")
    def embed_chunks(self, chunks: list[Chunk]):
        records = []
        uncached_chunks = []
        uncached_hashes = []

        # ── Phase 1: cache lookup ──
        for chunk in chunks:

            content_hash = self.compute_content_hash(
                chunk.text,
                self.model.model_name
            )

            cached_vector = None
            if self.cache:
                cached_vector = self.cache.get(content_hash)

            if cached_vector is not None:
                # cache hit — 直接复用已有向量
                records.append(
                    EmbeddingRecord(

                        vector=cached_vector,
                        chunk=chunk,
                        embedding_model=self.model.model_name,
                        dimension=len(cached_vector),
                        content_hash=content_hash
                    )
                )
            else:
                # cache miss — 收集等待 batch 推理
                uncached_chunks.append(chunk)
                uncached_hashes.append(content_hash)

        # ── Phase 2: batch inference for uncached chunks ──
        for i in range(0, len(uncached_chunks), self.batch_size):

            batch_chunks = uncached_chunks[i:i+self.batch_size]
            batch_hashes = uncached_hashes[i:i+self.batch_size]

            texts = [chunk.text for chunk in batch_chunks]
            vectors = self.model.embed_texts(texts)

            for chunk, vector, content_hash in zip(
                batch_chunks,
                vectors,
                batch_hashes
            ):
                if self.cache:
                    self.cache.set(content_hash, vector)

                records.append(
                    EmbeddingRecord(

                        vector=vector,
                        chunk=chunk,
                        embedding_model=self.model.model_name,
                        dimension=len(vector),
                        content_hash=content_hash
                    )
                )

        return records


class MockEmbeddingModel(BaseEmbeddingModel):
    @property
    def model_name(self):
        return "mock-embedding"

    @property
    def dimension(self) -> int:
        return 768

    def embed_texts(self, texts):
        vectors = []
        for _ in texts:
            vectors.append([
                random.random()
                for _ in range(self.dimension)
            ])
        return vectors



class HuggingFaceEmbeddingModel(BaseEmbeddingModel):
    """基于 sentence-transformers 的真实 Embedding 模型。

    支持 HuggingFace 的 sentence-transformers 模型（在线或本地路径）。
    model_name 可以是 HuggingFace 模型名，也可以是本地模型目录路径。
    模型采用延迟加载，实例化时不触发下载。
    """

    def __init__(
        self,
        model_name: str = "local_model/bge-base-zh",
        device: str = "cuda",
        normalize: bool = True,
    ):
        self._model_name = model_name
        self._device = device
        self._normalize = normalize
        self._model = None
        self._dimension: int | None = None

    def _load_model(self):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(self._model_name, device=self._device)

    def _ensure_dimension(self):
        if self._dimension is None:
            dummy = self.embed_texts([""])
            self._dimension = len(dummy[0])

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        self._ensure_dimension()
        return self._dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            self._load_model()
        embeddings = self._model.encode(texts, normalize_embeddings=self._normalize)
        return embeddings.tolist()


class InMemoryEmbeddingCache(EmbeddingCache):
    def __init__(self):
        self.cache = {}

    def get(self, key):
        return self.cache.get(key)

    def set(self, key, vector):
        self.cache[key] = vector
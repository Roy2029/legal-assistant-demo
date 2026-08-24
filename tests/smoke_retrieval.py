"""T1.4 冒烟：加载 RAG1.0 现有 Qdrant 索引并执行混合检索。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根

RAG1_ROOT = Path("D:/个人/Research/RAG1.0")
INDEX_DIR = RAG1_ROOT / "data/indices/法律"
MODEL_DIR = RAG1_ROOT / "local_model/bge-base-zh"
COLLECTION = "chunks"

def main():
    from offline_core.store import QdrantStore, QdrantConfig
    from offline_core.embedder import HuggingFaceEmbeddingModel
    from offline_core.retriever import HybridMethod

    print("[1/5] 加载 embedding 模型:", MODEL_DIR)
    embedding = HuggingFaceEmbeddingModel(model_name=str(MODEL_DIR), device="cpu")
    print("      dimension =", embedding.dimension)

    print("[2/5] 打开 Qdrant 索引:", INDEX_DIR / "qdrant")
    cfg = QdrantConfig(
        mode="embedded",
        path=str(INDEX_DIR / "qdrant"),
        collection_name=COLLECTION,
        dense_dimension=embedding.dimension,
        enable_sparse=True,
    )
    store = QdrantStore(cfg)
    store._load_impl(str(INDEX_DIR / "qdrant"))  # 恢复 BM25 encoder
    print("      chunk 总数 =", store.count())

    print("[3/5] 检查 collection schema")
    info = store.client.get_collection(COLLECTION)
    print("      vectors:", list(info.config.params.vectors.keys()))
    print("      sparse_vectors:", list(info.config.params.sparse_vectors.keys()) if info.config.params.sparse_vectors else None)
    print("      payload_schema:", info.payload_schema if hasattr(info, 'payload_schema') else 'n/a')

    print("[4/5] 执行混合检索: '民法典第580条 违约责任的承担方式'")
    method = HybridMethod(store, embedding, mode="hybrid")
    results = method.search("民法典第580条 违约责任的承担方式", top_k=5)
    for i, r in enumerate(results, 1):
        pid = getattr(r, 'payload', None) or {}
        text = (r.chunk.text or '')[:60].replace('\n', ' ')
        print(f"      {i}. type={r.retrieval_type} score={r.score:.4f} chunk_id={r.chunk.chunk_id} level={r.chunk.chunk_level} text={text}")

    print("[5/5] 检查 payload index")
    try:
        idx = store.client.get_collection(COLLECTION)
        # qdrant-client 1.x: 通过 collection info 查看 payload schema
        print("      collection status:", idx.status)
    except Exception as e:
        print("      (payload index 查询异常)", e)

    store.close()
    print("SMOKE OK")

if __name__ == "__main__":
    sys.exit(main())

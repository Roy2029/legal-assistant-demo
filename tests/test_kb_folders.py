"""知识库文件夹管理测试：建文件夹→上传到文件夹→按文件夹过滤检索→查看分块→清理。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from server.main import app

TEST_MD = Path("D:/个人/legal-assistant-demo/data/test_upload_folder.md")

client = TestClient(app)


def test_folder_upload_filter_chunks():
    TEST_MD.write_text(
        """# 测试公司考勤管理办法

第一条 为规范公司考勤管理，制定本办法。
第二条 连续旷工三日的，公司可以解除劳动合同。
""",
        encoding="utf-8",
    )
    try:
        _run()
    finally:
        TEST_MD.unlink(missing_ok=True)


def _run():
    # 1. 建文件夹
    r = client.post("/api/kb/folders", json={"name": "test-folder"})
    assert r.status_code == 200 and r.json()["ok"], r.text

    r = client.get("/api/kb/folders")
    assert r.json()["ok"]
    assert any(f["kb_id"] == "test-folder" for f in r.json()["data"])

    # 2. 上传到该文件夹
    with open(TEST_MD, "rb") as f:
        r = client.post("/api/kb/upload", files={"file": ("test_upload_folder.md", f, "text/markdown")}, data={"kb_id": "test-folder"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    doc_id = r.json()["data"]["doc_id"]

    # 3. 按文件夹过滤检索（RetrievalService.user_folders）
    from online_core.retrieval_service import get_retrieval_service
    svc = get_retrieval_service()
    out = svc.search("连续旷工三日", user_folders=["test-folder"])
    assert len(out.results) > 0, "应命中 test-folder 内的文档"
    assert any(r.chunk.metadata.get("folder") == "test-folder" for r in out.results)
    out2 = svc.search("连续旷工三日", user_folders=["other-folder"])
    assert all(r.chunk.metadata.get("folder") != "test-folder" for r in out2.results) or len(out2.results) == 0

    # 4. 文档列表过滤
    r = client.get("/api/kb/docs", params={"folder": "test-folder"})
    assert r.json()["ok"]
    assert any(d["doc_id"] == doc_id for d in r.json()["data"])

    # 5. 查看分块
    r = client.get(f"/api/kb/docs/{doc_id}/chunks")
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert len(r.json()["data"]) >= 1

    # 6. 清理：删文档 → 删文件夹
    r = client.delete(f"/api/kb/docs/{doc_id}")
    assert r.status_code == 200 and r.json()["ok"]
    r = client.delete("/api/kb/folders/test-folder")
    assert r.status_code == 200 and r.json()["ok"]
    print("PASS folder_upload_filter_chunks")


def test_chunk_manage():
    TEST_MD.write_text(
        """# 测试公司考勤管理办法

第一条 为规范公司考勤管理，制定本办法。
第二条 连续旷工三日的，公司可以解除劳动合同。
第三条 加班的，应当支付加班费。
""",
        encoding="utf-8",
    )
    try:
        r = client.post("/api/kb/folders", json={"name": "test-folder"})
        assert r.json()["ok"]
        with open(TEST_MD, "rb") as f:
            r = client.post("/api/kb/upload", files={"file": ("test_upload_folder.md", f, "text/markdown")}, data={"kb_id": "test-folder"})
        assert r.status_code == 200 and r.json()["ok"], r.text
        doc_id = r.json()["data"]["doc_id"]

        # 取 chunk 列表
        r = client.get(f"/api/kb/docs/{doc_id}/chunks")
        assert r.json()["ok"]
        chunks = r.json()["data"]
        assert len(chunks) >= 1

        # 编辑第一个 chunk
        c0 = chunks[0]
        r = client.put(f"/api/kb/docs/{doc_id}/chunks/{c0['chunk_id']}", json={"text": "第一条 为规范公司考勤管理，制定本办法。\n第二条 连续旷工三日的，公司可以解除劳动合同。"})
        assert r.status_code == 200 and r.json()["ok"], r.text
        new_id = r.json()["data"]["chunk_id"]

        # 拆分成两个
        r = client.post(f"/api/kb/docs/{doc_id}/chunks/{new_id}/split", json={"part1": "第一条 为规范公司考勤管理，制定本办法。", "part2": "第二条 连续旷工三日的，公司可以解除劳动合同。"})
        assert r.status_code == 200 and r.json()["ok"], r.text
        ids = r.json()["data"]["chunks"]
        assert len(ids) == 2

        # 合并
        r = client.post(f"/api/kb/docs/{doc_id}/chunks/merge", json={"chunk_id1": ids[0], "chunk_id2": ids[1]})
        assert r.status_code == 200 and r.json()["ok"], r.text

        # 只有一个 chunk 时删除应被拒绝
        r = client.get(f"/api/kb/docs/{doc_id}/chunks")
        chunks = r.json()["data"]
        assert len(chunks) == 1
        r = client.delete(f"/api/kb/docs/{doc_id}/chunks/{chunks[0]['chunk_id']}")
        assert r.status_code == 200 and not r.json()["ok"]

        # 清理
        r = client.delete(f"/api/kb/docs/{doc_id}")
        assert r.json()["ok"]
        r = client.delete("/api/kb/folders/test-folder")
        assert r.json()["ok"]
        print("PASS chunk_manage")
    finally:
        TEST_MD.unlink(missing_ok=True)


if __name__ == "__main__":
    test_folder_upload_filter_chunks()
    test_chunk_manage()
    print("ALL KB FOLDER TESTS PASSED")

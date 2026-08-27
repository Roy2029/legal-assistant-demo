"""PDF 上传补测：生成→上传→user scope 检索命中→删除（M0 知识库三格式验收之一）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from server.main import app

client = TestClient(app)


def _make_pdf(path: Path):
    reportlab = pytest.importorskip("reportlab", reason="生成测试 PDF 需要 reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("STSong-Light", 12)
    lines = [
        "建设工程施工合同司法解释测试文档",
        "",
        "第一条 实际施工人以转包人、违法分包人为被告起诉的，人民法院应当依法受理。",
        "第二条 实际施工人向发包人主张权利的，人民法院可以追加转包人或者违法分包人为本案当事人。",
        "第三条 发包人在欠付工程价款范围内对实际施工人承担责任。",
        "",
        "本测试文档用于验证 PDF 上传解析与用户库检索隔离。",
    ]
    y = 800
    for line in lines:
        c.drawString(80, y, line)
        y -= 24
    c.save()


def test_pdf_upload_retrieve_delete(tmp_path):
    pdf_path = tmp_path / "test_upload.pdf"
    _make_pdf(pdf_path)

    with open(pdf_path, "rb") as f:
        r = client.post("/api/kb/upload", files={"file": ("test_upload.pdf", f, "application/pdf")})
    assert r.status_code == 200 and r.json()["ok"], r.text
    data = r.json()["data"]
    doc_id = data["doc_id"]
    assert data["children"] >= 1, f"PDF 应至少生成 1 个 chunk，实际 {data}"

    # 列表
    r = client.get("/api/kb/docs")
    assert r.json()["ok"]
    docs = [d for d in r.json()["data"] if d["doc_id"] == doc_id]
    assert len(docs) == 1

    # user scope 检索命中
    from online_core.retrieval_service import get_retrieval_service
    svc = get_retrieval_service()
    out = svc.search("实际施工人向发包人主张权利", corpus_scope="user")
    assert len(out.results) > 0, "PDF 上传后 user scope 应检索到内容"
    assert any(r.chunk.metadata.get("corpus") == "user" for r in out.results)

    # 删除
    r = client.delete(f"/api/kb/docs/{doc_id}")
    assert r.status_code == 200 and r.json()["ok"]
    out2 = svc.search("实际施工人向发包人主张权利", corpus_scope="user")
    # 只要求该 PDF 文档的 chunk 已删除（用户库里可能有其他历史文档）
    assert all(r.chunk.doc_id != doc_id for r in out2.results), "删除后仍检索到该 PDF 文档的 chunk"
    print("PASS pdf_upload_retrieve_delete")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_pdf_upload_retrieve_delete(Path(d))
    print("ALL PDF KB TESTS PASSED")

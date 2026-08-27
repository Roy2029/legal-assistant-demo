"""LegalDocxParser.detect() 回归测试。

背景：detect() 旧启发式（前 20 段章/节/条命中率>30%）误伤两类合法法律文件：
  - 分点/续段密集的税法（命中率被稀释）；
  - 以「一、二、三」编号而非「第X条」的决定/规定/决议。
修复后：非空段落 ≥2 即认可，仅挡空文档。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document as DocxDocument

from offline_core.docx_parser import LegalDocxParser


def make_docx(tmp: Path, name: str, paragraphs: list[str]) -> str:
    path = tmp / name
    doc = DocxDocument()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(path))
    return str(path)


def test_detect_item_dense_tax_law():
    """分点/续段密集的税法：前 20 段命中率 <30%，旧启发式误拒、新启发式接受。"""
    tmp = Path(tempfile.mkdtemp())
    paras = ["中华人民共和国某税法", "（2000年1月1日通过）", "第一条　下列各项所得，应当缴纳某税："]
    for i in range(20):
        paras.append(f"（{i + 1}）第{i + 1}类所得，包括对其的详细描述与说明内容；")
    paras.append("第二条　前款各项所得的应纳税所得额计算，依照本法规定执行。")
    fp = make_docx(tmp, "tax.docx", paras)
    assert LegalDocxParser().detect(fp), "分点密集税法应被识别为法律文档"


def test_detect_numeral_items_decision():
    """以「一、二、三」编号的决定：不含 第X条，旧启发式误拒、新启发式接受。"""
    tmp = Path(tempfile.mkdtemp())
    paras = [
        "全国人民代表大会常务委员会关于某事的规定",
        "（1999年12月1日通过）",
        "为了……，作如下规定：",
        "一、当事人应当自收到通知之日起三十日内办理登记手续，逾期不予办理的视为放弃；",
        "二、涉及不动产的，依照有关法律办理；",
        "三、本规定自公布之日起施行。",
    ]
    fp = make_docx(tmp, "decision.docx", paras)
    assert LegalDocxParser().detect(fp), "编号式决定应被识别为法律文档"


def test_detect_empty_doc_rejected():
    """空文档 / 纯空白段落：应拒绝。"""
    tmp = Path(tempfile.mkdtemp())
    fp = make_docx(tmp, "empty.docx", ["", "  ", ""])
    assert not LegalDocxParser().detect(fp), "空文档不应被识别"


if __name__ == "__main__":
    test_detect_item_dense_tax_law()
    test_detect_numeral_items_decision()
    test_detect_empty_doc_rejected()
    print("ALL DETECT TESTS PASSED")

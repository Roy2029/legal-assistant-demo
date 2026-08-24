"""chunker_v2 单元测试（D01 核心行为）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transformers import AutoTokenizer
from offline_core.chunker_v2 import LegalStructureChunker
from offline_core.data_model import HeadingBlock, ParagraphBlock, StructuredDocument


def make_doc(blocks):
    return StructuredDocument(doc_id="test-doc", source="test", blocks=blocks)


def H(content, level):
    return HeadingBlock(order=0, content=content, level=level)


def P(content):
    return ParagraphBlock(order=0, content=content)


TOKENIZER = AutoTokenizer.from_pretrained("D:/个人/Research/RAG1.0/local_model/bge-base-zh")


def test_small_section_single_child():
    doc = make_doc([H("第一节 一般规定", 2), P("第一条 这是简短内容。")])
    ck = LegalStructureChunker(tokenizer=TOKENIZER)
    parents, children = ck.chunk(doc)
    assert len(parents) == 1, f"parents={len(parents)}"
    assert len(children) == 1, f"children={len(children)}"
    assert children[0].parent_chunk_id == parents[0].chunk_id
    assert parents[0].child_chunk_ids == [children[0].chunk_id]
    print("PASS test_small_section_single_child")


def test_long_paragraph_recursive_split():
    long_text = "这是测试段落。" * 120  # 很长
    doc = make_doc([H("第一节 测试", 2), P(long_text)])
    ck = LegalStructureChunker(tokenizer=TOKENIZER)
    parents, children = ck.chunk(doc)
    assert len(parents) == 1
    assert len(children) > 1, f"应切分多块，实际 {len(children)}"
    for c in children:
        assert ck._token_len(c.text) <= ck.L_child, f"child 超长: {ck._token_len(c.text)}"
        assert c.parent_chunk_id == parents[0].chunk_id
    # 邻接关系
    for a, b in zip(children, children[1:]):
        assert a.next_chunk_id == b.chunk_id and b.prev_chunk_id == a.chunk_id
    print(f"PASS test_long_paragraph_recursive_split ({len(children)} children)")


def test_item_list_with_guide_preserved():
    guide = "第三十八条 有下列情形之一的，人民法院应当予以支持："
    items = [f"（{i}）第{i}种具体情形的详细描述，包含足够多的文字以模拟真实条文内容。" * 6 for i in "一二三四五六七八"]
    text = guide + "".join(items)
    doc = make_doc([H("第一节 测试", 2), P(text)])
    ck = LegalStructureChunker(tokenizer=TOKENIZER)
    parents, children = ck.chunk(doc)
    assert len(children) > 1, "分点条款超长应切分"
    for c in children:
        assert c.text.startswith(guide), f"child 缺首部引导语: {c.text[:40]}"
        assert ck._token_len(c.text) <= ck.L_child
    print(f"PASS test_item_list_with_guide_preserved ({len(children)} children)")


def test_short_merge():
    short1 = "第一条 内容简短。"  # 较短
    short2 = "第二条 内容也很简短。"
    # 两短条相邻，不应各自为块后存在过短块；应合并或保持在合理范围
    doc = make_doc([H("第一节 测试", 2), P(short1), P(short2)])
    ck = LegalStructureChunker(tokenizer=TOKENIZER)
    parents, children = ck.chunk(doc)
    # 该节整体不超长 → 单 child，两短条自然在同一 child 内
    assert len(children) == 1
    assert short1 in children[0].text and short2 in children[0].text
    print("PASS test_short_merge")


if __name__ == "__main__":
    test_small_section_single_child()
    test_long_paragraph_recursive_split()
    test_item_list_with_guide_preserved()
    test_short_merge()
    print("ALL CHUNKER V2 TESTS PASSED")

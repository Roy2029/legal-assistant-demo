"""检索层知识库选择器过滤条件测试（D09 阶段2）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_core.retrieval_service import build_kb_filters


def test_folders_all():
    assert build_kb_filters(folders=[]) is None


def test_folders_public_only():
    f = build_kb_filters(folders=["__public__"])
    assert f["must"] == [{"key": "metadata.corpus", "match": {"value": "public"}}]


def test_folders_user_only():
    f = build_kb_filters(folders=["施工合同", "default"])
    assert f["must"][0] == {"key": "metadata.corpus", "match": {"value": "user"}}
    assert {"key": "metadata.folder", "match": {"any": ["施工合同", "default"]}} in f["must"]


def test_folders_public_plus_user_or():
    f = build_kb_filters(folders=["__public__", "施工合同"])
    assert "should" in f and f["should"] is not None
    assert f["should"][0] == {"key": "metadata.corpus", "match": {"value": "public"}}
    nested = f["should"][1]
    assert nested["must"][2] == {"key": "metadata.folder", "match": {"any": ["施工合同"]}}


def test_folders_with_article_filter():
    f = build_kb_filters(folders=["__public__"], pq_filter={"article_no": "580", "law_name": "中华人民共和国民法典"})
    assert {"key": "metadata.articles", "match": {"any": ["580"]}} in f["must"]
    assert {"key": "metadata.law_name", "match": {"value": "中华人民共和国民法典"}} in f["must"]


def test_backward_compat_corpus_scope():
    assert build_kb_filters(corpus_scope="public") == {"must": [{"key": "metadata.corpus", "match": {"value": "public"}}]}
    assert build_kb_filters(user_folders=["a"]) is not None


if __name__ == "__main__":
    test_folders_all()
    test_folders_public_only()
    test_folders_user_only()
    test_folders_public_plus_user_or()
    test_folders_with_article_filter()
    test_backward_compat_corpus_scope()
    print("ALL SEARCH FOLDER FILTER TESTS PASSED")

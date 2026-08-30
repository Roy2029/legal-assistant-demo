"""检索层知识库选择器过滤条件测试（D09 阶段2）。

覆盖公共库作用域修复：corpus=="public" 或缺失（历史公共 chunk 无 corpus 标签），
且不得匹配 user/case chunk。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_core.retrieval_service import _corpus_public_condition, build_kb_filters


def _matches(condition, payload):
    """极简过滤求值：仅覆盖公共库 corpus 条件用到的 match / is_empty / should / must。"""
    if "key" in condition and "match" in condition:
        key = condition["key"].removeprefix("metadata.")
        meta = payload.get("metadata", {})
        actual = meta.get(key)
        if "any" in condition["match"]:
            vals = actual if isinstance(actual, list) else [actual]
            return bool(set(vals or []) & set(condition["match"]["any"]))
        return actual == condition["match"]["value"]
    if "is_empty" in condition:
        key = condition["is_empty"]["key"].removeprefix("metadata.")
        actual = payload.get("metadata", {}).get(key)
        return actual is None or actual == "" or actual == []
    if "should" in condition:
        return any(_matches(sub, payload) for sub in condition["should"])
    if "must" in condition:
        return all(_matches(sub, payload) for sub in condition["must"])
    raise ValueError(f"unsupported condition: {condition}")


PUBLIC_COND = _corpus_public_condition()


def test_folders_all():
    assert build_kb_filters(folders=[]) is None


def test_folders_public_only():
    f = build_kb_filters(folders=["__public__"])
    assert f["must"] == [PUBLIC_COND]


def test_folders_user_only():
    f = build_kb_filters(folders=["施工合同", "default"])
    assert f["must"][0] == {"key": "metadata.corpus", "match": {"value": "user"}}
    assert {"key": "metadata.folder", "match": {"any": ["施工合同", "default"]}} in f["must"]


def test_folders_public_plus_user_or():
    f = build_kb_filters(folders=["__public__", "施工合同"])
    assert "should" in f and f["should"] is not None
    assert f["should"][0] == PUBLIC_COND
    nested = f["should"][1]
    assert nested["must"][2] == {"key": "metadata.folder", "match": {"any": ["施工合同"]}}


def test_folders_with_article_filter():
    f = build_kb_filters(folders=["__public__"], pq_filter={"article_no": "580", "law_name": "中华人民共和国民法典"})
    assert {"key": "metadata.articles", "match": {"any": ["580"]}} in f["must"]
    assert {"key": "metadata.law_name", "match": {"value": "中华人民共和国民法典"}} in f["must"]


def test_backward_compat_corpus_scope():
    assert build_kb_filters(corpus_scope="public") == {"must": [PUBLIC_COND]}
    assert build_kb_filters(user_folders=["a"]) is not None


def test_public_condition_shape_is_qdrant_valid():
    # 形态必须能被嵌入式 Qdrant 的 Filter 模型接受（is_empty 为独立条件，非 FieldCondition 字段）
    assert PUBLIC_COND == {
        "should": [
            {"key": "metadata.corpus", "match": {"value": "public"}},
            {"is_empty": {"key": "metadata.corpus"}},
        ]
    }


def test_public_condition_matches_public_and_missing():
    # corpus=="public" 的 chunk
    assert _matches(PUBLIC_COND, {"metadata": {"corpus": "public"}})
    # 缺失 corpus 字段的历史公共 chunk（仅 law_name 等）
    assert _matches(PUBLIC_COND, {"metadata": {"law_name": "中华人民共和国民法典", "article_no": "580"}})
    # corpus 为空的 chunk
    assert _matches(PUBLIC_COND, {"metadata": {"corpus": ""}})
    # 完全无 metadata 的 chunk
    assert _matches(PUBLIC_COND, {})


def test_public_condition_rejects_user_and_case():
    assert not _matches(PUBLIC_COND, {"metadata": {"corpus": "user"}})
    assert not _matches(PUBLIC_COND, {"metadata": {"corpus": "case"}})


def test_public_scope_recalls_missing_corpus():
    # folders=["__public__"] 与 corpus_scope="public" 两条路径都必须命中缺失 corpus 的 chunk
    for f in (build_kb_filters(folders=["__public__"]), build_kb_filters(corpus_scope="public")):
        assert _matches(f, {"metadata": {"law_name": "中华人民共和国民法典"}})
        assert _matches(f, {"metadata": {"corpus": "public"}})
        assert not _matches(f, {"metadata": {"corpus": "user"}})
        assert not _matches(f, {"metadata": {"corpus": "case"}})


def test_public_plus_user_keeps_user_isolation():
    f = build_kb_filters(folders=["__public__", "施工合同"])
    should = f["should"]
    assert should[0] == PUBLIC_COND
    # 用户分支仍要求 corpus=user + user_id + folder，公共条件不匹配 user/case chunk
    user_cond = should[1]
    assert {"key": "metadata.corpus", "match": {"value": "user"}} in user_cond["must"]
    assert {"key": "metadata.user_id", "match": {"value": "local"}} in user_cond["must"]
    assert not _matches(should[0], {"metadata": {"corpus": "user"}})
    assert not _matches(should[0], {"metadata": {"corpus": "case"}})


def test_user_scope_unaffected():
    # 用户文件夹作用域不引入公共条件，公共 chunk 不会混入
    f = build_kb_filters(folders=["施工合同"])
    assert f["must"][0] == {"key": "metadata.corpus", "match": {"value": "user"}}
    assert {"key": "metadata.user_id", "match": {"value": "local"}} in f["must"]
    assert {"key": "metadata.folder", "match": {"any": ["施工合同"]}} in f["must"]
    assert PUBLIC_COND not in f["must"]
    assert not _matches(f, {"metadata": {"law_name": "中华人民共和国民法典"}})

    f2 = build_kb_filters(user_folders=["a"])
    assert {"key": "metadata.corpus", "match": {"value": "user"}} in f2["must"]
    assert PUBLIC_COND not in f2["must"]


if __name__ == "__main__":
    test_folders_all()
    test_folders_public_only()
    test_folders_user_only()
    test_folders_public_plus_user_or()
    test_folders_with_article_filter()
    test_backward_compat_corpus_scope()
    test_public_condition_shape_is_qdrant_valid()
    test_public_condition_matches_public_and_missing()
    test_public_condition_rejects_user_and_case()
    test_public_scope_recalls_missing_corpus()
    test_public_plus_user_keeps_user_isolation()
    test_user_scope_unaffected()
    print("ALL SEARCH FOLDER FILTER TESTS PASSED")

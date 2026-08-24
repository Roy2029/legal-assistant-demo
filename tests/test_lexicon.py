import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jieba
from online_core.lexicon_service import add_term, apply_user_lexicon, list_terms

def test_user_lexicon_query_side():
    add_term("实际施工人")
    n = apply_user_lexicon()
    assert n >= 1
    tokens = jieba.lcut("实际施工人能否向发包人主张工程款")
    assert "实际施工人" in tokens, tokens
    print("PASS user_lexicon_query_side:", tokens)

if __name__ == "__main__":
    test_user_lexicon_query_side()
    print("ALL LEXICON TESTS PASSED")

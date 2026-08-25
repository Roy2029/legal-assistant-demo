import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.prefilter import prefilter

def test_trivial_blocked():
    assert prefilter("你好")["passed"] is False
    assert prefilter("今天天气怎么样")["passed"] is False
    assert prefilter("")["passed"] is False
    print("PASS trivial_blocked")

def test_legal_passed():
    assert prefilter("民法典第32条说了什么？")["passed"] is True
    assert prefilter("老板不给工资怎么办")["passed"] is True
    assert prefilter("合同违约怎么起诉")["passed"] is True
    print("PASS legal_passed")

if __name__ == "__main__":
    test_trivial_blocked(); test_legal_passed()
    print("ALL PREFILTER TESTS PASSED")

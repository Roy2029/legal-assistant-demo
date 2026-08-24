import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from online_core.difficulty import estimate


def test_simple():
    r = estimate("民法典第580条说了什么")
    assert r["level"] == "simple" and r["top_k"] == 5, r
    print("PASS simple")


def test_compare_hard():
    r = estimate("实际施工人向发包人主张工程款与向转包人主张有何区别")
    assert r["level"] == "hard" and r["top_k"] == 10, r
    print("PASS compare_hard")


def test_long_hard():
    r = estimate("这是一个非常长的问题" + "字" * 50)
    assert r["level"] == "hard" and r["top_k"] == 10, r
    print("PASS long_hard")


def test_procedure_medium():
    r = estimate("如何认定实际施工人")
    assert r["level"] == "medium" and r["top_k"] == 8, r
    print("PASS procedure_medium")


def test_default_medium():
    r = estimate("实际施工人能否向发包人主张工程款")
    assert r["level"] == "medium" and r["top_k"] == 8, r
    print("PASS default_medium")


if __name__ == "__main__":
    test_simple(); test_compare_hard(); test_long_hard(); test_procedure_medium(); test_default_medium()
    print("ALL DIFFICULTY TESTS PASSED")

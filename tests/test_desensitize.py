import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.desensitize import desensitize, restore

def test_phone_company():
    text = "张三在华为技术有限公司工作，电话13800138000。"
    masked, added = desensitize(text)
    assert "13800138000" not in masked
    assert "华为技术有限公司" not in masked
    assert "[phone1]" in masked and "[company1]" in masked
    restored = restore(masked)
    assert "13800138000" in restored
    assert "华为技术有限公司" in restored
    print("PASS phone_company roundtrip")

def test_no_pii_unchanged():
    text = "民法典第580条规定了违约责任。"
    masked, added = desensitize(text)
    assert masked == text
    print("PASS no_pii_unchanged")

if __name__ == "__main__":
    test_phone_company(); test_no_pii_unchanged()
    print("ALL DESENSITIZE TESTS PASSED")

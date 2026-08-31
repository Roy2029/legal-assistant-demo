"""wenshu_api 使用示例。

运行前：pip install requests （PDF 下载另需 weasyprint 或 pdfkit）

注意：本示例会真实访问 wenshu.court.gov.cn。受反爬影响，可能需要：
  - 配置 captcha_solver（见 README 4.2）
  - 校准 vl5x（见 README 4.1）
否则可能抛出 CaptchaRequiredError，属正常反爬表现。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wenshu_api import WenshuClient, CaptchaRequiredError, RateLimitError, WenshuError


def example_db_structure():
    """功能 2：查看数据库结构（纯本地，不触发反爬）。"""
    client = WenshuClient()
    struct = client.get_db_structure()
    print("=== 数据库结构 ===")
    print("可查询字段:")
    for f in struct.queryable_fields:
        print(f"  - {f.key} ({f.label}) 示例: {f.example}")
    print("案件类型:", struct.case_types)
    print("法院层级:", struct.court_levels)
    client.close()


def example_search_and_download():
    """功能 1 + 3 + 4：查询并下载（需联网且可能触发验证码）。"""
    # 如需验证码求解：client = WenshuClient(captcha_solver=my_solver)
    client = WenshuClient(max_qps=1.0)

    try:
        result = client.search(
            keyword="合同纠纷",
            case_type="民事案件",
            page=1,
            page_size=10,
        )
    except CaptchaRequiredError as e:
        print("[验证码] 需要处理验证码。图片字节长度:", len(e.captcha_image or b""))
        print("        请配置 captcha_solver 后重试。")
        return
    except RateLimitError as e:
        print(f"[限流] 等待 {e.retry_after}s 后重试。")
        return
    except WenshuError as e:
        print("[错误]", type(e).__name__, e)
        return

    print(f"=== 命中 {result.total} 条，共 {result.total_pages} 页 ===")
    for i, doc in enumerate(result.documents, 1):
        print(f"{i}. {doc.title} | {doc.case_number} | {doc.court_name} | {doc.publish_date}")

    if result.documents:
        doc_id = result.documents[0].doc_id
        if doc_id:
            try:
                path = client.download_document(doc_id, save_format="text", save_path="./downloads")
                print("文书已保存:", path)
            except WenshuError as e:
                print("[下载失败]", type(e).__name__, e)

    client.close()


if __name__ == "__main__":
    example_db_structure()
    print()
    example_search_and_download()

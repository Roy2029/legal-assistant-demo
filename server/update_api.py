"""法律库增量更新 API（W6 占位）：M0 未接入爬虫，返回明确状态。"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/update")


@router.get("/status")
def status():
    return {
        "ok": True,
        "data": {
            "status": "stub",
            "message": "M0 增量更新服务未接入爬虫；当前索引为本地构建（2026-08-27）。完整更新服务列入 M1。",
        },
    }


@router.post("/run")
def run():
    return {"ok": True, "data": {"started": False, "message": "M0 未实现增量更新，完整更新服务列入 M1；当前索引为本地构建（2026-08-27）。"}}

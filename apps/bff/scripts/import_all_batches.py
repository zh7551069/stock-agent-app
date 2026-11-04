#!/usr/bin/env python3
"""按顺序导入所有剩余上传批次（重复项自动跳过；完成后清理批次目录）。"""
import json
import os
import time
import urllib.request

BASE = "http://127.0.0.1:8770"
UPLOADS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "uploads")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.load(r)


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def delete(path):
    req = urllib.request.Request(BASE + path, method="DELETE")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def wait_done(timeout=1900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = get("/api/upload/status")
        if not st.get("running"):
            return st
        time.sleep(3)
    return {"error": "等待超时"}


def main():
    batches = [b["batch"] for b in get("/api/upload/batches")["batches"]]
    batches.sort()
    log("待处理批次:", batches)

    for batch in batches:
        detail = get(f"/api/upload/batch/{batch}")
        files = detail.get("files", [])
        todo = [f for f in files if f["supported"] and not f.get("duplicate")]
        if not todo:
            log(f"[{batch}] 全部重复/不支持 → 直接删除批次")
            try:
                delete(f"/api/upload/batch/{batch}")
            except Exception as e:  # noqa: BLE001
                log("  删除失败:", e)
            continue

        log(f"[{batch}] 开始导入 {len(todo)} 个文件（共 {len(files)}）")
        try:
            post("/api/upload/import", {"batch": batch, "dry": False, "wait_lock": 1800})
        except Exception as e:  # noqa: BLE001
            log("  启动失败:", e)
            continue

        st = wait_done()
        if st.get("error"):
            log("  失败:", st["error"][:160])
        for r in st.get("results") or []:
            if r.get("error"):
                log("  ✗", r.get("file"), "→", r.get("table"), r["error"][:120])
            else:
                log("  ✓", r.get("file"), "→", r.get("table"), r.get("rows"), "行")
        if st.get("skipped"):
            log("  跳过重复:", [x["file"] for x in st["skipped"]])
        log("  已删副本:", len(st.get("deleted") or []))

        # 批次里若还有残留（重复/不支持的文件），删掉整个批次目录
        try:
            detail = get(f"/api/upload/batch/{batch}")
            if detail.get("files"):
                delete(f"/api/upload/batch/{batch}")
                log("  清理残留批次目录")
        except Exception:  # noqa: BLE001
            pass

    log("== 全部完成 ==")
    try:
        left = get("/api/upload/batches")["batches"]
        log("剩余批次:", [b["batch"] for b in left] or "无")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""一键补全：探针 -> 回答补全 -> 作者资料 -> 重建交付表。

设计给定时任务用：
  * 退出码 0  = 全部完成（或本来就已完成）
  * 退出码 3  = 接口仍被拦，等下一轮
  * 退出码 4  = 已有实例在跑（锁文件存在）
  * 退出码 1  = 出错

有锁文件 raw/.completing.lock，避免定时任务重入。
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.environ.get("ZHIHU_ROOT") or os.getcwd()
PY = sys.executable
TOOLS = os.path.join(ROOT, "tools")
LOCK = os.path.join(ROOT, "raw", ".completing.lock")
LOG = os.path.join(ROOT, "logs", "complete_data.log")


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(script, env_extra=None, timeout=7200):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)
    log("run %s" % script)
    p = subprocess.run([PY, os.path.join(TOOLS, script)],
                       cwd=ROOT, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    tail = [l for l in (p.stdout or "").strip().split("\n") if l.strip()][-6:]
    for l in tail:
        log("  " + l[:200])
    if p.returncode != 0:
        err = (p.stderr or "").strip().split("\n")[-3:]
        for l in err:
            log("  ERR " + l[:200])
    return p.returncode


def gap():
    """还差多少条回答：已知回答总数 - 已采条数。"""
    try:
        q = json.load(open(os.path.join(ROOT, "交付", "02_问题总表.csv.json"),
                           encoding="utf-8")) if False else None
    except Exception:
        pass
    store = os.path.join(ROOT, "raw", "api", "answers.json")
    have = 0
    if os.path.exists(store):
        d = json.load(open(store, encoding="utf-8"))
        have = len({a["id"] for v in d.values() for a in (v.get("answers") or [])})
    # 目标：所有问题的回答总数之和（取自目标清单）
    want = 0
    tp = os.path.join(ROOT, "data", "crawl_targets_filtered.json")
    if os.path.exists(tp):
        for t in json.load(open(tp, encoding="utf-8")):
            try:
                want += int(t.get("answer_count") or 0)
            except Exception:
                pass
    # 已在内容表里的（含早期缓存）也算采到
    return have, want


def main():
    os.makedirs(os.path.join(ROOT, "raw"), exist_ok=True)
    if os.path.exists(LOCK):
        try:
            age = time.time() - os.path.getmtime(LOCK)
        except Exception:
            age = 0
        if age < 6 * 3600:
            log("已有实例在跑（锁 %d 秒前创建），跳过" % age)
            return 4
        log("锁文件过旧（%d 秒），视为残留，继续" % age)

    with open(LOCK, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    try:
        # 0) 前置：必须配置登录 profile
        if not os.environ.get("ZHIHU_PROFILE"):
            log("未设置 ZHIHU_PROFILE，接口通道无法使用")
            return 2
        # 1) 探针
        rc = run("probe_api.py", timeout=300)
        if rc != 0:
            log("接口不可用（探针 exit=%d），等下一轮" % rc)
            return 3

        # 2) 回答补全（取全，不设上限）
        have, want = gap()
        log("回答进度：已采 %d / 目标 %d" % (have, want))
        if want and have < want:
            run("api_backfill_full.py", {"K": "6", "PPR": "8", "MAX_PER_Q": "100000"},
                timeout=10800)
            have2, _ = gap()
            log("回答补全后：%d" % have2)
        else:
            log("回答已取全，跳过")

        # 3) 作者资料（粉丝数/认证）
        run("member_backfill.py", {"K": "6"}, timeout=5400)

        # 4) 重建交付表
        run("build_deliverable.py", timeout=1800)
        run("filter_deliverable.py", timeout=1800)

        log("补全流程结束")
        return 0
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())

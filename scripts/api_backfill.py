# -*- coding: utf-8 -*-
"""接口补采 v2：一批问题一次性并发取多页，速度最快且不炸号。

每轮：
  1. 取 K 个问题
  2. 为每个问题生成 offset=0/20/40 的 URL（按回答数决定取几页，上限 MAX_PER_Q）
  3. 一个 page_action 里 Promise.all 并发取完（K*3 个请求）
  4. 结果落盘，按问题累加

实测依据：登录态 + 手机热点下 /api/v4/questions/{qid}/answers 返回 200，
且 include 里 favlists_count、thanks_count 都有值；visits_count 为 null（接口不提供）。
"""
import json
import os
import random
import shutil
import sys
import tempfile
import time

ROOT = os.environ.get("ZHIHU_ROOT") or os.getcwd()
RAW = os.path.join(ROOT, "raw", "api")
DATA = os.path.join(ROOT, "data")
os.makedirs(RAW, exist_ok=True)

STORE = os.path.join(RAW, "answers.json")
TARGETS = os.path.join(DATA, "crawl_targets_filtered.json")

# 改成你自己的已登录 Chorme profile 目录（见 SKILL.md「登录态」一节）
SRC_PROFILE = os.environ.get("ZHIHU_PROFILE") or ""
TMP_PROFILE = os.path.join(tempfile.gettempdir(), "zhprof_backfill")

MAX_PER_Q = int(os.environ.get("MAX_PER_Q", "60"))
K = int(os.environ.get("K", "5"))          # 每轮处理几个问题
LIMIT_Q = int(os.environ.get("LIMIT_Q", "100000"))

# 关键：只有第一个字段带 data[*]. 前缀，后续用裸名，否则收藏/评论/感谢会被丢弃
# （实测 2026-10-21：写成 data[*].content,data[*].favlists_count 会拿不到 favlists_count）
INC = ("data%5B*%5D.content,favlists_count,comment_count,thanks_count,voteup_count,"
       "created_time,updated_time,author,question")


def jdump(p, o, tries=8):
    """原子写入，带重试。

    Windows 上如果别的进程正在读这个文件（例如我在另一个终端查进度），
    os.replace 会抛 PermissionError。这里重试几次而不是让整个采集崩掉。
    """
    tmp = p + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False)
    for i in range(tries):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.4 * (i + 1))
    # 最后一次仍失败：直接覆盖写，宁可短暂非原子也不丢进度
    with open(p, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False)
    try:
        os.remove(tmp)
    except Exception:
        pass


def jload(p, d=None):
    if not os.path.exists(p):
        return d
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def build_urls(qid, want):
    urls = []
    for off in range(0, min(want, MAX_PER_Q), 20):
        urls.append((off, f"https://www.zhihu.com/api/v4/questions/{qid}/answers"
                          f"?limit=20&offset={off}&sort_by=default&include={INC}"))
    return urls


def main():
    targets = jload(TARGETS, [])
    store = jload(STORE, {})

    ac_map = {}
    for t in targets:
        try:
            ac_map[t["question_id"]] = int(t.get("answer_count") or 0)
        except Exception:
            ac_map[t["question_id"]] = 0

    queue = [t["question_id"] for t in targets if t["question_id"] not in store]
    queue.sort(key=lambda q: -ac_map.get(q, 0))
    queue = queue[:LIMIT_Q]
    print("待补问题: %d | 已补: %d | 每问上限 %d" % (len(queue), len(store), MAX_PER_Q), flush=True)

    if os.path.exists(TMP_PROFILE):
        shutil.rmtree(TMP_PROFILE, ignore_errors=True)
    shutil.copytree(SRC_PROFILE, TMP_PROFILE, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(
                        "Cache", "Code Cache", "GPUCache", "ShaderCache",
                        "GrShaderCache", "Service Worker", "DawnCache",
                        "SingletonLock", "SingletonCookie", "SingletonSocket",
                        "lockfile", "*.lock"))

    from scrapling.fetchers import StealthySession

    t0 = time.time()
    ok = fail = 0
    total_answers = 0
    with StealthySession(headless=True, network_idle=False, timeout=180000,
                         max_pages=1, user_data_dir=TMP_PROFILE) as s:
        for i in range(0, len(queue), K):
            part = queue[i:i + K]
            jobs = []           # (qid, offset, url)
            for qid in part:
                for off, u in build_urls(qid, ac_map.get(qid, 0) or MAX_PER_Q):
                    jobs.append((qid, off, u))

            box = {}

            def action(page, _jobs=jobs, _box=box):
                try:
                    page.wait_for_timeout(800)
                    urls = [j[2] for j in _jobs]
                    raw = page.evaluate("""async (urls) => {
                      const rs = await Promise.all(urls.map(async (u) => {
                        try {
                          const r = await fetch(u, {credentials:'include'});
                          return {status: r.status, body: r.status===200 ? (await r.text()) : ''};
                        } catch(e) { return {error: String(e)}; }
                      }));
                      return JSON.stringify(rs);
                    }""", urls)
                    _box["rs"] = json.loads(raw) if raw else []
                except Exception as e:
                    _box["err"] = f"{type(e).__name__}: {e}"[:200]

            try:
                s.fetch("https://www.zhihu.com/", page_action=action, wait=800)
            except Exception as e:
                print("  fetch err:", str(e)[:120], flush=True)

            rs = box.get("rs") or []
            got = 0
            per_q = {}
            for (qid, off, _u), r in zip(jobs, rs):
                if not isinstance(r, dict) or r.get("status") != 200:
                    continue
                body = r.get("body") or ""
                if not body.strip().startswith("{"):
                    continue
                try:
                    j = json.loads(body)
                except Exception:
                    continue
                data = j.get("data") or []
                per_q.setdefault(qid, []).extend(data)
                got += len(data)

            for qid in part:
                rec = store.get(qid) or {"answers": [], "done": False}
                known = {a["id"]: a for a in rec["answers"]}
                for a in per_q.get(qid, []):
                    known[a["id"]] = a
                rec["answers"] = list(known.values())
                rec["count"] = len(rec["answers"])
                rec["done"] = len(rec["answers"]) >= min(ac_map.get(qid, 0) or 0, MAX_PER_Q) \
                    or len(rec["answers"]) >= MAX_PER_Q
                store[qid] = rec
            jdump(STORE, store)
            total_answers += got
            if got:
                ok += len(part)
            else:
                fail += len(part)

            done = min(i + K, len(queue))
            if done % 50 < K or done <= 10:
                el = time.time() - t0
                rate = done / el if el else 0
                eta = (len(queue) - done) / rate / 60 if rate else 0
                print("[%d/%d] 本批回答=%d 累计=%d 用时%.0fs 预计剩余%.0f分"
                      % (done, len(queue), got, total_answers, el, eta), flush=True)
            time.sleep(random.uniform(0.8, 1.6))

    print("完成: 问题=%d 新增回答=%d 用时%.0fs" % (ok, total_answers, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()

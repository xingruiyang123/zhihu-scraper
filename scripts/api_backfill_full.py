# -*- coding: utf-8 -*-
"""接口补采 v2：把每个问题的回答**取全**（不再限 60 条）。

与 v1 的区别：
  * 续采模式：已采过的问题从 next_offset 继续翻页，不重复下载
  * 不限条数：翻到 paging.is_end 为止
  * 顺带记录 paging.totals —— 用于填补"回答总数未知"的那 259 个问题
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
STORE = os.path.join(RAW, "answers.json")
TARGETS = os.path.join(DATA, "crawl_targets_filtered.json")
TOTALS = os.path.join(RAW, "paging_totals.json")

SRC_PROFILE = os.environ.get("ZHIHU_PROFILE") or ""
TMP_PROFILE = os.path.join(tempfile.gettempdir(), "zhprof_full")

K = int(os.environ.get("K", "6"))            # 每轮处理几个问题
MAX_PER_Q = int(os.environ.get("MAX_PER_Q", "100000"))
PAGES_PER_ROUND = int(os.environ.get("PPR", "6"))   # 每轮每问最多翻几页

INC = ("data%5B*%5D.content,favlists_count,comment_count,thanks_count,voteup_count,"
       "created_time,updated_time,author,question")


def jdump(p, o, tries=8):
    tmp = p + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False)
    for i in range(tries):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.4 * (i + 1))
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


def main():
    targets = jload(TARGETS, [])
    store = jload(STORE, {})
    totals = jload(TOTALS, {})

    ac = {}
    for t in targets:
        try:
            ac[t["question_id"]] = int(t.get("answer_count") or 0)
        except Exception:
            ac[t["question_id"]] = 0

    # 计算每个问题还需翻几页
    plan = {}
    for t in targets:
        qid = t["question_id"]
        rec = store.get(qid) or {}
        have = len({a["id"] for a in (rec.get("answers") or [])})
        want = ac.get(qid) or MAX_PER_Q
        if totals.get(qid):
            want = max(want, totals[qid])
        want = min(want, MAX_PER_Q)
        if have >= want:
            continue
        plan[qid] = {"have": have, "want": want, "next": rec.get("next_offset", (have // 20) * 20)}

    queue = sorted(plan.keys(), key=lambda q: -(plan[q]["want"] - plan[q]["have"]))
    print("待续采问题: %d | 已采 %d | 总目标回答 %d"
          % (len(queue), len(store), sum(plan[q]["want"] for q in queue)), flush=True)
    if not queue:
        print("全部已采全")
        return

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
    added_total = 0
    with StealthySession(headless=True, network_idle=False, timeout=180000,
                         max_pages=1, user_data_dir=TMP_PROFILE) as s:
        i = 0
        while i < len(queue):
            part = queue[i:i + K]
            jobs = []
            for qid in part:
                p = plan[qid]
                for page in range(PAGES_PER_ROUND):
                    off = p["next"] + page * 20
                    if off >= p["want"]:
                        break
                    jobs.append((qid, off,
                                 f"https://www.zhihu.com/api/v4/questions/{qid}/answers"
                                 f"?limit=20&offset={off}&sort_by=default&include={INC}"))
            if not jobs:
                i += K
                continue

            box = {}

            def action(page, _jobs=jobs, _box=box):
                try:
                    page.wait_for_timeout(700)
                    urls = [j[2] for j in _jobs]
                    raw = page.evaluate("""async (urls) => {
                      const rs = await Promise.all(urls.map(async (u) => {
                        try { const r = await fetch(u, {credentials:'include'});
                              return {status:r.status, body:r.status===200?(await r.text()):''}; }
                        catch(e){ return {error:String(e)}; }
                      }));
                      return JSON.stringify(rs);
                    }""", urls)
                    _box["rs"] = json.loads(raw) if raw else []
                except Exception as e:
                    _box["err"] = str(e)[:150]

            try:
                s.fetch("https://www.zhihu.com/", page_action=action, wait=600)
            except Exception as e:
                box["err"] = str(e)[:150]

            rs = box.get("rs") or []
            per_q = {}
            got = 0
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
                pg = j.get("paging") or {}
                if pg.get("totals"):
                    totals[qid] = pg["totals"]
                per_q.setdefault(qid, []).extend(data)
                got += len(data)

            for qid in part:
                rec = store.get(qid) or {"answers": []}
                known = {a["id"]: a for a in rec.get("answers") or []}
                page_new = per_q.get(qid, [])
                first_page_new = 0
                for a in page_new:
                    if a["id"] not in known:
                        first_page_new += 1
                    known[a["id"]] = a
                rec["answers"] = list(known.values())
                rec["count"] = len(rec["answers"])
                pl = plan[qid]
                pl["next"] += PAGES_PER_ROUND * 20
                rec["next_offset"] = pl["next"]
                rec["done"] = pl["next"] >= pl["want"] or not page_new
                store[qid] = rec
                if rec["done"]:
                    plan[qid]["next"] = pl["want"]      # 标记完成
            jdump(STORE, store)
            jdump(TOTALS, totals)
            added_total += got
            i += K

            done_q = sum(1 for q in queue if (store.get(q) or {}).get("done"))
            if i % 30 < K or i <= K:
                el = time.time() - t0
                print("[%d/%d] 本轮+%d 累计+%d 完成问题=%d 用时%.0fs"
                      % (min(i, len(queue)), len(queue), got, added_total, done_q, el), flush=True)
            time.sleep(random.uniform(0.8, 1.5))

    print("完成: 新增回答=%d 用时%.0fs" % (added_total, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""补作者资料：粉丝数 / 认证 / 简介（成员接口）。

背景：回答接口返回的 author 对象不含 follower_count（6610 条里只有 887 条带），
所以内容表的「粉丝数」覆盖率只有 22%。成员接口能补。

注意限流：连续跑 3000+ 请求后该接口会 403，所以放在回答补采之后跑，
并按批 sleep。断点续采：raw/api/members.json。
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
OUT = os.path.join(ROOT, "交付")
STORE = os.path.join(RAW, "members.json")

SRC_PROFILE = os.environ.get("ZHIHU_PROFILE") or ""
TMP_PROFILE = os.path.join(tempfile.gettempdir(), "zhprof_members")

K = int(os.environ.get("K", "6"))          # 每轮并发查几个用户
LIMIT = int(os.environ.get("LIMIT", "100000"))

INC = "follower_count,voteup_count,answer_count,articles_count,headline,badge,gender"


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


def jload(p, d=None):
    if not os.path.exists(p):
        return d
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def main():
    import csv
    rows = list(csv.DictReader(open(os.path.join(OUT, "01_内容总表.csv"),
                                    encoding="utf-8-sig")))
    # 需要补的用户：有主页链接且粉丝数为空
    need = {}
    for r in rows:
        url = (r.get("发布者主页") or "").strip()
        if not url or "/people/" not in url:
            continue
        tok = url.rstrip("/").split("/people/")[-1].split("?")[0]
        if not tok:
            continue
        if (r.get("粉丝数") or "").strip():
            continue
        need[tok] = (r.get("发布者") or "")
    store = jload(STORE, {})
    queue = [t for t in need if t not in store][:LIMIT]
    print("需补作者: %d | 已完成: %d | 本轮: %d" % (len(need), len(store), len(queue)), flush=True)
    if not queue:
        print("没有需要补的作者")
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

    ok = 0
    blocked = 0
    t0 = time.time()
    with StealthySession(headless=True, network_idle=False, timeout=120000,
                         max_pages=1, user_data_dir=TMP_PROFILE) as s:
        for i in range(0, len(queue), K):
            part = queue[i:i + K]
            box = {}

            def action(page, _part=part, _box=box):
                try:
                    page.wait_for_timeout(500)
                    urls = [f"https://www.zhihu.com/api/v4/members/{t}?include={INC}"
                            for t in _part]
                    raw = page.evaluate("""async (urls) => {
                      const rs = await Promise.all(urls.map(async u => {
                        try { const r = await fetch(u,{credentials:'include'});
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
            for tok, r in zip(part, rs):
                if not isinstance(r, dict) or r.get("status") != 200:
                    if isinstance(r, dict) and r.get("status") == 403:
                        blocked += 1
                    continue
                try:
                    d = json.loads(r.get("body") or "{}")
                except Exception:
                    continue
                if d.get("follower_count") is None and not d.get("name"):
                    continue
                store[tok] = {
                    "name": d.get("name") or need.get(tok, ""),
                    "follower_count": d.get("follower_count"),
                    "voteup_count": d.get("voteup_count"),
                    "answer_count": d.get("answer_count"),
                    "articles_count": d.get("articles_count"),
                    "headline": d.get("headline") or "",
                    "badge": json.dumps(d.get("badge") or {}, ensure_ascii=False)[:400],
                }
                ok += 1
            jdump(STORE, store)

            done = min(i + K, len(queue))
            if done % 60 < K or done <= K:
                print("[%d/%d] 成功%d 被拦%d 用时%.0fs"
                      % (done, len(queue), ok, blocked, time.time() - t0), flush=True)
            # 被拦就早点停，别把限流拖长
            if blocked >= 20 and ok == 0:
                print("连续被拦，提前结束本轮", flush=True)
                break
            time.sleep(random.uniform(0.6, 1.2))

    print("完成: 成功 %d / %d, 用时 %.0fs" % (ok, len(queue), time.time() - t0), flush=True)


if __name__ == "__main__":
    main()

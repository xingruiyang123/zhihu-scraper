# -*- coding: utf-8 -*-
"""Can Scrapling reuse a logged-in Chrome profile to unlock the API?

If yes: every answer + every comment becomes reachable (no more 3-per-question).
Test profile: MediaCrawler's dedicated zhihu profile (logged in 2026-09-29).
"""
import json
import os
import shutil
import sys
import tempfile

from scrapling.fetchers import StealthySession

SRC = os.environ.get("ZHIHU_PROFILE") or ""   # 指向你已登录知乎的 Chrome profile 目录副本
TMP = os.path.join(tempfile.gettempdir(), "zhprof_test")

QID = "31663423"
API = f"https://www.zhihu.com/api/v4/questions/{QID}/answers?limit=5&offset=0&include=data%5B*%5D.content"

# copy the profile so the original is never locked or modified
if os.path.exists(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
print("copying profile...", flush=True)
shutil.copytree(SRC, TMP, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("Cache", "Code Cache", "GPUCache",
                                              "ShaderCache", "GrShaderCache",
                                              "Service Worker", "DawnCache"))
print("copied to", TMP, flush=True)

box = {}


def page_action(page):
    try:
        page.wait_for_timeout(2000)
        res = page.evaluate("""async (u) => {
          try {
            const r = await fetch(u, {credentials: 'include'});
            const t = await r.text();
            return JSON.stringify({status: r.status, body: t.slice(0, 2000)});
          } catch (e) { return JSON.stringify({error: String(e)}); }
        }""", API)
        box["api"] = json.loads(res)
        # who am I?
        me = page.evaluate("""async () => {
          try {
            const r = await fetch('https://www.zhihu.com/api/v4/me', {credentials:'include'});
            return JSON.stringify({status: r.status, body: (await r.text()).slice(0,300)});
          } catch (e) { return JSON.stringify({error: String(e)}); }
        }""")
        box["me"] = json.loads(me)
    except Exception as e:
        box["error"] = f"{type(e).__name__}: {e}"[:250]


try:
    with StealthySession(headless=True, network_idle=True, timeout=120000,
                         max_pages=1, user_data_dir=TMP) as s:
        s.fetch(f"https://www.zhihu.com/question/{QID}", page_action=page_action, wait=1500)
except Exception as e:
    print("SESSION ERROR:", type(e).__name__, str(e)[:400])

print("\n=== /api/v4/me ===")
print(json.dumps(box.get("me"), ensure_ascii=False)[:600])
print("\n=== answers API ===")
print(json.dumps(box.get("api"), ensure_ascii=False)[:900])
if box.get("error"):
    print("\nERROR:", box["error"])

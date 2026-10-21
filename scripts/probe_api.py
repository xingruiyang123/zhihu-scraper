# -*- coding: utf-8 -*-
"""探针：知乎接口现在能不能用（登录态 + 已采 profile）。

判据（实测 2026-10-21）：
  /api/v4/me           200 且带 name  -> 登录态有效
  /api/v4/questions/{}/answers 200   -> 回答接口可用（这是续采必需的那个）

输出一行 READY / BLOCKED / NO_LOGIN，方便定时任务直接判读。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.environ.get("ZHIHU_ROOT") or os.getcwd()
SRC_PROFILE = os.environ.get("ZHIHU_PROFILE") or ""
TMP_PROFILE = os.path.join(tempfile.gettempdir(), "zhprof_probe")
QID = "31663423"


def main():
    # 先检查是否配置了登录 profile，否则直接给出可操作的提示
    if not SRC_PROFILE:
        print("NO_PROFILE  未设置 ZHIHU_PROFILE")
        print("            请把已登录知乎的 Chrome profile 目录指给它，例如：")
        print(r'            set ZHIHU_PROFILE=%LOCALAPPDATA%\Google\Chrome\User Data\Default')
        print("            注意：要先复制一份再用，别直接指向正在运行的浏览器目录")
        return 2
    if not os.path.isdir(SRC_PROFILE):
        print("NO_PROFILE  目录不存在：%s" % SRC_PROFILE)
        return 2

    if os.path.exists(TMP_PROFILE):
        shutil.rmtree(TMP_PROFILE, ignore_errors=True)
    try:
        shutil.copytree(SRC_PROFILE, TMP_PROFILE, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(
                            "Cache", "Code Cache", "GPUCache", "ShaderCache",
                            "GrShaderCache", "Service Worker", "DawnCache",
                            "SingletonLock", "SingletonCookie", "SingletonSocket",
                            "lockfile", "*.lock"))
    except Exception as e:
        print("BLOCKED copy_profile_failed: %s" % str(e)[:100])
        return 1

    from scrapling.fetchers import StealthySession

    box = {}

    def action(page):
        try:
            page.wait_for_timeout(1200)
            urls = {
                "me": "https://www.zhihu.com/api/v4/me",
                "answers": (f"https://www.zhihu.com/api/v4/questions/{QID}/answers"
                            "?limit=1&offset=0&include=data%5B*%5D.content"),
            }
            out = {}
            for k, u in urls.items():
                raw = page.evaluate("""async (u) => {
                  try { const r = await fetch(u,{credentials:'include'});
                        return JSON.stringify({status:r.status, body:(await r.text()).slice(0,300)}); }
                  catch(e){ return JSON.stringify({error:String(e)}); }
                }""", u)
                out[k] = json.loads(raw) if raw else {}
            box.update(out)
        except Exception as e:
            box["err"] = str(e)[:150]

    try:
        with StealthySession(headless=True, network_idle=False, timeout=90000,
                             max_pages=1, user_data_dir=TMP_PROFILE) as s:
            s.fetch("https://www.zhihu.com/", page_action=action, wait=800)
    except Exception as e:
        print("BLOCKED session_error: %s" % str(e)[:120])
        return 1

    me = box.get("me") or {}
    ans = box.get("answers") or {}
    me_ok = me.get("status") == 200 and '"name"' in (me.get("body") or "")
    ans_ok = ans.get("status") == 200 and (ans.get("body") or "").strip().startswith('{"data"')

    print("me=%s answers=%s" % (me.get("status"), ans.get("status")))
    if not me_ok:
        print("NO_LOGIN  登录态无效（profile 可能过期或被顶）")
        return 2
    if not ans_ok:
        body = (ans.get("body") or "")[:160].replace("\n", " ")
        print("BLOCKED   接口被拦：%s" % body)
        return 1
    print("READY     接口可用，可以续采")
    return 0


if __name__ == "__main__":
    sys.exit(main())

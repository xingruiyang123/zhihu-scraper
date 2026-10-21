# -*- coding: utf-8 -*-
"""Supervisor: keeps the Zhihu crawler alive across network drops / reboots.

Loop:
  1. probe network (a cheap HEAD to zhihu; fail = offline)
  2. if offline -> log and sleep 60s, retry (no data lost, no progress lost)
  3. if online  -> run one crawling pass (LOT=20 questions)
  4. pass finished -> repeat until every target is done

State lives entirely in raw/scrapling/questions.json, so this can be killed and
restarted at any moment. Nothing is ever re-done from scratch.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.environ.get("ZHIHU_ROOT") or os.getcwd()
PY = sys.executable
CRAWLER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crawler.py")
STORE = os.path.join(ROOT, "raw", "scrapling", "questions.json")
TARGETS = os.path.join(ROOT, "data", "crawl_targets_filtered.json")
LOG = os.path.join(ROOT, "logs", "supervisor.log")

LOT = int(os.environ.get("LOT", "20"))


def log(msg):
    line = "[%s] %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def online(timeout=12):
    """True if zhihu is reachable. Uses the same host the crawler needs."""
    for url in ("https://www.zhihu.com/robots.txt", "https://www.baidu.com"):
        try:
            req = urllib.request.Request(url, method="HEAD",
                                         headers={"User-Agent": "Mozilla/5.0"})
            urllib.request.urlopen(req, timeout=timeout)
            return True
        except urllib.error.HTTPError:
            return True          # got an HTTP response -> network is fine
        except Exception:
            continue
    return False


def progress():
    try:
        store = json.load(open(STORE, encoding="utf-8"))
        targets = json.load(open(TARGETS, encoding="utf-8"))
        return len(store), len(targets)
    except Exception:
        return 0, 0


def main():
    log("supervisor start (lot=%d)" % LOT)
    offline_rounds = 0
    while True:
        done, total = progress()
        if total and done >= total:
            log("ALL DONE: %d/%d targets" % (done, total))
            break

        if not online():
            offline_rounds += 1
            log("network DOWN (round %d) - sleeping 60s, progress kept %d/%d"
                % (offline_rounds, done, total))
            time.sleep(60)
            continue
        offline_rounds = 0

        log("network OK - starting pass (%d/%d done)" % (done, total))
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["LIMIT"] = str(LOT)
        try:
            r = subprocess.run([PY, CRAWLER], env=env, cwd=ROOT,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=7200)
            tail = (r.stdout or "").strip().split("\n")[-3:]
            for t in tail:
                log("  " + t[:200])
        except subprocess.TimeoutExpired:
            log("pass timed out - restarting")
        except Exception as e:
            log("pass crashed: %s" % str(e)[:200])

        time.sleep(10)


if __name__ == "__main__":
    main()

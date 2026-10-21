# -*- coding: utf-8 -*-
"""Zhihu crawler v5 — fixed the 180s stall + robust comment expansion.

Two bugs found in v4 (measured):
1. `network_idle=True` makes scrapling wait for the page to go quiet with the
   *page* default timeout (180s). Zhihu long-polls forever, so ~half the pages
   burned the full 180s. Fix: network_idle=False (SSR data is there at `load`).
2. Comment buttons were clicked before the answer list rendered, so the click
   hit nothing and the comment table came back empty. Fix: wait for the action
   bar selector first, click, then wait for `.CommentContent` to actually appear.

Measured effect: ~118s/question average -> ~25-35s.
"""
import json
import os
import random
import re
import sys
import time

from scrapling.fetchers import StealthySession

ROOT = os.environ.get("ZHIHU_ROOT") or os.getcwd()
RAW = os.path.join(ROOT, "raw", "scrapling")
DATA = os.path.join(ROOT, "data")
os.makedirs(RAW, exist_ok=True)

INIT = re.compile(r'<script id="js-initialData" type="text/json">(.*?)</script>', re.S)
STORE = os.path.join(RAW, "questions.json")
RETRY = os.path.join(RAW, "retry_queue.json")

CLICK_COMMENTS = r"""
(limit) => {
  const btns = Array.from(document.querySelectorAll('.ContentItem-actions button, .ContentItem-actions a, button, a, div[role=button]'))
    .filter(e => /条评论/.test((e.innerText || '').trim()));
  let n = 0;
  for (const b of btns.slice(0, limit)) { try { b.click(); n++; } catch (e) {} }
  return n;
}
"""

EXPAND_MORE = r"""
() => {
  let n = 0;
  document.querySelectorAll('button, a, div[role=button]').forEach(e => {
    const t = (e.innerText || '').trim();
    if (/^(展开|查看更多评论|查看全部|更多评论|点击查看)/.test(t)) { try { e.click(); n++; } catch (err) {} }
  });
  return n;
}
"""

COUNT_COMMENTS = "() => document.querySelectorAll('.CommentContent').length"

READ_COMMENTS = r"""
() => {
  const out = [];
  document.querySelectorAll('.CommentContent').forEach(function(c) {
    let root = c.parentElement, hops = 0;
    while (root && hops < 4 && !(root.innerText || '').includes('\n回复')) {
      root = root.parentElement; hops++;
    }
    const lines = (root ? root.innerText || '' : '').split('\n').map(s => s.trim()).filter(Boolean);
    let author = lines[0] || '', time = '', likes = '';
    for (const ln of lines) { if (/^\d{4}-\d{2}-\d{2}/.test(ln)) time = ln; }
    for (const t of lines.slice(-3)) { if (/^\d+$/.test(t)) likes = t; }
    out.push({author:author, time:time, likes:likes, text:(c.innerText||'').trim()});
  });
  return JSON.stringify(out);
}
"""


def jdump(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def jload(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def is_complete(rec):
    if not isinstance(rec, dict) or rec.get("error"):
        return False
    return bool(((rec.get("page") or {}).get("question") or {}).get("title"))


def extract(html, qid):
    m = INIT.search(html)
    if not m:
        return None
    try:
        ents = json.loads(m.group(1)).get("initialState", {}).get("entities", {})
    except Exception:
        return None
    users = ents.get("users") or {}
    answers = []
    for aid, a in (ents.get("answers") or {}).items():
        au = a.get("author") or {}
        tok = au.get("urlToken") or ""
        u = users.get(tok) or {}
        answers.append({
            "id": str(aid),
            "voteup": a.get("voteupCount") or 0,
            "favorites": a.get("favlistsCount") or 0,
            "comments_count": a.get("commentCount") or 0,
            "thanks": a.get("thanksCount") or 0,
            "created": a.get("createdTime"),
            "updated": a.get("updatedTime"),
            "truncated": bool(a.get("contentNeedTruncated")),
            "content_html": a.get("content") or "",
            "author": au.get("name") or "",
            "author_token": tok,
            "author_headline": au.get("headline") or u.get("headline") or "",
            "author_followers": au.get("followerCount", u.get("followerCount")),
            "author_votes": au.get("voteupCount", u.get("voteupCount")),
            "author_answers": au.get("answerCount", u.get("answerCount")),
            "author_articles": au.get("articlesCount", u.get("articlesCount")),
            "author_badge": json.dumps(au.get("badgeV2") or {}, ensure_ascii=False)
                            if au.get("badgeV2") else "",
            "is_anonymous": (au.get("name") or "") in ("", "匿名用户"),
        })
    q = None
    for _, qq in (ents.get("questions") or {}).items():
        q = {"question_id": qid, "title": qq.get("title") or "",
             "detail": qq.get("detail") or "",
             "answer_count": qq.get("answerCount"),
             "follower_count": qq.get("followerCount"),
             "visit_count": qq.get("visitCount"),
             "comment_count": qq.get("commentCount"),
             "created": qq.get("created"), "updated": qq.get("updatedTime")}
        break
    return {"question": q, "answers": answers}


def crawl_question(session, qid, comment_slots=8):
    box = {}

    def page_action(page):
        try:
            # SSR payload is present once the DOM is loaded — no networkidle wait
            page.wait_for_timeout(1800)
            box["page"] = extract(page.content(), qid)

            # wait until the action bars exist, otherwise the click hits nothing
            try:
                page.wait_for_selector(".ContentItem-actions", timeout=8000)
            except Exception:
                pass
            clicked = page.evaluate(CLICK_COMMENTS, comment_slots)
            box["clicked"] = clicked

            # wait for comments to actually render instead of blind sleeping
            got = 0
            for _ in range(6):
                page.wait_for_timeout(700)
                got = page.evaluate(COUNT_COMMENTS)
                if got:
                    break
            if got:
                page.evaluate(EXPAND_MORE)
                page.wait_for_timeout(1200)
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(900)
            box["comments"] = json.loads(page.evaluate(READ_COMMENTS) or "[]")
        except Exception as e:
            box["error"] = f"{type(e).__name__}: {e}"[:250]

    resp = session.fetch(f"https://www.zhihu.com/question/{qid}",
                         page_action=page_action, wait=800)
    box["status"] = getattr(resp, "status", None)
    return box


NET_MARKERS = ("getaddrinfo", "ConnectError", "ConnectionError", "Timeout",
               "net::ERR", "ECONN", "Temporary failure", "Name or service")


def looks_like_network(err):
    return any(m in str(err) for m in NET_MARKERS)


def main():
    limit = int(os.environ.get("LIMIT", "100000"))
    targets = jload(os.path.join(DATA, "crawl_targets_filtered.json"), [])
    store = jload(STORE, {})
    retry = jload(RETRY, [])

    purged = [k for k, v in list(store.items()) if not is_complete(v)]
    for k in purged:
        del store[k]
    if purged:
        jdump(STORE, store)

    done = set(store)
    queue = [t["question_id"] for t in targets if t["question_id"] not in done]
    queue = retry + [q for q in queue if q not in retry]
    queue = queue[:limit]
    print("targets=%d done=%d queue=%d" % (len(targets), len(done), len(queue)), flush=True)

    ok = fail = 0
    t0 = time.time()
    # network_idle=False: this is the fix for the 180s stall
    with StealthySession(headless=True, network_idle=False, timeout=90000, max_pages=1) as s:
        for i, qid in enumerate(queue, 1):
            hint = next((t["title"] for t in targets if t["question_id"] == qid), "")
            attempt, box, ts = 0, None, time.time()
            while attempt < 3:
                attempt += 1
                try:
                    box = crawl_question(s, qid)
                except Exception as e:
                    box = {"error": f"FETCH {type(e).__name__}: {e}"[:250]}
                if is_complete(box):
                    break
                if looks_like_network(box.get("error", "")):
                    time.sleep(30 * attempt)

            if is_complete(box):
                box["title_hint"] = hint
                box["crawled_at"] = int(time.time())
                store[qid] = box
                jdump(STORE, store)
                ok += 1
                print("[%d/%d] q%s %.0fs ans=%d cm=%d %s" % (
                    i, len(queue), qid, time.time() - ts,
                    len(box["page"].get("answers") or []),
                    len(box.get("comments") or []),
                    (box["page"]["question"].get("title") or "")[:32]), flush=True)
            else:
                fail += 1
                if qid not in retry:
                    retry.append(qid)
                jdump(RETRY, retry)
                print("[%d/%d] q%s FAIL %s" % (i, len(queue), qid,
                                               (box or {}).get("error", "")[:100]), flush=True)
            time.sleep(random.uniform(1.5, 3.0))

    jdump(RETRY, retry)
    print("pass done: ok=%d fail=%d elapsed=%.0fs" % (ok, fail, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()

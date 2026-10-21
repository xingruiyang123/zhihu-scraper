# -*- coding: utf-8 -*-
"""以战养战 v2：从语料里挖「本身不含校名、但紧贴校名出现」的实体。

思路：真正有用的别名往往出现在固定模式里：
    「XX大学的XX」      -> 抓后面那个词
    「XX（公大）」       -> 抓括号里的
    「我叫XX，公大XX级」 -> 抓人名/年级
    「XX队长/XX老师」    -> 抓人名

不靠通用分词，直接按锚点做模式抽取，噪声低得多。
"""
import csv
import json
import os
import re
from collections import Counter

ROOT = os.environ.get("ZHIHU_ROOT") or os.getcwd()
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "交付")

ANCHOR = r"(?:中国人民公安大学|公安大学|公大|公安大)"

# 抽取模式：(名称, 正则, 说明)
PATTERNS = [
    ("校区/地址", ANCHOR + r"(?:的)?([\u4e00-\u9fa5]{2,6}(?:校区|区|路|街|门))", "校区与地址"),
    ("院系", ANCHOR + r"(?:的)?([\u4e00-\u9fa5]{2,8}(?:学院|系|大队|中队|区队))", "院系/建制"),
    ("人物", r"([\u4e00-\u9fa5]{2,4})(?:老师|教授|队长|校长|教师|师兄|师姐)(?:说|讲|教|在|是)", "教师/管理者"),
    ("括号注", r"[\u4e00-\u9fa5]{2,6}[（(](" + ANCHOR + r")[)）]", "简称与全称对照"),
    ("别称", r"[\u4e00-\u9fa5]{2,6}(?=，?\s*(?:就是|即|指的是)" + ANCHOR + ")", "别称定义"),
    ("行业黑话", r"(警界|公安系统|体制内|入警|联考|警务化|预备警官|警号|警衔|授衔|见习警员)", "行业用语"),
    ("地标", r"(木樨地|团河|大兴|西城区|南里|公安大北门|公大南门)", "地理坐标"),
    ("事件", r"([\u4e00-\u9fa5]{2,8}(?:事件|风波|案|通报))", "标志性事件"),
]


def load_texts():
    parts = []
    p = os.path.join(OUT, "01_内容总表.jsonl")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                parts.append((r.get("标题/所属问题") or "") + "。" + (r.get("正文") or ""))
    cp = os.path.join(ROOT, "raw", "scrapling", "comments_v2.json")
    if os.path.exists(cp):
        d = json.load(open(cp, encoding="utf-8"))
        for v in d.values():
            for c in (v.get("comments") or []):
                parts.append(c.get("text") or "")
    return parts


def main():
    texts = load_texts()
    blob = "\n".join(texts)
    print("语料: %d 条 / %.1f 万字" % (len(texts), len(blob) / 10000))

    found = {}
    for kind, pat, note in PATTERNS:
        c = Counter((m.group(1) if m.groups() else m.group(0)) for m in re.finditer(pat, blob))
        for word, n in c.items():
            word = word.strip()
            if len(word) < 2 or n < 2:
                continue
            if re.fullmatch(r"[\d\.%]+", word):
                continue
            key = (kind, word)
            found[key] = {"类型": kind, "候选词": word, "出现次数": n, "说明": note}

    rows = sorted(found.values(), key=lambda x: (x["类型"], -x["出现次数"]))
    path = os.path.join(DATA, "keyword_aliases.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["类型", "候选词", "出现次数", "说明"])
        w.writeheader()
        w.writerows(rows)

    print("\n候选别名:", len(rows), "-> ", path)
    bytype = {}
    for r in rows:
        bytype.setdefault(r["类型"], []).append(r)
    for kind, lst in bytype.items():
        print("\n=== %s ===" % kind)
        for r in lst[:16]:
            print("  %-10s %d 次" % (r["候选词"], r["出现次数"]))


if __name__ == "__main__":
    main()

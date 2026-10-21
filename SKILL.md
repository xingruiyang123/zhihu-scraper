---
name: zhihu-scraper
description: 抓取知乎内容的可用通路与方法。当需要采集知乎的回答、文章、评论、问题数据（关注数/浏览量/回答数）、用户资料，或遇到知乎 403、「网络环境异常」unhuman 验证、接口被拦、搜索结果为空时使用。核心是：知乎页面是服务端渲染的，数据在 <script id="js-initialData"> 里，走 Scrapling StealthyFetcher 取页面即可，不要死磕 /api/v4 接口。
---

# 知乎采集

## 一句话结论

**知乎的数据在页面 HTML 里，不在接口里。**
页面（问题页/文章页/话题页/用户页）能 200 返回，`/api/v4` 接口却可能全部 403。
所以正确顺序是：拿页面 HTML → 解析 `js-initialData` → 需要评论时点开评论从 DOM 读。
**不要**一上来就调 `/api/v4`，也不要因为接口 403 就判定"知乎采不了"。

## 通道优先级（实测，2026-10-21 更新）

| 顺序 | 通道 | 实测结果 | 何时用 |
|---|---|---|---|
| 1 | **页面 + `js-initialData`** | ✅ 200，无需登录 | 默认首选，稳定 |
| 2 | **已登录 profile + `/api/v4`** | ✅ 200，字段最全、可翻页 | **能拿到全部回答，不只 3 条** |
| 3 | opencli `zhihu` 适配器 | ✅ 未风控时可用 | 命令行快速取搜索/问题/评论 |
| 4 | 直连 curl_cffi 指纹伪装 | ❌ 403 | 基本不可用 |
| 5 | Playwright MCP | ❌ 403 unhuman | 不可用 |

**两条通道的分工（这是本 skill 最重要的一条）**：

| | 页面 SSR | 登录态接口 |
|---|---|---|
| 无需登录 | ✅ | ❌ |
| 每个问题能拿几条回答 | **只有 3 条** | **全部，可翻页** |
| 收藏数 | 部分 | ✅ 有 |
| 评论数 | 部分 | ✅ 有 |
| 感谢数 | ✅ 有 | ✅ 有 |
| 单条浏览量 | ❌ | ❌ 接口也不给 |
| 搜索 | ❌ 需登录 | ✅ 有 |

**正确做法：用页面 SSR 拿问题级数据和前 3 条回答兜底，用登录态接口补全回答列表和指标。**
只用页面会漏掉 90% 的回答；只用接口在没有登录态时会被 401/403 挡死。

### 接口的三个关键事实（2026-10-21 实测）

**1. `include` 参数只有第一个字段能带 `data[*].` 前缀**

```
✅ data[*].content,favlists_count,comment_count,thanks_count,voteup_count
❌ data[*].content,data[*].favlists_count,data[*].comment_count
```

写成后者时，`favlists_count`、`comment_count`、`thanks_count` **会被静默丢弃**——
返回 200、字段缺失、不报错。这是最坑的一个点，会让人以为"知乎不提供收藏数"。

**2. 回答列表可以翻页拿全，不受 SSR 的 3 条限制**

```
/api/v4/questions/{qid}/answers?limit=20&offset=0&sort_by=default&include=...
```

offset 0/20/40… 每页 20 条，直到 `paging.is_end`。
实测一个 144 回答的问题能稳定翻到第 60 条以上。

**3. 登录态要复用已登录 Chrome profile**

```python
StealthySession(headless=True, network_idle=False, max_pages=1,
                user_data_dir=<profile 副本路径>)
```

必须先**复制** profile 再指过去（原目录会被锁）。
`MediaCrawler` 的 `browser_data/cdp_zhihu_user_data_dir` 就是个现成的已登录 profile。
复制时排除 `Cache/SingletonLock/lockfile` 等。

**接口的限流特征**：连续跑 ~3000 次请求后，`/api/v4/members/*` 会开始返回 403
（回答列表接口仍可用）。**成员接口要放在最前面跑，或单独限速。**

## 判断通道是否可用

取任意一个知乎问题页，看 HTML 里有没有
`<script id="js-initialData"`，以及有没有「网络环境异常」「unhuman」。
有 initialData、无 unhuman = 页面通道可用。

接口通道另测一枪：`/api/v4/me` 返回 200 且带 name = 登录态有效。

## 第 0 步：关键词（最容易做漏的一步）

**别只搜「单位全称 + 话题后缀」。** 这是我最开始的做法（20 个词全是一个模式），
结果漏掉了量级更大的入口。实测该学校相关语料里的真实频次：

| 叫法 | 语料中出现 |
|---|---|
| 公安大 | **7,694 次** |
| 公安大学 | 7,483 次 |
| 公大 | **4,750 次** |
| 中国人民公安大学 | 4,331 次 |

「公安大」比「公大」还高频，而这两个最初都**没有**作为独立搜索词。

### 关键词要覆盖这几类

| 类别 | 例子 | 为什么 |
|---|---|---|
| 全称 / 简称 / 昵称 | 中国人民公安大学、公安大学、公安大、公大 | 各占一部分受众 |
| 圈内别称 | 警界清华、公安最高学府 | 指向性极强，帖子质量高 |
| 英文缩写 | CPPSU、PPSUC、copsu | 量少但精准（实测各 60 条，不是零） |
| 校区 / 地标 | 木樨地、团河 | **与单位名完全无关，却直指该单位** |
| 行业黑话 | 公安联考、入警、警务化管理、预备警官、警号、授衔 | 带出大量相关讨论 |
| 相关人物 | 教授、队长、管理者姓名（配单位名限定） | 会带出具体事件 |
| 对比词 | 「A vs B」「A 还是 985」 | 高价值选择类讨论 |

### 以战养战：从已抓语料反挖关键词

抓到 200 万字以后，能挖出第一批根本想不到的词。做法：

1. 用锚点（单位全称/简称）筛出相关句子
2. 在锚点**附近**抽实体，而不是通用分词——噪声低得多
3. 三类线索：高频专有名词、与锚点高共现的词、人名/地名/缩写

```python
ANCHOR = r"(?:中国人民公安大学|公安大学|公大|公安大)"
PATTERNS = [
    ("校区", ANCHOR + r"(?:的)?([\u4e00-\u9fa5]{2,6}(?:校区|区|路|街|门))"),
    ("院系", ANCHOR + r"(?:的)?([\u4e00-\u9fa5]{2,8}(?:学院|系|大队|中队|区队))"),
    ("人物", r"([\u4e00-\u9fa5]{2,4})(?:老师|教授|队长|校长)(?:说|讲|教|在|是)"),
    ("行业词", r"(警界|入警|联考|警务化|预备警官|警号|警衔|授衔)"),
]
```

**共现强度** = 与锚点同段出现次数 ÷ 该词总频次。越高越可能是专属叫法。

## 第 0.5 步：相关性过滤（不做这一步，数据会被污染）

搜索结果会混进大量**无关热点**：只要有人在回答里提了一句关键词，那个问题就被搜出来。

实测踩过的污染样例：

- 「三个月没开学的大学宿舍现在什么样了？」（回答 3,766 条，只因有人提了警校）
- 「南师大 20 岁学生校内宿舍死亡」
- 「广东清远 16 岁女孩诬告父亲」
- 「研究生卖黄金被跨省冻结」

这些问题的浏览量动辄上千万，会把统计彻底带偏。

### 过滤规则（两级）

```python
SCHOOL_TERMS = ["公安大学","公大","公安大","CPPSU","公安院校","警校",
                "警察学院","公安联考","警务化","预备警官","公安专业"]

def is_relevant(title, body):
    # 一级：标题里点名
    if any(t in title for t in SCHOOL_TERMS):
        return True
    # 二级：正文里多处提及，或出现强指向词
    if len(body) > 120:
        n = sum(body.count(t) for t in ["公安大学","公大","警校","警察学院"])
        strong = any(t in body for t in ["警务化","公安联考","公大","公安大学"])
        return n >= 2 or strong
    return False
```

**必须把剔除清单落盘**（`dropped_titles.txt`），否则数据"悄悄变少"没法交代。

### 一条要提前跟需求方确认的边界

「讨论公安系统」和「讨论该单位」是两回事。实测被过滤掉的 198 个标题里：

- 真无关热点 16 个
- **讨论公安系统但没提校名 91 个**（如「狱警和公安怎么选」「公安调去纪委好吗」）

后者要不要收，取决于报告口径。**别自己替需求方决定**，单独出一张表或直接问。

## 标准流程

### 第 1 步：取页面 + 解析

```python
import json, re
from scrapling.fetchers import StealthySession

INIT = re.compile(r'<script id="js-initialData" type="text/json">(.*?)</script>', re.S)

def extract(html):
    m = INIT.search(html)
    if not m: return None
    return json.loads(m.group(1))

with StealthySession(headless=True, network_idle=True, timeout=180000, max_pages=1) as s:
    resp = s.fetch("https://www.zhihu.com/question/31663423", wait=2000)
    html = resp.html_content
    data = extract(html)
```

**为什么用 `StealthySession` 而不是 `StealthyFetcher.fetch`**：
Session 可以复用浏览器、连续抓多个页面，快很多。

### 第 2 步：读数据

```python
ents = data["initialState"]["entities"]
questions = ents.get("questions")   # 问题：标题/回答数/关注数/浏览量
answers   = ents.get("answers")     # 回答：赞同/收藏/评论数/感谢/正文/作者
articles  = ents.get("articles")    # 文章
users     = ents.get("users")       # 用户：粉丝数/简介/认证
```

字段名是 **camelCase**（和接口的 snake_case 不同）：

| 要什么 | 在页面数据里的字段 |
|---|---|
| 赞同数 | `voteupCount` |
| 收藏数 | `favlistsCount` |
| 评论数 | `commentCount` |
| 感谢数 | `thanksCount` |
| 正文 | `content`（HTML） |
| 匿名 | `author.name == "匿名用户"` 或 `is_anonymous` |
| 问题关注数 | `followerCount` |
| 问题浏览量 | `visitCount` |
| 问题回答总数 | `answerCount` |
| 用户粉丝数 | `author.followerCount` |
| 用户认证 | `author.badgeV2` |

### 第 3 步：评论要从 DOM 取

评论接口被拦，但**点击「N 条评论」会由站点自身 JS 带签名加载**，加载后就在 DOM 里。

**别用「向上找含『回复』的容器」这种文本特征定位**（我一开始就这么写，结果
没二级回复的评论容器里根本没有"回复"二字，循环一路爬到整条回答上，
取到的是回答作者）。改成按结构定位。

实测结构（2026-10-21，知乎现行版）：

```
<容器 class=css-1bklfe6>
  ├─ <a class=css-10u695f>          作者名
  ├─ <div class="CommentContent">   正文
  ├─ <span class=css-12cl38p>       日期（2024-12-12 或 06-02）
  ├─ <span>·</span>
  ├─ <span class=css-ntkn7q>        IP 属地（2022 年后才有）
  ├─ <span class=css-1kq4cxq>       热评标记（可选）
  ├─ 回复
  └─ 点赞数（纯文本，无 class，只能从容器文本末行取）
```

```python
def page_action(page):
    page.wait_for_timeout(2200)
    try:
        # 先等操作栏渲染，否则点击落空（踩过：clicked=0 / comments=0）
        page.wait_for_selector(".ContentItem-actions", timeout=8000)
    except Exception:
        pass
    page.evaluate("""() => {
      document.querySelectorAll('button, a, div[role=button]').forEach(e => {
        if (/条评论/.test((e.innerText || '').trim())) { try { e.click(); } catch (err) {} }
      });
    }""")
    # 轮询确认评论真的渲染，不要盲等固定秒数
    for _ in range(6):
        page.wait_for_timeout(700)
        if page.evaluate("() => document.querySelectorAll('.CommentContent').length"):
            break
    page.mouse.wheel(0, 2500)
    page.wait_for_timeout(1000)

    raw = page.evaluate(r"""() => {
      const out = [];
      document.querySelectorAll('.CommentContent').forEach(function(c) {
        // 容器 = 最近的、内部含 people 链接的祖先
        let item = c.parentElement, found = false;
        for (let d = 0; d < 6 && item; d++) {
          if (item.querySelector && item.querySelector('a[href*="/people/"]')) { found = true; break; }
          item = item.parentElement;
        }
        if (!found) item = c.parentElement;
        if (!item) return;
        let author = '';
        const a = item.querySelector('a[href*="/people/"]');
        if (a) author = (a.innerText || '').trim().split('\n')[0];
        let time = '', ip = '';
        const dn = item.querySelector('span[class*="css-12cl38p"]');
        if (dn) {
          time = (dn.innerText || '').trim();
          const sibs = Array.from(dn.parentElement.children);
          for (let i = sibs.indexOf(dn) + 1; i <= sibs.indexOf(dn) + 3 && i < sibs.length; i++) {
            const t = (sibs[i].innerText || '').trim();
            if (/^[\u4e00-\u9fa5]{2,}$/.test(t) && !/热评|回复|展开/.test(t)) { ip = t; break; }
          }
        }
        let likes = '';
        const lines = (item.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
        for (let i = lines.length - 1; i >= lines.length - 4 && i >= 0; i--) {
          if (/^\d+$/.test(lines[i])) { likes = lines[i]; break; }
        }
        out.push({author: author, time: time, ip_location: ip, likes: likes,
                  text: (c.innerText || '').trim()});
      });
      return JSON.stringify(out);
    }""")
```

**关键点**：

- `.CommentContent` 只有正文，作者/时间/点赞在**父容器**里
- 点赞数**没有 class**，只能从容器文本末行取
- 校验口径：作者/时间/点赞应接近 100%，IP 约 40%（见下条）

### 评论数据的三个陷阱

**1. 时间有两种格式，还有相对时间**

| 形态 | 例 | 处理 |
|---|---|---|
| 完整日期 | `2024-08-28` | 直接用 |
| 年月日省略 | `06-02` | **知乎对「当年」评论只显示月日**，补当年 |
| 相对时间 | `昨天 02:19`、`7 小时前` | 按抓取时间换算，标记为近似 |
| 空 | `` | 极少数，单独标记 |

只用 `\d{4}-\d{2}-\d{2}` 会让 **30% 的评论时间变空**（我第一次就是这么错的，
还误以为采到了）。必须同时处理短格式。

**2. IP 属地 2022 年才有**

知乎 2022 年下半年才上线 IP 属地显示。实测按年份：

| 年份 | 有 IP 占比 |
|---|---|
| 2014–2021 | **0%** |
| 2022 | 46% |
| 2023 | 79% |
| 2025 | 93% |
| 2026 | 96% |

整体约 39%，**这是平台功能时间线，不是采集失败**。

**3. 点赞为空 = 没人点赞**

知乎对 0 赞不显示数字。所以约 78% 的评论 `likes` 为空/0 是正常的。
不要当成提取 bug 去反复修。

## 一定要知道的限制

1. **每个问题页只 SSR 3 个回答**。滚动不会加载更多（翻页走被拦的 XHR）。
   要全量回答**只有登录态接口这条路**（见上面「接口的三个关键事实」），
   或依赖早期接口缓存。历史缓存极其宝贵，**不要丢**。
2. **`page_action` 的返回值会被丢弃**，必须在函数里自己写文件。
3. **`page_action` 可能被 SPA 导航打断**（`Execution context was destroyed`），
   要等 `wait_for_load_state("networkidle")` 后重试 2–4 次。
4. **搜索页在未验证 IP 上会显示"未搜索到相关内容"**，不是你的选择器错了。
   目标清单可以用问题 ID 直接构造，不依赖搜索。
5. **登录 ≠ 解锁**。带登录态（`/api/v4/me` 返回 200）时接口仍可能 403 ——
   拦的是 IP 风险分，不是账号。
6. **复用已登录 profile** 可以让 `/api/v4/me` 通过，但接口限制另算：
   `StealthySession(user_data_dir=<profile副本>, real_chrome=True)`。
   注意**先复制** profile 再用，别直接指向原目录（会被锁）。
7. **单条回答的浏览量拿不到**。`/api/v4/answers/{id}` 的 `visits_count` 返回 null，
   只有搜索结果对象里才带。这是知乎的设计，不是漏采——**要在交付说明里写清**。
8. **作者粉丝数也不在回答对象里**。回答接口的 `author` 只有 name/url_token/headline，
   要单独调 `/api/v4/members/{token}`。这个接口限流更严，**要放在最前面跑**。

## 字段缺失 ≠ 值为 0（这条会直接影响结论可信度）

不同来源能提供的指标不一样。如果统统填 0，「没采集」就被读成「真的是 0」。

实测各来源的真实能力：

| 来源 | 赞同 | 收藏 | 评论数 | 感谢 | 单条浏览 |
|---|---|---|---|---|---|
| 页面 SSR | ✅ | ✅ | ✅ | ✅ | ❌ |
| 搜索接口 | ✅ | ✅ | ✅ | ❌ | ✅ |
| 回答列表接口（旧 include） | ✅ | ❌ | ✅ | ❌ | ❌ |
| 回答列表接口（正确 include） | ✅ | ✅ | ✅ | ✅ | ❌ |

**正确做法**：按来源记 `_seen` 字段集合，导出时未采到的写空字符串而不是 0。

```python
SOURCE_FIELDS = {
    "搜索列表": {"voteup", "favorites", "visits", "comments_count"},
    "回答列表": {"voteup", "comments_count"},
    "问题页":   {"voteup", "favorites", "comments_count", "thanks"},
    "接口补采": {"voteup", "favorites", "comments_count", "thanks"},
}

def metric(field, value):
    return value if field in seen else ""      # 而不是 value or 0
```

交付时同时给出「覆盖率 + 缺失原因」，比一个漂亮的 100% 有用得多。

## Windows 上写文件会被 Excel 锁住

交付表常年被人在 Excel 里开着，脚本覆盖写会直接 `PermissionError` 崩掉，
而且往往崩在最后一步（前面几十分钟白跑）。

```python
def safe_write_csv(path, header, rows, enc="utf-8-sig"):
    for i in range(6):
        try:
            with open(path, "w", encoding=enc, newline="") as f:
                w = csv.writer(f)
                if header: w.writerow(header)
                w.writerows(rows)
            return path
        except PermissionError:
            time.sleep(0.5 * (i + 1))
    alt = path.replace(".csv", "_新.csv")
    ...写 alt 并提示用户...
```

同理，**用另一个进程读 JSON 也会锁住它**，导致采集器的 `os.replace` 失败。
原子写入要带重试，别因为一次读文件把几小时的采集搞崩。

## 性能：`network_idle=True` 会白等 180 秒（实测踩过）

**这是最贵的坑。** `StealthySession(network_idle=True)` 会让 scrapling 等页面
「网络彻底静默」，而它内部的实现是：

```python
page.wait_for_load_state("networkidle", timeout=None)  # 用页面默认超时
```

知乎页面有长轮询，**永远不会静默**，于是每次都要耗尽默认超时（180 秒）才继续。
表现是：一半页面耗时稳定在 206 秒左右（180 + 正常 25），另一半正常 ——
平均被拖到 118 秒/个，全量要 28 小时。

**修复**：`network_idle=False`。SSR 数据在 `load` 阶段就已经在 HTML 里了，
根本不需要等网络静默。

```python
with StealthySession(headless=True, network_idle=False,   # ← 关键
                     timeout=90000, max_pages=1) as s:
```

实测：**118 秒/个 → 17 秒/个**，全量从 28 小时降到 4.8 小时。
`network_idle=True` 并不会让数据更完整 —— 数据是 SSR 的，早就齐了。

## 评论点不开：点击时机错了

症状：`clicked=0` 或 `clicked=N` 但 `comments=0`。

原因：`page_action` 里 `wait_for_timeout(2200)` 之后就点「N 条评论」，
但**回答卡片可能还没渲染出来**，按钮不存在，点击落空。

正确顺序：

```python
page.wait_for_timeout(1800)                     # 先解析 SSR 数据
try:
    page.wait_for_selector(".ContentItem-actions", timeout=8000)   # 等操作栏出现
except Exception:
    pass
clicked = page.evaluate(CLICK_COMMENTS, 8)      # 现在点才有效

# 不要盲等固定秒数，轮询确认评论真的渲染了
got = 0
for _ in range(6):
    page.wait_for_timeout(700)
    got = page.evaluate("() => document.querySelectorAll('.CommentContent').length")
    if got:
        break
if got:
    page.evaluate(EXPAND_MORE)                   # 再点「展开/查看更多」
    page.wait_for_timeout(1200)
    page.mouse.wheel(0, 2500)
    page.wait_for_timeout(900)
comments = json.loads(page.evaluate(READ_COMMENTS))
```

## 限速（这是被风控的唯一原因）

早期事故：页内并发 7 个请求、1.6 秒一批，几百页几分钟跑完 → IP 被标记。

必须：
- **严格串行**，一次一页
- **页间随机 1.5–3 秒**（修掉 180 秒空等后，实测这个间隔稳定不触发风控）
- 不并发、不多标签
- 驱动加载用真实滚动/点击，不要狂发请求

> 注：限速的目的是**别并发**，不是「越慢越安全」。单页内点几次按钮、
> 滚动几屏都是正常用户行为；真正触发风控的是「1.6 秒发 7 个 XHR」这种爆发。

### 实测的限流阈值与恢复特征（2026-10-21）

| 累计请求量 | 状态 |
|---|---|
| 0 – 1000 | 全通 |
| ~3000 | 开始出现 403，**成员接口先挂**，回答列表接口还能用 |
| 3000+ | 全部 403，页面也被重定向到 unhuman |

**注意：页内并发（一个 `page_action` 里 Promise.all 6–8 个 URL）本身能显著提速，
不是禁手；关键是总请求量和批次间隔。** 实测 6 并发 + 1 秒间隔跑到 3000 次才挂。

被拦的特征长这样：

```json
{"error":{"code":40352,"need_login":false,
 "redirect":"https://www.zhihu.com/account/unhuman?type=Q8J2L3&need_login=false",
 "message":"系统监测到您的网络环境存在异常"}}
```

**恢复**：不是分钟级，是**小时级**（本次等了几小时仍未恢复）。
别反复重试——越试标记越久。

### 用探针判断是否恢复（给定时任务用）

```python
# 两个端点分开判断，结论才准
me      = fetch("https://www.zhihu.com/api/v4/me")                 # 200 + name = 登录有效
answers = fetch("https://www.zhihu.com/api/v4/questions/{qid}/answers?limit=1&offset=0&include=...")

# me=200 但 answers=403  → 账号正常，接口被限流，继续等
# me=401                  → 登录态失效，要重新登录取 profile
# 两个都 200              → 可以续采
```

**换网络能立刻解封**（实测：换手机热点后同一账号同一 profile 立即恢复）。
比等待快得多，值得优先试。

## 断点续爬（网络会断，必须做对）

**错误做法**：失败也写进缓存 → 该条永远不会再抓。
**正确做法**：

```python
def is_complete(rec):
    if not isinstance(rec, dict): return False
    if rec.get("error"): return False
    return bool((rec.get("page") or {}).get("question", {}).get("title"))

# 失败不写 store，进重试队列
if is_complete(box): store[qid] = box; save()
else: retry_queue.add(qid); save()
```

外加守护进程：探网 → 断网睡 60s 重试 → 联网跑一批 → 循环，直到全部完成。
进度全在 JSON 文件里，**随时 kill 随时重启，不丢不重**。

## 随包脚本（本 skill 自带，开箱可跑）

`scripts/` 下的脚本是完整可运行的实现，路径全部自适应，不需要改。

| 文件 | 用途 | 用法 |
|---|---|---|
| `scripts/crawler.py` | 页面采集器：断点续爬 + 已修 180s 空等 + 评论展开 | `python scripts/crawler.py` |
| `scripts/api_backfill.py` | **接口续采**：登录态下把每个问题的回答取全（突破 3 条限制） | `python scripts/api_backfill.py` |
| `scripts/supervisor.py` | 断网守护：断网等待、恢复继续、跑满为止 | `python scripts/supervisor.py` |
| `scripts/examples/profile_login.py` | 复用已登录 Chrome profile 的写法示例 | 按需参考 |

**先建目录再运行**（脚本会在当前目录下写 `raw/`）：

```bash
mkdir my-zhihu && cd my-zhihu
python <skill目录>/scripts/crawler.py      # 页面通道：问题级数据 + 前3条回答 + 评论
python <skill目录>/scripts/api_backfill.py # 接口通道：回答取全 + 收藏/评论/感谢
```

环境变量：
- `ZHIHU_ROOT` — 数据落盘根目录，默认当前目录
- `ZHIHU_PROFILE` — 已登录 Chrome profile 目录（接口通道必需）
- `LIMIT` / `LIMIT_Q` — 本次最多跑多少个问题（不设=不限）
- `MAX_PER_Q` — 每个问题最多取多少条回答（接口通道默认不限）

采集目标清单从 `data/crawl_targets_filtered.json` 读（数组，每项需含
`question_id`、`title`、`answer_count`）。没有这个文件就先自己写一份问题 ID 列表。

## 自检清单

**通道与配置**
- [ ] 页面 HTML 里有 `js-initialData`、没有 `unhuman`
- [ ] `StealthySession(network_idle=False)` —— **别开 network_idle，会白等 180 秒**
- [ ] 用 `StealthySession` 复用浏览器，不要一次 fetch 一个
- [ ] 接口通道：`/api/v4/me` 返回 200 且带 name（否则先解决登录态）
- [ ] `include` 只有第一个字段带 `data[*].` 前缀，其余用裸名

**关键词与相关性**
- [ ] 关键词覆盖：全称/简称/昵称/别称/英文缩写/校区地名/行业黑话/人物
- [ ] 抓够量后回头挖关键词（以战养战），别指望第一版列全
- [ ] 相关性过滤做了，且剔除清单落盘
- [ ] 跟需求方确认过「行业相关但没提主体名」的内容要不要

**数据正确性**
- [ ] 字段名用 camelCase（`voteupCount` 不是 `voteup_count`）
- [ ] 评论按结构定位（作者 `a[href*="/people/"]`、时间 `span.css-12cl38p`）
- [ ] 点评论前先 `wait_for_selector('.ContentItem-actions')`，别用固定毫秒盲等
- [ ] 评论时间同时处理 `YYYY-MM-DD`、`MM-DD`、`昨天/N小时前`
- [ ] 字段缺失写空，不要写 0（否则「没采集」被读成「真是 0」）
- [ ] 交付说明写明：单条浏览量知乎不提供、IP 属地 2022 年后才有

**工程健壮性**
- [ ] `page_action` 里自己写文件（返回值会被丢）
- [ ] 失败不写 store，进重试队列
- [ ] 原子写带重试（Excel 会锁 CSV，别的进程读 JSON 会锁 JSON）
- [ ] 页间 sleep 1.5–3s，别无节制爆发
- [ ] 先小批量（4–6 个）实测速率和字段完整度，再放全量
- [ ] **验收要抽实际数据看，不要只看统计数字**（我犯过：单页 100% 就当整体修好了）

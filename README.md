# zhihu-scraper

知乎内容采集 skill —— 给 AI agent 用，也能纯手工跑。

> 起因：一次真实任务里，知乎接口全被 403，换了四种通道才走通。
> 这份文档把踩过的坑和可跑的脚本都固化下来，下次不用重新摸索。

## 一分钟上手

```bash
pip install "scrapling[fetchers]==0.4.15"
scrapling install
python scripts/probe_api.py     # 先看通不通
```

然后按 [使用说明.md](使用说明.md) 跑。

## 这玩意解决什么问题

| 问题 | 这份 skill 的答案 |
|---|---|
| 知乎接口全 403，是不是采不了？ | 不是。**数据在页面 HTML 里**，不用接口也能拿 |
| 每个问题只能拿 3 个回答？ | 页面确实只有 3 条，**但登录态接口能翻页取全** |
| 收藏数怎么是空的？ | `include` 参数写法有个陷阱，只有第一个字段能带 `data[*].` 前缀 |
| 评论时间一半是空的？ | 知乎对当年评论只显示 `06-02`，正则要兼容短格式 |
| 抓完发现混进一堆无关热点？ | 要做相关性过滤，否则统计全被带偏 |
| 关键词想不全？ | 从已抓语料里反挖（以战养战），能挖出想不到的叫法 |

## 核心结论

**两条通道分工，缺一不可：**

| | 页面 SSR | 登录态接口 |
|---|---|---|
| 需要登录 | 否 | 是 |
| 每个问题能拿几条回答 | **只有 3 条** | **全部** |
| 收藏数 / 感谢数 | 部分 | 有 |
| 单条浏览量 | 否 | 否（知乎不提供） |

只用页面会漏 90% 的回答；只用接口在没有登录态时会被 401/403 挡死。

## 目录

```
SKILL.md              完整方法论（给 agent 读，也是查坑手册）
使用说明.md            给人读的上手文档
scripts/
  crawler.py          页面通道：问题级数据 + 前 3 条回答 + 评论
  api_backfill.py     接口通道：回答取全 + 收藏/评论/感谢
  api_backfill_full.py 接口续采增强版
  member_backfill.py  补作者粉丝数 / 认证
  probe_api.py        探针：READY / BLOCKED / NO_LOGIN / NO_PROFILE
  complete_data.py    一键补全（探针→取全→作者资料→重建表）
  mine_aliases.py     从语料里挖关键词别名
  supervisor.py       断网守护
  examples/           写法示例
```

## 怎么给 agent 用

把整个目录放进 agent 的 skills 目录，然后说「按这个 skill 抓知乎数据」。

## 注意

- 采集要有节制：串行、页间 1.5–3 秒。爆发式并发的唯一后果是被封几小时。
- 换网络能立刻解封（实测手机热点最有效）。
- 这是给内部研究用的，别拿去商用或大规模骚扰对方服务器。

## 队友怎么获取

```bash
git clone https://github.com/xingruiyang123/zhihu-scraper.git
```

放进 agent 的 skills 目录即可。后续更新只要 `git pull`。

## 维护者怎么更新

改完直接推：

```bash
cd <这个目录>
git add -A
git commit -m "update: 说明改了什么"
git push
```

需要同步到本机多个 agent 的 skills 目录时，再跑一次
`sync_skills.py --only zhihu-scraper`（如果本机有这套下发机制）。
# stalker2-mod-monitor — 潜行者2 新MOD监控

监控 NexusMods 上 S.T.A.L.K.E.R. 2 的新发布与近期更新，对比缓存后只报增量。**NexusMods 前面是 Cloudflare**（2026-10-22 实测无头 Chrome 只拿到挑战页、0 条，直连同样被挡），所以抓取要真浏览器——现在**脚本自己起**，不用人开页面。

触发说法：「检查潜行者2新MOD」「潜行者2监控有新MOD吗」。

## 跑一次（就两步）
```bat
py -3 D:\Alife\Storage\Skills\stalker2-mod-monitor\scripts\cdp_grab.py
py -3 D:\Alife\Storage\Skills\stalker2-mod-monitor\scripts\nexus_monitor.py --from-file D:\Alife\Storage\SkillData\nexus_grab.txt --update
```
`cdp_grab.py` 用独立 user-data-dir 起一个正常的（非无头）msedge/chrome，连 DevTools 调试口，导航后**边滚边轮询** `Runtime.evaluate`（Nexus 的列表是滚动加载的），抓到就写 `SkillData\nexus_grab.txt`、把自己起的浏览器关掉。

**Nexus 自己会抖，两条列表地址都要试**（2026-10-30 本机实测）：`/games/<game>/mods?sort=updatedAt` 十次有六七次只回页面壳子、列表根本不渲染（不是 CF——页内没有 challenges iframe、标题正常，等 56 秒也 0 条）；不带 `games/` 的那条会 302 回 `/games/<game>/mods` 并**把 `?sort=updatedAt` 丢掉**。所以脚本是「两条地址 × 各重载一次」，抓到就往下走；每条卡片自带绝对时间，排序被丢掉只影响覆盖面，不影响判新增/更新。修完之后连跑三次 20 / 20 / 20 条（之前是 1 中 2 空）。

参数：`--pages 2`（渲出列表后再多滚几轮「加载更多」）、`--timeout 120`、`--url`（只试指定那条）、`--out`、`--browser`、`--print`。比对端：`--hours 48`、`--max-age 60`（**默认拒绝 60 分钟前的旧抓取文件**，旧文件冒充新结果会一路假报「无新增/更新」；要用的话 `--max-age 0`）、`--selftest --print`。

## 看退出码，别看 stdout
| 脚本 | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| `cdp_grab.py` | 抓到并写盘 | — | 两条地址都试过仍一条没抽到（多半改版） | Cloudflare 挑战页没过（见兜底写法） | 浏览器起不来或 CDP 连不上 | 抓到但写盘失败 |
| `nexus_monitor.py` | 无变化，**只回一句「无新增/更新」，不去读文件** | 有变化，去读 `SkillData\nexus_report.txt` 汇报 | 抓到东西但解析不出条目 | 没抓到/读写失败/抓取文件太旧（`nexus_error.txt`） | — | — |

两个脚本全程零回显，`--print` 才给人看；**参数用错退 9（用法错误）**，不会和表里那些码混。

**报「无新增/更新」前先分清是哪一种**：每次跑都会把一行纯计数写进 `SkillData\nexus_status.txt`，例如
`10-30 12:59 [潜行者2] 退出码0 抓到19 没变化19 窗口外0 新增0 更新0 窗口24小时`。
`没变化` 是「与缓存一致、没写进报告的条数」，不是「缓存里已有几条」（已知但更新过的算进 `更新`）。`抓到0` 或退出码 2/3/4 = 抓取那一步出问题了。这行只有计数、没有条目名，读它不会把数据搬进对话。

## 兜底写法（`cdp_grab` 回 2/3 时）
真人开 `https://www.nexusmods.com/games/stalker2heartofchornobyl/mods?sort=updatedAt`，先起接收器（`start /min "" py -3 D:\Alife\Storage\Skills\stalker2-mod-monitor\scripts\sink.py`），再跑这段 JS：
```js
(() => {
  const rows = [], seen = new Set();
  document.querySelectorAll('a[href*="/stalker2heartofchornobyl/mods/"]').forEach(a => {
    const m = a.href.match(/\/mods\/(\d+)/);
    if (!m || seen.has(m[1])) return;
    const title = (a.innerText || '').trim();
    if (title.length < 2) return;
    seen.add(m[1]);
    let time = '', node = a;
    for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
      const t = node.querySelector && node.querySelector('relative-time[datetime], time[datetime]');
      if (t) { time = t.getAttribute('datetime') || ''; break; }
    }
    rows.push(m[1] + ' :: ' + title.slice(0, 60) + ' :: ' + time);
  });
  const f = document.createElement('form');
  f.method = 'POST'; f.action = 'http://127.0.0.1:38471/'; f.style.display = 'none';
  const ta = document.createElement('textarea'); ta.name = 'data'; ta.value = rows.join('\n');
  f.appendChild(ta); document.body.appendChild(f); f.submit();
})();
```
**接收器是一次性的、覆盖写**：滚第二页再提交会把第一页顶掉——要么每页各跑一次比对，要么交给 `cdp_grab.py --pages N`。（提交会导航走，JS 的返回值拿不到；行数看接收器回的那页或 grab 文件。）

## 汇报
按 `nexus_report.txt` 原样转述：抬头「最近N小时：新增 X 条、更新 Y 条（抓到…，窗口外…）」，下面编号列变化条目，每条 `序. 新|更 标题〔中文名〕 - 月/日 时:分 - 链接`。告警行（`[时间告警]`／`[回填]`／`[覆盖告警]`）只有计数，照抄即可。

**每条都要加中文名**：写在标题后面的全角`〔〕`里，只括中文名，原来那些字段（序号、新/更、时间、链接）一个字都不动。**原标题必须保留**——缓存按标题和 mod id 对，删了就对不上了；中文名是给人看的第二份标签。
- 有通行译名就用通行译名：`Inventory Dense Grid` → 〔背包密格〕
- 没有通行译名就直译；专有名词、型号、缩写保留原文：`S.T.A.L.K.E.R. 2 Optimizer` → 〔S.T.A.L.K.E.R. 2 优化器〕、`GTX 1050` 照抄不动
- 标题里的版本号（`(2.1)`）、尾部的降级/适配说明（`- Low-End PC Optimization`）属于原题的一部分，别并进中文名，但原标题里照抄
- 纯型号/缩写没有可译成分的（`ZM_Raygun`、`ACCESS_GRANTED`）就音译或留原文，别硬翻
- 拿不准就直译。**不要为了通顺编一个跟原名对不上的名字**——日报是给人找 MOD 用的，译名和原名对不上比没有译名更糟

## 链接铁律（2026-09-12 主人定稿）
每条必须给真实可点的完整链接，禁占位符：`https://www.nexusmods.com/stalker2heartofchornobyl/mods/{数字ID}`（**不带 `games/`**，带了 404）。

## 注意
- `[覆盖告警]` = 抓到的最旧一条还在窗口里：`--pages 3` 多滚几轮，或把 `--hours` 调小。
- 判更新只认绝对时间**严格晚于**缓存里那条（`mods[id].updated` 存页面原始绝对串）。旧缓存只有相对串的，第一次走 `[回填]` 而不是误报整页更新。
- `SkillData\browser-nexus\` 是脚本自己的浏览器 profile（与你日常用的浏览器分开）；固定 profile 会被上一轮没退干净的进程占住，所以脚本自带「换一次性 profile 再试一次」的兜底。超过一天的旧副本自动清，最多留 5 个。
- 检查间隔建议 ≥2 小时；只记录信息，不下载 MOD。

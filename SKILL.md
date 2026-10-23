# stalker2-mod-monitor — 潜行者2 新MOD监控

监控 NexusMods 上 S.T.A.L.K.E.R. 2 的新发布与近期更新，对比缓存后只报增量。**NexusMods 直连被 Cloudflare 挡**（2026-10-22 本机实测：无头 Chrome 拿排序页只得到挑战页、0 条），所以抓取这一步必须走真浏览器。

触发说法：「检查潜行者2新MOD」「潜行者2监控有新MOD吗」。

## 跑一次
```bat
:: 1) 先起接收器（后台跑，等浏览器把数据投进来）
start /min "" py -3 D:\Alife\Storage\Skills\stalker2-mod-monitor\scripts\sink.py
:: 2) 浏览器打开排序页，跑下面那段抓取JS（它抓完自己提交给 127.0.0.1:38471）
::    https://www.nexusmods.com/games/stalker2heartofchornobyl/mods?sort=updatedAt
:: 3) 接收器退出码 0 之后，比对并落盘
py -3 D:\Alife\Storage\Skills\stalker2-mod-monitor\scripts\nexus_monitor.py --from-file D:\Alife\Storage\SkillData\nexus_grab.txt --update
```
回退写法（接收器起不来时）：让 JS 把结果 `return` 回来，用文件工具写进 `SkillData\nexus_grab.txt`，再跑第 3 步——代价是数据会进一次对话。

## 看退出码，别看 stdout
| 脚本 | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| `sink.py` | 已收到并落盘 | — | 收到了但没一行有效数据 | — | 超时没收到（`nexus_error.txt`） |
| `nexus_monitor.py` | 无变化，**只回一句「无新增/更新」，不去读文件** | 有变化，去读 `SkillData\nexus_report.txt` 汇报 | 抓到东西但解析不出条目 | 没抓到东西或读写失败（`nexus_error.txt`） | — |

两个脚本全程零回显，`--print` 才给人看。其它参数：`--hours 48`（默认24）、`--selftest --print`（离线自检，不碰真缓存）。

**报「无新增/更新」前先分清是哪一种**：每次跑都会把一行纯计数写进 `SkillData\nexus_status.txt`，例如
`10-22 15:40 [潜行者2] 退出码0 抓到20 缓存已含20 窗口外0 新增0 更新0 窗口24小时`。
`抓到20 缓存已含20` = 抓到了、都是老熟人；`抓到0` 或退出码 2/3 = 抓取那一步出问题了。这行只有计数、没有条目名，读它不会把数据搬进对话。

## 抓取JS
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
  return rows.length;      // 只回行数，不回数据
})();
```
每行是 `数字ID :: 标题 :: 绝对时间`。**绝对时间优先**（`<relative-time datetime>`）：脚本只用它判更新，只拿到「2 hours ago」这种相对串时会按 ID 判新增、并在 report 里记一条 `[时间告警]`。上面这组 datetime 选择器已由主人在 2026-10-23 实跑证实能取到 `2026-10-22T22:14:59.000Z` 这种绝对串；**标题元素各站会变**——ModDB 那边实测标题在 `h4 a` 里（写成 `h3 a` 会整批空标题），Nexus 这边 `a.innerText` 是对的，实跑若出现 `[标题告警]` 就说明这个也要跟着改。

## 汇报
按 `nexus_report.txt` 原样转述：抬头是「最近N小时：新增 X 条、更新 Y 条（翻了/抓到…，窗口外…）」，下面编号列变化条目，每条 `序. 新|更 标题 - 月/日 时:分 - 链接`。告警行（`[时间告警]`／`[回填]`／`[覆盖告警]`）只有计数，照抄即可。

## 链接铁律（2026-09-12 主人定稿）
每条必须给真实可点的完整链接，禁占位符：`https://www.nexusmods.com/stalker2heartofchornobyl/mods/{数字ID}`（**不带 `games/`**，带了 404）。

## 注意
- `[覆盖告警]` = 抓到的最旧一条还在窗口里，多半有下一页没抓：在页面上点分页再抓一次（分页参数本轮未实测，不写死），或把 `--hours` 调小。
- 判更新只认绝对时间**严格晚于**缓存里那条；缓存存的就是页面原始绝对串（`mods[id].updated`）。旧缓存只有相对串的，第一次会走 `[回填]` 而不是误报整页更新。
- 检查间隔建议 ≥2 小时；只记录信息，不下载 MOD。

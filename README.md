# stalker2-mod-monitor（Alife 技能）

监控 NexusMods 上 S.T.A.L.K.E.R. 2 的新 MOD 发布与近期更新。用法、抓取 JS、退出码口径全在 **[SKILL.md](SKILL.md)**。

- 装法：解压到 `Alife\Storage\Skills\stalker2-mod-monitor\`（就是这个目录本体）
- 抓取由 `scripts/cdp_grab.py` 起非无头 msedge/chrome 走 CDP 完成（独立 user-data-dir、窗口在屏幕外）。列表页两条地址各重载一次：`/games/<game>/mods?sort=updatedAt` 有时只回页面壳子、列表不渲染，不带 `games/` 的那条会 302 并丢掉 sort 参数。`scripts/sink.py` 保留为兜底
- 三个脚本全程零回显：结果写 `Storage\SkillData\nexus_report.txt`，状态一行写 `nexus_status.txt`，失败写 `nexus_error.txt`，靠退出码带信号（monitor `0 无变化 / 1 有变化 / 2 解析不出 / 3 抓取读写失败或文件太旧`；cdp_grab `0 / 2 一条没抽到 / 3 CF 没过 / 4 浏览器或 CDP 起不来 / 5 写盘失败`；sink `0 / 2 / 4`）；**参数用错一律退 9**，不和这些码混
- 缓存与抓取产物都在 `Storage\SkillData\`，不在本仓库里
- 离线自检：`py -3 scripts/nexus_monitor.py --selftest --print`（不联网、不碰真缓存）

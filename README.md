# stalker2-mod-monitor（Alife 技能）

监控 NexusMods 上 S.T.A.L.K.E.R. 2 的新 MOD 发布与近期更新。用法、抓取 JS、退出码口径全在 **[SKILL.md](SKILL.md)**。

- 装法：解压到 `Alife\Storage\Skills\stalker2-mod-monitor\`（就是这个目录本体）
- NexusMods 直连被 Cloudflare 挡（无头 Chrome 也只拿到挑战页），所以抓取走真浏览器，`scripts/sink.py` 在本机收数据落盘，`scripts/nexus_monitor.py` 比对缓存
- 两个脚本全程零回显：结果写 `Storage\SkillData\nexus_report.txt`，状态一行写 `nexus_status.txt`，失败写 `nexus_error.txt`，靠退出码带信号（`0 无变化 / 1 有变化 / 2 解析不出 / 3 抓取读写失败`；sink 是 `0 / 2 / 4`）
- 缓存与抓取产物都在 `Storage\SkillData\`，不在本仓库里
- 离线自检：`py -3 scripts/nexus_monitor.py --selftest --print`（不联网、不碰真缓存）

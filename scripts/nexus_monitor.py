#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""潜行者2 MOD 监控（NexusMods）——静默比对抓取结果与缓存，只落盘不回显

抓取必须由浏览器完成：NexusMods 直连被 Cloudflare 挡（2026-10-22 本机实测，无头 Chrome
`--dump-dom` 排序页只拿到 28.9KB 挑战页、0 条）。本脚本**不联网**：读抓取文本 → 与缓存比
→ 有变化才写 report，靠退出码带状态，不把列表打到 stdout（数据不该回流到对话里）。

输入每行：`{数字ID} :: {标题} :: {绝对时间}`
  绝对时间取页面 `<time datetime>` / `<relative-time datetime>`；只拿到「2 hours ago」「7小时前」
  这种相对串时也接受，但**相对串不拿去判更新**（只按 ID 判新增，并在告警行里计数）。

退出码：0 无变化（report 已删） / 1 有变化（去读 report） / 2 抓到东西但解析不出条目 / 3 没抓到东西或读写失败

用法：
  py -3 nexus_monitor.py --from-file grab.txt --update
  py -3 nexus_monitor.py --from-file grab.txt --hours 48 --print
  py -3 nexus_monitor.py --selftest --print     # 离线自检，不碰真缓存
被别的脚本调用：`import nexus_monitor as m; code, rows = m.run(text, hours=24, update=True)`

缓存 `nexus_cache.json`：`ids`（见过的 ID）、`mods`（{id: {title, link, updated}}）、`last_check`、
`last_error`、`last_error_time`。判更新只认**严格晚于**缓存里那个绝对时间——所以缓存存原始绝对串。
"""
import sys, os, re, json, argparse, tempfile
from datetime import datetime, timezone, timedelta

BASE = "https://www.nexusmods.com/stalker2heartofchornobyl/mods"
DATA = r"D:\Alife\Storage\SkillData"
CACHE = os.path.join(DATA, "nexus_cache.json")
REPORT = os.path.join(DATA, "nexus_report.txt")
ERROR = os.path.join(DATA, "nexus_error.txt")
STATUS = os.path.join(DATA, "nexus_status.txt")
NAME = "潜行者2"
SKILL = "stalker2-mod-monitor"

EXIT_NO_CHANGE = 0
EXIT_CHANGED = 1
EXIT_NO_ITEMS = 2
EXIT_FAILED = 3

LOCAL_TZ = timezone(timedelta(hours=8))
EPOCH = datetime.fromtimestamp(0, timezone.utc)
RELATIVE = re.compile(r"^(\d+\s*(second|minute|hour|day|week|month)s?\s*ago|\d+\s*(秒|分钟|小时|天|周|个月)前)", re.I)
NAIVE = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")
ITEM = re.compile(r"^(?P<id>\d+)\s*::\s*(?P<title>.*?)(?:\s*::\s*(?P<time>.*))?$")


def parse_abs(raw):
    """页面时间串 → UTC datetime；空串、相对说法、认不出的一律回 None（不拿去判更新）"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        # 历史缓存里 date 存的是 epoch 秒浮点（实测 nexus_cache.json 有 50 条），0.0 是占位不当时间看
        return datetime.fromtimestamp(raw, timezone.utc) if raw > 10 ** 8 else None
    text = str(raw or "").strip()
    if not text or text in ("?", "-") or RELATIVE.match(text):
        return None
    moment = None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in NAIVE:
            try:
                moment = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if moment is None:
        return None
    if moment.tzinfo is None:
        # 页面没标时区就按本地时间算：抓取源与缓存走同一条规则，比较才不会被时区差误判
        moment = moment.replace(tzinfo=LOCAL_TZ)
    return moment.astimezone(timezone.utc)


def show(moment):
    return moment.astimezone(LOCAL_TZ).strftime("%m/%d %H:%M") if moment else "时间未知"


def load_cache():
    if os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            pass
    return {"ids": [], "mods": {}, "last_check": None, "last_error": None, "last_error_time": None}


def save_cache(cache):
    os.makedirs(DATA, exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2)


def write_error(reason, quiet, code):
    """原因落盘并记进缓存；stdout 保持干净，只有 --print 时看一眼"""
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(ERROR, "w", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(LOCAL_TZ).isoformat()} [{SKILL}] {reason}\n")
        cache = load_cache()
        cache["last_error"] = reason
        cache["last_error_time"] = datetime.now(LOCAL_TZ).isoformat()
        save_cache(cache)
    except Exception:
        pass
    if not quiet:
        print(f"[{NAME}] 退出码 {code}：{reason}")
    return code


def parse(text):
    items, seen = [], set()
    for line in text.splitlines():
        match = ITEM.match(line.strip())
        if not match:
            continue
        mid = match.group("id")
        if mid in seen:
            continue
        seen.add(mid)
        items.append({"id": mid,
                      "title": (match.group("title") or "").strip() or f"MOD {mid}",
                      "raw_time": (match.group("time") or "").strip(),
                      "link": f"{BASE}/{mid}"})
    return items


def collect(items, cache, hours):
    """分出要报的行与几类计数。判更新只认绝对时间严格晚于缓存值"""
    known = set(str(x) for x in cache.get("ids", [])) | set((cache.get("mods") or {}).keys())
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    mods = cache.get("mods") or {}
    rows, notes = [], {"unknown_time": 0, "outside": 0, "already": 0, "backfill": []}
    for item in items:
        moment = parse_abs(item["raw_time"])
        old = mods.get(item["id"]) or {}
        old_moment = parse_abs(old.get("updated") or old.get("date") or old.get("time") or "")
        if item["id"] not in known:
            tag = "新"
        elif moment is not None and old_moment is not None and moment > old_moment:
            tag = "更"
        elif moment is not None and old_moment is None:
            # 缓存里那个时间认不出来（旧版存的相对串）：证明不了「晚于」就不报，只把绝对时间补进缓存
            notes["backfill"].append((item["id"], item["raw_time"]))
            notes["already"] += 1
            continue
        else:
            notes["already"] += 1
            continue
        if moment is not None and moment < cutoff:
            notes["outside"] += 1
            continue
        if moment is None:
            notes["unknown_time"] += 1
        rows.append((tag, item, moment))
    rows.sort(key=lambda row: row[2] or EPOCH, reverse=True)
    return rows, notes, cutoff


def build_report(rows, notes, items, cutoff, hours):
    times = [moment for moment in (parse_abs(item["raw_time"]) for item in items) if moment]
    oldest = min(times) if times else None
    lines = [f"[{NAME}] 最近{hours}小时：新增 {sum(1 for r in rows if r[0] == '新')} 条、"
             f"更新 {sum(1 for r in rows if r[0] == '更')} 条（抓到 {len(items)} 条"
             + (f"，窗口外 {notes['outside']} 条" if notes["outside"] else "") + "）"]
    for index, (tag, item, moment) in enumerate(rows, 1):
        lines.append(f"{index}. {tag} {item['title']} - {show(moment)} - {item['link']}")
    if notes["unknown_time"]:
        lines.append(f"[时间告警] {notes['unknown_time']} 条只有相对时间，按ID判了新增、没拿它判更新")
    if notes["backfill"]:
        lines.append(f"[回填] {len(notes['backfill'])} 条缓存里原本没有绝对时间，这次补上了")
    if oldest and oldest > cutoff:
        lines.append(f"[覆盖告警] 抓到的最旧一条是 {show(oldest)}，还在窗口里——多半有下一页没抓，翻页后再跑")
    return lines


def apply_update(cache, rows, notes):
    known = set(str(x) for x in cache.get("ids", []))
    for tag, item, moment in rows:
        if item["id"] not in known:
            cache.setdefault("ids", []).append(item["id"])
            known.add(item["id"])
        entry = cache.setdefault("mods", {}).setdefault(item["id"], {})
        entry.update({"title": item["title"], "link": item["link"],
                      "updated": item["raw_time"] or entry.get("updated") or "时间未知"})
    for mid, raw_time in notes["backfill"]:
        cache.setdefault("mods", {}).setdefault(mid, {})["updated"] = raw_time


def run(text, hours=24, update=False, quiet=True):
    """比对并落盘。返回 (退出码, 要报的条数)。每次都在 status 里留一行纯计数"""
    code, count, stats = compare(text, hours, update, quiet)
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(STATUS, "w", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(LOCAL_TZ).strftime('%m-%d %H:%M')} [{NAME}] 退出码{code}"
                         f" 抓到{stats['got']} 缓存已含{stats['already']} 窗口外{stats['outside']}"
                         f" 新增{stats['new']} 更新{stats['updated']} 窗口{hours}小时"
                         f"{' 时间告警' + str(stats['unknown_time']) + '条' if stats['unknown_time'] else ''}"
                         + (f" {stats['note']}" if stats.get("note") else "") + "\n")
    except Exception:
        pass
    return code, count


def compare(text, hours, update, quiet):
    stats = {"got": 0, "already": 0, "outside": 0, "new": 0, "updated": 0, "unknown_time": 0, "note": ""}
    if not text.strip():
        return write_error("抓取结果是空的（浏览器那一步没拿到列表）", quiet, EXIT_FAILED), 0, stats
    items = parse(text)
    stats["got"] = len(items)
    if not items:
        stats["note"] = "一行都没解析出来，先看抓取那一步"
        return write_error(f"抓到 {len(text.splitlines())} 行，但没有一行是 `数字ID :: 标题 :: 时间`",
                           quiet, EXIT_NO_ITEMS), 0, stats

    cache = load_cache()
    rows, notes, cutoff = collect(items, cache, hours)
    stats.update({"already": notes["already"], "outside": notes["outside"], "unknown_time": notes["unknown_time"],
                  "new": sum(1 for row in rows if row[0] == "新"), "updated": sum(1 for row in rows if row[0] == "更")})
    lines = build_report(rows, notes, items, cutoff, hours)

    if rows:
        with open(REPORT, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    elif os.path.exists(REPORT):
        os.remove(REPORT)

    if update:
        apply_update(cache, rows, notes)
        cache["last_check"] = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        cache["last_error"] = None
        save_cache(cache)

    if not quiet:
        print("\n".join(lines) if rows else f"[{NAME}] 无新增/更新（抓到 {len(items)} 条，缓存已含 {stats['already']} 条，窗口 {hours} 小时）")
    return ((EXIT_CHANGED, len(rows), stats) if rows else (EXIT_NO_CHANGE, 0, stats))


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--from-file", dest="from_file")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--print", dest="echo", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest(args.echo)

    if args.from_file:
        try:
            with open(args.from_file, encoding="utf-8") as handle:
                text = handle.read()
        except Exception as read_error:
            return write_error(f"读不到抓取文件 {args.from_file}：{read_error}", not args.echo, EXIT_FAILED)
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        return write_error("没给 --from-file，也没有管道输入", not args.echo, EXIT_FAILED)
    code, _ = run(text, hours=args.hours, update=args.update, quiet=not args.echo)
    return code


def _selftest(echo):
    """离线自检：缓存/report/error 全改到临时目录，绝不碰真数据"""
    global DATA, CACHE, REPORT, ERROR, STATUS
    DATA = tempfile.mkdtemp(prefix="monitor_selftest_")
    CACHE = os.path.join(DATA, "cache.json")
    REPORT = os.path.join(DATA, "report.txt")
    ERROR = os.path.join(DATA, "error.txt")
    STATUS = os.path.join(DATA, "status.txt")

    checks = []
    for raw, want in (("2026-10-20T23:07", True), ("2026-10-15T17:17:42+00:00", True),
                      ("2026-10-15T17:17:42Z", True), ("2026-10-20 23:07", True),
                      ("2 hours ago", False), ("7小时前", False), ("昨天", False),
                      ("", False), ("?", False)):
        checks.append((f"时间判定 {raw!r}", parse_abs(raw) is not None, want))
    checks.append(("解析·三字段行", len(parse("1 :: 甲 :: 2026-10-20T23:07")), 1))
    checks.append(("解析·没有时间的行也收", parse("2 :: 乙")[0]["raw_time"], ""))
    checks.append(("解析·非数字ID跳过", len(parse("abc :: 甲")), 0))
    checks.append(("解析·同一ID重复只留一条",
                    len(parse("1 :: 甲 :: 2026-10-20T23:07\n1 :: 甲 :: 2026-10-20T23:07")), 1))

    fresh = {"ids": [], "mods": {}}
    rows, notes, _ = collect(parse("1 :: 甲 :: 2026-10-20T23:07\n2 :: 乙 :: 2 hours ago"), fresh, 24 * 365)
    checks.append(("判新增·两个陌生ID都报", len(rows), 2))
    checks.append(("时间告警·相对串计数为1", notes["unknown_time"], 1))

    seen = {"ids": ["1"], "mods": {"1": {"updated": "2026-10-20T23:07"}, "2": {"updated": "2026-10-01T00:00"}}}
    rows, notes, _ = collect(parse("1 :: 甲 :: 2026-10-20T23:07\n2 :: 乙 :: 2026-10-01T00:00"), seen, 24 * 365)
    checks.append(("无变化·时间没晚于就不报（整页误报的根因）", len(rows), 0))
    rows, _, _ = collect(parse("2 :: 乙 :: 2026-10-22T10:00"), seen, 24 * 365)
    checks.append(("判更新·严格晚于才报", len(rows), 1))
    stale = {"ids": ["1"], "mods": {"1": {"time": "7小时前"}}}
    rows, notes, _ = collect(parse("1 :: 甲 :: 2026-10-22T10:00"), stale, 24 * 365)
    checks.append(("回填·缓存里只有相对时间时先补上、不误报更新", len(rows) == 0 and len(notes["backfill"]) == 1, True))
    rows, notes, _ = collect(parse("1 :: 甲 :: 2026-10-22T10:00"), seen, 24 * 365)
    checks.append(("判更新·缓存有绝对时间且晚于它才报", len(rows), 1))
    rows, notes, _ = collect(parse("1 :: 甲 :: 2026-10-01T00:00"), seen, 1)
    checks.append(("窗口·早于窗口的不报", len(rows), 0))

    # 真缓存里的历史形状：date 是 epoch 浮点、0.0 是占位（实测 nexus_cache.json 有 50 条这种）
    now_text = datetime.now(LOCAL_TZ).strftime("%Y-%m-%dT%H:%M")
    checks.append(("历史缓存·epoch浮点认得出", parse_abs(1788222600.0) is not None, True))
    checks.append(("历史缓存·0.0 当没有时间", parse_abs(0.0), None))
    checks.append(("历史缓存·数字之外的怪值不炸", parse_abs(None), None))
    epoch_rows, _, _ = collect(parse("731 :: 甲 :: " + now_text),
                              {"ids": ["731"], "mods": {"731": {"date": 1788222600.0}}}, 24 * 365)
    checks.append(("历史缓存·晚于epoch值就判更新", len(epoch_rows), 1))
    zero_rows, zero_notes, _ = collect(parse("2544 :: 乙 :: " + now_text),
                                      {"ids": ["2544"], "mods": {"2544": {"date": 0.0, "time": "45分钟前"}}}, 24 * 365)
    checks.append(("历史缓存·占位0走回填不误报", len(zero_rows) == 0 and len(zero_notes["backfill"]) == 1, True))

    code, _ = run("乱七八糟不成格式的一行", quiet=True)
    checks.append(("退出码·解析不出条目是2", code, EXIT_NO_ITEMS))
    code, _ = run("", quiet=True)
    checks.append(("退出码·空输入是3", code, EXIT_FAILED))
    sample_new = f"9 :: 新来的 :: {now_text}"
    code, _ = run(sample_new, quiet=True)
    checks.append(("退出码·有变化是1", code, EXIT_CHANGED))
    checks.append(("report 落了盘", os.path.exists(REPORT), True))
    code, _ = run(sample_new, quiet=True)
    checks.append(("未写缓存时同一份仍报1（下次 --update 才吃掉）", code, EXIT_CHANGED))
    run(sample_new, update=True, quiet=True)
    checks.append(("缓存写进去了", os.path.exists(CACHE) and "9" in (load_cache().get("mods") or {}), True))
    code, _ = run(sample_new, quiet=True)
    checks.append(("退出码·同一份再跑是0", code, EXIT_NO_CHANGE))
    checks.append(("无变化时 report 被删掉", os.path.exists(REPORT), False))
    status = open(STATUS, encoding="utf-8").read()
    checks.append(("status 说清是缓存已含、不是没抓到", "抓到1 缓存已含1 窗口外0 新增0 更新0" in status, True))
    checks.append(("status 里不带条目名（不往对话塞数据）", "新来的" in status, False))

    failed = [f"{label} → 实际 {actual!r} 期望 {want!r}"
              for label, actual, want in checks if actual != want]
    if echo:
        for label, actual, want in checks:
            print(("PASS  " if actual == want else "FAIL  ") + label + ("" if actual == want else f" → {actual!r}≠{want!r}"))
        print(f"SELFTEST {'OK' if not failed else 'FAIL'}（{len(checks)} 项）")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

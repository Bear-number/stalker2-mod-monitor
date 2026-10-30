#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""潜行者2（NexusMods）列表页的自动抓取——脚本自己起**真浏览器**，用 CDP 取数据，不用人开页面

为什么要真浏览器：NexusMods 前面是 Cloudflare，2026-10-22 实测无头 Chrome 只拿到挑战页、直连同样被挡，
而真浏览器抓得动。这里用独立 user-data-dir 起一个正常的（非无头）msedge/chrome 窗口，连它的 DevTools
调试口，导航后边滚边轮询 `Runtime.evaluate` 跑抽取 JS（列表是滚动加载的），抓到就写盘、把自己起的
浏览器进程关掉。数据只在脚本与文件之间走，不进对话（和接收器解决的是同一个问题）。

Nexus 的两个毛病（2026-10-30 本机实测，所以两条地址都试、每条重载一次）：
① `/games/<game>/mods?sort=updatedAt` 十次有六七次只回页面壳子、列表根本不渲染（不是 CF：页内没有
   challenges iframe、标题正常，56 秒等不到一条链接）；② 不带 `games/` 的那条会 302 到
   `/games/<game>/mods` 并**把 `?sort=updatedAt` 丢掉**。每条卡片自带绝对时间，所以排序被丢掉只影响
   覆盖面，不影响判新增/更新。

退出码：0 抓到并写盘 / 2 两条地址都试过仍一条没抽到（多半是改版）/ 3 Cloudflare 挑战页没过去
        / 4 浏览器起不来或 CDP 连不上 / 5 抓到但写盘失败
全程零 stdout（`--print` 才回一行结论），失败原因写 `SkillData\cdp_nexus_error.txt`。

用法：
  py -3 cdp_grab.py --print            # 起浏览器、两条列表地址轮着试、写 grab 文件
  py -3 cdp_grab.py --pages 3          # 渲出列表后再多滚两轮「加载更多」（默认 2 页量）
  py -3 cdp_grab.py --url "<别的排序地址>"
比对：py -3 nexus_monitor.py --from-file <out> --update
"""
import sys, os, json, time, socket, struct, base64, argparse, subprocess, shutil
import urllib.request
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

GAME = "stalker2heartofchornobyl"
URLS = [f"https://www.nexusmods.com/games/{GAME}/mods?sort=updatedAt",
        f"https://www.nexusmods.com/{GAME}/mods?sort=updatedAt"]
DATA = r"D:\Alife\Storage\SkillData"
DEFAULT_OUT = os.path.join(DATA, "nexus_grab.txt")
ERROR = os.path.join(DATA, "cdp_nexus_error.txt")
PROFILE = os.path.join(DATA, "browser-nexus")
NAME = "潜行者2"
SKILL = "stalker2-mod-monitor"

EXIT_OK = 0
EXIT_NO_ROWS = 2
EXIT_CHALLENGE = 3
EXIT_BROWSER = 4
EXIT_WRITE = 5
LOCAL_TZ = timezone(timedelta(hours=8))

# 页内抽取：一次把「行 + 页面状态」一起回出来，脚本才分得清「没抓到」和「被 CF 挡着」这两种失败
JS = r"""(() => {
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
  return JSON.stringify({
    title: document.title,
    url: location.href,
    challenge: /just a moment|verifying|attention required/i.test(document.title)
      || !!document.querySelector('iframe[src*="challenges.cloudflare.com"], input[name="cf_chl_opt"]'),
    count: rows.length,
    text: rows.join('\n')
  });
})()"""

# 列表是滚出来的：滚到底、有「加载更多」这个按钮就点一下，等它长出来再抽
PUMP = r"""(() => {
  window.scrollTo(0, document.body.scrollHeight);
  // 只点 button：列表页里文字像「加载更多」的 <a> 有好几个，点中一个就把页面导航走了
  const more = [...document.querySelectorAll('button, [role=button]')].find(e => /load more|加载更多/i.test((e.innerText || '').trim()));
  if (more) more.click();
  return 1;
})()"""


def write_error(reason, quiet):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(ERROR, "w", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(LOCAL_TZ).isoformat()} [{SKILL}] cdp_grab：{reason}\n")
    except Exception:
        pass
    if not quiet:
        print(f"[{NAME}] cdp_grab 失败：{reason}")


def find_browser():
    """自己传 --browser 优先；否则按常见路径 + PATH 找 msedge/chrome"""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            candidates.insert(0, found)
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def run_profile(base_profile):
    """一次性 profile：固定 profile 被上一轮没退干净的进程占着时用它兜底。
    超过一天的旧目录顺手清掉，最多留 5 个"""
    try:
        keep = []
        if os.path.isdir(base_profile):
            for name in os.listdir(base_profile):
                full = os.path.join(base_profile, name)
                if not os.path.isdir(full):
                    continue
                if time.time() - os.path.getmtime(full) > 86400:
                    shutil.rmtree(full, ignore_errors=True)
                else:
                    keep.append(full)
        if len(keep) >= 6:
            for stale in sorted(keep)[:len(keep) - 5]:
                shutil.rmtree(stale, ignore_errors=True)
    except Exception:
        pass
    return os.path.join(base_profile, datetime.now(LOCAL_TZ).strftime("%Y%m%d-%H%M%S-%f"))


def launch(browser, port, profile, start_url):
    os.makedirs(profile, exist_ok=True)
    args = [browser, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-session-crashed-bubble",
            "--window-size=1280,1000", "--window-position=-3200,-3200", "--new-window", start_url]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def http_json(url, timeout=2.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_devtools(port, deadline):
    """浏览器冷启动要几秒；起来了才有 /json/version 和 page 目标"""
    while time.time() < deadline:
        try:
            http_json(f"http://127.0.0.1:{port}/json/version")
            pages = [target for target in http_json(f"http://127.0.0.1:{port}/json/list") if target.get("type") == "page"]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.4)
    return None


class Cdp:
    """DevTools 协议最小客户端：handshake + 发文本帧 + 按 id 收回复（纯标准库，不装 websockets）"""

    def __init__(self, ws_url):
        parts = urlparse(ws_url)
        self.sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parts.path + (("?" + parts.query) if parts.query else "")
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buffer = b""
        self.fragments = b""
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("CDP handshake 断了")
            self.buffer += chunk
        head, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        if b"101" not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("CDP handshake 不是 101：" + head[:120].decode("ignore"))
        self.next_id = 0

    def _frame(self, payload):
        header = bytearray([0x81])
        length = len(payload)
        mask = os.urandom(4)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        header += mask
        return bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    def send(self, method, params=None):
        self.next_id += 1
        call_id = self.next_id
        self.sock.sendall(self._frame(json.dumps({"id": call_id, "method": method, "params": params or {}}).encode()))
        while True:
            message = json.loads(self._read_message())
            if message.get("id") == call_id:
                if message.get("error"):
                    raise RuntimeError(f"{method}：{message['error'].get('message', message['error'])}")
                return message.get("result", {})

    def close_browser(self):
        try:
            self.send("Browser.close")
        except Exception:
            pass

    def evaluate(self, expression):
        result = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return (result.get("result") or {}).get("value")

    def _need(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("CDP 连接被关掉")
            self.buffer += chunk
        taken, self.buffer = self.buffer[:count], self.buffer[count:]
        return taken

    def _read_message(self):
        while True:
            first, second = self._need(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._need(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._need(8))[0]
            payload = self._need(length) if length else b""
            opcode = first & 0x0F
            if opcode == 8:
                raise ConnectionError("CDP 那头把连接关了")
            if opcode == 9:
                self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + payload)
                continue
            if opcode in (0, 1, 2):
                self.fragments += payload
                if first & 0x80:
                    text, self.fragments = self.fragments, b""
                    return text.decode("utf-8", "ignore")


def open_browser(browser, base_profile, start_url, patience):
    """先用**固定 profile**（登录态/cookie 会影响 Nexus 列不列列表）；它被上一轮没退干净的进程
    占着时调试口永远不起来，这时退到一次性 profile 再试一次"""
    for profile in (base_profile, run_profile(base_profile)):
        port = free_port()
        process = launch(browser, port, profile, start_url)
        ws_url = wait_devtools(port, time.time() + patience)
        if ws_url:
            return process, Cdp(ws_url)
        try:
            process.terminate()
            process.wait(timeout=6)
        except Exception:
            process.kill()
    return None, None


def peek(cdp):
    try:
        raw = cdp.evaluate(JS)
    except Exception:
        return None
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


def settle(cdp, deadline, settle_seconds):
    """轮着等页面出条目：过 CF、React 把列表渲出来都要时间，一次没抓到就判失败太急"""
    last = None
    while time.time() < deadline:
        snapshot = peek(cdp)
        if snapshot:
            last = snapshot
            if snapshot.get("count"):
                return snapshot
        time.sleep(settle_seconds)
    return last


def pump_more(cdp, best, rounds):
    """已经渲出列表了就再多滚几轮，把「加载更多」后面的吃进来；只留条目最多的一帧"""
    for _ in range(max(0, rounds)):
        before = best.get("count") or 0
        for _ in range(6):
            try:
                cdp.evaluate(PUMP)
            except Exception:
                return best
            time.sleep(1.0)
            snapshot = peek(cdp)
            if snapshot and (snapshot.get("count") or 0) > (best.get("count") or 0):
                best = snapshot
        if (best.get("count") or 0) <= before:
            break
    return best


class ArgParser(argparse.ArgumentParser):
    """参数用错时 argparse 默认 exit(2)——那个码在本脚本里另有所指，会骗到读退出码的人；
    这里统一退成 9（用法错误），用法行照样给到 stderr，也不往 stdout 吐东西"""

    def error(self, message):
        sys.stderr.write("用法错误：" + message + "（看脚本开头的用法说明）\n")
        raise SystemExit(9)


def main(argv=None):
    parser = ArgParser(add_help=False)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--browser")
    parser.add_argument("--url")
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--settle", type=float, default=2.0)
    parser.add_argument("--print", dest="echo", action="store_true")
    args = parser.parse_args(argv)
    quiet = not args.echo

    browser = args.browser or find_browser()
    if not browser:
        write_error("这台机器没找到 msedge/chrome，改走接收器那条老路子", quiet)
        return EXIT_BROWSER

    urls = [args.url] if args.url else URLS
    patience = min(max(20.0, args.settle * 6), args.timeout * 0.3)
    process = None
    cdp = None
    try:
        process, cdp = open_browser(browser, PROFILE, urls[0], patience)
        if cdp is None:
            write_error("起浏览器两次都等不到调试口（固定 profile 与一次性 profile 各一次）——"
                        "窗口被组策略拦了，或有别的实例占着 profile", quiet)
            return EXIT_BROWSER
        best = None
        budget = max(12.0, (args.timeout - patience) / (len(urls) * 2))
        for url in urls:
            for _ in (1, 2):
                try:
                    cdp.send("Page.navigate", {"url": url})
                except Exception:
                    break
                snapshot = settle(cdp, time.time() + budget, args.settle)
                if snapshot and snapshot.get("count"):
                    best = snapshot
                    break
                if snapshot and best is None:
                    best = snapshot
            if best and best.get("count"):
                break
        if best and best.get("challenge"):
            write_error(f"Cloudflare 挑战页没过去（页面标题「{best.get('title')}」）："
                        "先真人开一次浏览器把这站过了（同一个 profile 会记住），再跑这里；还不行就用接收器", quiet)
            return EXIT_CHALLENGE
        if not (best and best.get("count")):
            write_error(f"两条列表地址都试过，一条都没抽到（最后标题「{(best or {}).get('title', '没拿到页面状态')}」）"
                        "——要么页面改版选择器对不上，要么 Nexus 这次就是不给列表；先用接收器那一版应急", quiet)
            return EXIT_NO_ROWS
        best = pump_more(cdp, best, args.pages - 1)
        rows, seen_ids = [], set()
        for line in (best.get("text") or "").splitlines():
            mid = line.split("::")[0].strip()
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                rows.append(line)
        try:
            os.makedirs(os.path.dirname(args.out), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write("\n".join(rows) + "\n")
        except Exception as write_failure:
            write_error(f"抓到 {len(rows)} 条但写 {args.out} 失败：{write_failure}", quiet)
            return EXIT_WRITE
        try:
            if os.path.exists(ERROR):
                os.remove(ERROR)
        except Exception:
            pass
        if not quiet:
            print(f"[{NAME}] cdp_grab 抓到 {len(rows)} 条（落点 {best.get('url', '')[:56]}）→ {args.out}")
        return EXIT_OK
    except Exception as crash:
        write_error(f"跑挂了：{crash.__class__.__name__} {crash}", quiet)
        return EXIT_BROWSER
    finally:
        if cdp is not None:
            cdp.close_browser()
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=8)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    sys.exit(main())

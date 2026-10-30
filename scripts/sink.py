#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""浏览器抓取回传接收器（潜行者2 / NexusMods）——数据直接落盘，不经过对话

浏览器里抓到的列表如果让 JS `return` 回来，整页数据就进了对话上下文（白烧 token）。这个接收器在本机
`127.0.0.1` 开一个一次性端口，页面用**隐藏 form 的顶层提交**把数据 POST 进来，直接写文件后退出。
（实测 https 页面里 `fetch`/XHR 打 loopback 会被 Chrome 的私有网络访问(PNA)拦死，补
`Access-Control-Allow-Private-Network` 也没用；顶层提交的导航不受 PNA 管。）

退出码：0 收到并写好了 / 2 收到了但内容是空的 / 4 超时没收到东西
全程零 stdout（只有 --print 会给一行结论），失败原因写 error 文件。

用法（先起接收器，再让浏览器提交）：
  py -3 sink.py --timeout 150
  py -3 sink.py --out "D:\Alife\Storage\SkillData\nexus_grab.txt" --port 38471 --print

配套的抓取 JS（runwebsitejs 里跑，抓完自己提交）见 SKILL.md。
"""
import sys, os, time, argparse
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

DATA = r"D:\Alife\Storage\SkillData"
DEFAULT_OUT = os.path.join(DATA, "nexus_grab.txt")
ERROR = os.path.join(DATA, "nexus_error.txt")
NAME = "潜行者2"
SKILL = "stalker2-mod-monitor"
DEFAULT_PORT = 38471

EXIT_OK = 0
EXIT_EMPTY = 2
EXIT_TIMEOUT = 4
LOCAL_TZ = timezone(timedelta(hours=8))

_state = {"received": False, "lines": 0}


def write_error(reason, quiet):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(ERROR, "w", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(LOCAL_TZ).isoformat()} [{SKILL}] sink：{reason}\n")
    except Exception:
        pass
    if not quiet:
        print(f"[{NAME}] sink 失败：{reason}")


class Receiver(BaseHTTPRequestHandler):
    out_path = DEFAULT_OUT

    def log_message(self, *args):
        """默认会把每次请求打到 stderr——闭掉，接收器全程静默"""

    def _reply(self):
        page = (f"<meta charset=utf-8><body style=font-family:sans-serif>已收到 {_state['lines']} 行，"
                f"可以关掉这页了" if _state["received"] else "没有收到数据")
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _store(self, text):
        lines = [line.strip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
                 if line.strip()]
        _state["received"] = True
        _state["lines"] = len(lines)
        os.makedirs(os.path.dirname(self.out_path), exist_ok=True)
        with open(self.out_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + ("\n" if lines else ""))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "ignore") if length else ""
        fields = parse_qs(raw, keep_blank_values=True)
        self._store(fields.get("data", [""])[0] or raw)
        self._reply()

    def do_GET(self):
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        fields = parse_qs(query, keep_blank_values=True)
        self._store(fields.get("data", fields.get("d", [""]))[0])
        self._reply()


class ArgParser(argparse.ArgumentParser):
    """参数用错时 argparse 默认 exit(2)——那个码在本脚本里另有所指，会骗到读退出码的人；
    这里统一退成 9（用法错误），用法行照样给到 stderr，也不往 stdout 吐东西"""

    def error(self, message):
        sys.stderr.write("用法错误：" + message + "（看脚本开头的用法说明）\n")
        raise SystemExit(9)


def main(argv=None):
    parser = ArgParser(add_help=False)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--print", dest="echo", action="store_true")
    args = parser.parse_args(argv)
    quiet = not args.echo

    Receiver.out_path = args.out
    try:
        server = HTTPServer(("127.0.0.1", args.port), Receiver)
    except OSError as bind_error:
        write_error(f"端口 {args.port} 起不来（可能被占）：{bind_error}", quiet)
        return EXIT_TIMEOUT

    server.timeout = 1
    deadline = time.time() + max(5, args.timeout)
    while time.time() < deadline and not _state["received"]:
        server.handle_request()
    server.server_close()

    if not _state["received"]:
        write_error(f"等 {args.timeout} 秒没收到任何提交（浏览器那一步没跑，或端口不对）", quiet)
        return EXIT_TIMEOUT
    if _state["lines"] == 0:
        write_error("收到了提交但里面没有一行有效数据", quiet)
        return EXIT_EMPTY
    if not quiet:
        print(f"[{NAME}] sink 收到 {_state['lines']} 行 → {args.out}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

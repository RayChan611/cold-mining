#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冷门/金融报告静态服务（自托管，不依赖 GitHub Pages）。

- 监听 0.0.0.0:80（可用环境变量 COLD_SERVE_PORT 覆盖），根目录 = /home/ubuntu/cold-mining
- 只放行站点自身需要的文件：.html / .css / .js / 图片 / 字体 / .txt
- 屏蔽：隐藏目录、.env、_archive、__pycache__、*.bak*、*.py、*.md、*.log、*.json、template.html
- 关闭目录列表（2026-09-30 新增）—— 之前 /reports/ 会把每份报告的 .bak 备份和 _archive 里的
  重复报告一并列出来，看起来就是「已发布的报告有重复」
- 目录自动定位 index.html；保留 .well-known/acme-challenge 以支持证书续期

运行：sudo python3 serve_cold.py   （端口 80 需 root）
"""
import os
from urllib.parse import urlparse, unquote
import http.server
import socketserver

ROOT = "/home/ubuntu/cold-mining"
PORT = int(os.environ.get("COLD_SERVE_PORT", "80"))

ALLOWED_EXT = {
    ".html", ".htm", ".css", ".js", ".mjs", ".txt",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".woff", ".woff2", ".ttf",
}
BLOCKED_DIRS = {"_archive", "__pycache__"}
BLOCKED_NAMES = {"template.html"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def _forbidden(self):
        self.send_error(403, "Forbidden")

    def _allowed(self):
        """返回 True 表示放行该请求路径。"""
        parts = [unquote(x) for x in urlparse(self.path).path.split("/") if x]
        if not parts:                                   # 根路径 -> index.html
            return True
        if len(parts) == 3 and parts[:2] == [".well-known", "acme-challenge"]:
            return True                                 # ACME 校验需放行

        for p in parts:
            if p.startswith(".") or p in BLOCKED_DIRS:
                self._forbidden()                       # 隐藏目录 / 内部目录
                return False
            if ".bak" in p or p in BLOCKED_NAMES:
                self._forbidden()                       # 备份 / 内部模板
                return False
            if p.endswith((".py", ".pyc", ".md", ".log", ".json", ".sh", ".env")):
                self._forbidden()                       # 脚本 / 日志 / 状态文件
                return False

        full = os.path.join(ROOT, *parts)
        if os.path.isdir(full):
            if not os.path.isfile(os.path.join(full, "index.html")):
                self._forbidden()                       # 目录列表已关闭
                return False
        else:
            ext = os.path.splitext(parts[-1])[1].lower()
            if ext and ext not in ALLOWED_EXT:
                self._forbidden()                       # 不在白名单的文件类型
                return False
        return True

    def do_GET(self):
        if self._allowed():
            super().do_GET()

    def do_HEAD(self):
        if self._allowed():
            super().do_HEAD()

    def list_directory(self, path):
        self.send_error(403, "Forbidden")
        return None

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    os.chdir(ROOT)
    with Server(("0.0.0.0", PORT), Handler) as httpd:
        print(f"serving {ROOT} on 0.0.0.0:{PORT}")
        httpd.serve_forever()

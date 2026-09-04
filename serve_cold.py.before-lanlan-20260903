#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冷门/金融报告静态服务（自托管，不依赖 GitHub Pages）。

- 监听 0.0.0.0:80，根目录 = /home/ubuntu/cold-mining
- 屏蔽所有点文件目录（.git / .env 等），避免泄露仓库与密钥
- 目录自动定位 index.html
运行：sudo python3 serve_cold.py   （端口 80 需 root）
"""
import os
from urllib.parse import urlparse
import http.server
import socketserver

ROOT = "/home/ubuntu/cold-mining"


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # 按 URL 路径分段拦截隐藏文件/目录与 .env，避免泄露密钥与 git 历史
        # 注意：根路径 "/" 解析为 [""], 不应被误伤
        parts = [x for x in urlparse(self.path).path.split("/") if x]
        if any(x.startswith(".") for x in parts) or ".env" in parts:
            self.send_error(403, "Forbidden")
            return
        super().do_GET()

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
    with Server(("0.0.0.0", 80), Handler) as httpd:
        httpd.serve_forever()

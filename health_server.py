#!/usr/bin/env python3
"""
Railway 健康检查 HTTP 服务器
在后台运行监控服务，同时提供 HTTP 健康检查端点
"""
import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

# 确保项目根目录在 Python 路径中
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class HealthCheckHandler(BaseHTTPRequestHandler):
    """简单的健康检查处理器"""

    def do_GET(self):
        if self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"healthy","service":"arbitrage-monitor"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """禁用访问日志"""
        pass


def run_health_server(port=5000):
    """在后台线程运行健康检查服务器"""
    def server_thread():
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        print(f"[Health] HTTP server running on port {port}")
        server.serve_forever()

    thread = threading.Thread(target=server_thread, daemon=True)
    thread.start()
    return thread


def main():
    """主函数：启动健康检查服务器，然后运行监控服务"""
    print()
    print("=" * 70)
    print("  Railway Health Server + Arbitrage Monitor")
    print("=" * 70)
    print()

    # 获取端口（Railway 会设置 PORT 环境变量）
    port = int(os.getenv('PORT', 5000))

    # 启动健康检查服务器（后台线程）
    health_thread = run_health_server(port)

    # 等待服务器启动
    time.sleep(1)

    # 运行监控服务
    print("[Monitor] Starting arbitrage monitor...")
    import continuous_monitor
    sys.exit(continuous_monitor.main())


if __name__ == '__main__':
    sys.exit(main())

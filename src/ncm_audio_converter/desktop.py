from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from ncm_audio_converter import web as web_module


def _find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _guess_frontend_dist() -> Path | None:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path.cwd()))
        for candidate in [base / "frontend_dist", base / "frontend" / "dist"]:
            if (candidate / "index.html").exists():
                return candidate

    project_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (project_dist / "index.html").exists():
        return project_dist
    return None


def _wait_server(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            try:
                s.connect((host, port))
                return True
            except OSError:
                time.sleep(0.15)
    return False


def _run_server(host: str, port: int) -> None:
    if web_module.app is None:
        raise RuntimeError("Flask 未安装，无法启动桌面应用")
    web_module.app.run(host=host, port=port, debug=False, use_reloader=False)


def main() -> int:
    dist = _guess_frontend_dist()
    if dist is not None:
        os.environ["NCM_FRONTEND_DIST"] = str(dist)

    host = "127.0.0.1"
    port = _find_free_port(host)

    thread = threading.Thread(target=_run_server, args=(host, port), daemon=True)
    thread.start()

    if not _wait_server(host, port):
        print("后端启动超时")
        return 2

    url = f"http://{host}:{port}"
    try:
        import webview

        webview.create_window("NCM Audio Converter", url=url, width=1200, height=860, min_size=(960, 640))
        webview.start()
        return 0
    except Exception as err:
        # Fallback to browser if webview runtime is unavailable.
        print(f"webview 启动失败，已回退浏览器模式: {err}")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())

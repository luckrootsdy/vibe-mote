# -*- coding: utf-8 -*-
r"""VibeMote 遥控器启动器（EXE 版，可固定到任务栏）。

替代 启动遥控器.vbs。纯 Python，用 PyInstaller --onefile --windowed 打包：
* 无黑控制台窗口；
* 找本地 pythonw（**优先程序目录 runtime\pythonw.exe**，其次 .venv\Scripts\pythonw.exe）；
* 启动 app.py --silent --heal（已有实例则只发 heal，不动按键映射、不重启进程）；
* 等待就绪（最多 25 秒）后打开浏览器；失败则弹中文提示框并指向 client.log。
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser


def resource_dir():
    """exe 所在目录（用户存放 exe 的地方），app.py / config.json / client.log 都在这里。"""
    return os.path.dirname(os.path.abspath(sys.executable))


def find_pythonw(base):
    r"""找该用来跑 app.py 的 pythonw.exe。

    ★ 顺序是 **runtime\ 优先，.venv 其次**（2026-11-12 改的，别调回去）。

    为什么不能沿用"`.venv` 优先"：`.venv` **不是自带 Python**，它只是壳 ——
    `pyvenv.cfg` 里 `home = C:\\Python312`，标准库和 `python312.dll` 都在系统 Python
    那边。装到一台没装过 Python 的电脑上，它只会回一句
    `No Python at 'C:\\Python312\\pythonw.exe'`（退出码 103），用户看到的是"双击没反应"。

    `runtime\\` 是官方 embeddable 发行版 + 复制进来的 site-packages，prefix 就是自己，
    拷到任何机器上都能跑。开发机上两者都有（.venv 是给开发用的），必须让 runtime 赢。
    """
    cands = [
        os.path.join(base, "runtime", "pythonw.exe"),
        os.path.join(base, ".venv", "Scripts", "pythonw.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"),
        r"C:\Python313\pythonw.exe",
        r"C:\Python312\pythonw.exe",
        r"C:\Python311\pythonw.exe",
        r"C:\Python310\pythonw.exe",
        "pythonw.exe",
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def read_port(base):
    p = os.path.join(base, "config.json")
    port = 8787
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
        v = cfg.get("ui_port")
        if v:
            port = int(v)
    except Exception:
        pass
    return port


def server_up(port, timeout=1.5):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def marker_stamp(base):
    p = os.path.join(base, "_ready.txt")
    try:
        return os.stat(p).st_mtime
    except Exception:
        return None


def send_heal(port):
    """向已运行实例发 {"action":"heal"}：只重建语音链路，不动按键映射、不重启进程。"""
    try:
        data = json.dumps({"action": "heal"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/action",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


def msgbox(text, title="VibeMote", flags=0x30):
    ctypes.windll.user32.MessageBoxW(0, text, title, flags)


def main():
    base = resource_dir()
    py = find_pythonw(base)
    if not py:
        msgbox(
            "未找到 pythonw.exe —— 程序自带的运行时（runtime\\）不见了，\n"
            "安装包可能没装完整或被安全软件删掉了。\n\n"
            "修法：重新运行 VibeMote 安装程序（选同一个目录，会补回缺失文件）。",
            "VibeMote - 运行时缺失", 0x10)
        return 1

    port = read_port(base)

    # 若已有实例在跑：只发 heal（等价启动遥控器.vbs 的 --heal），然后打开浏览器。
    if server_up(port):
        send_heal(port)
        webbrowser.open(f"http://127.0.0.1:{port}/")
        return 0

    # 否则启动 app.py（隐藏窗口，--silent；--heal 让已有实例重建语音链路，无实例则正常启动）。
    cmd = [py, os.path.join(base, "app.py"), "--silent", "--port", str(port), "--heal"]
    try:
        subprocess.Popen(cmd, cwd=base, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        msgbox(f"启动失败：{e}", "VibeMote - 启动失败", 0x10)
        return 1

    # 等待就绪：_ready.txt 刷新 或 HTTP 可响应，最多 25 秒。
    before = marker_stamp(base)
    deadline = time.time() + 25
    ok = False
    while time.time() < deadline:
        now = marker_stamp(base)
        if now is not None and now != before:
            ok = True
            break
        if server_up(port):
            ok = True
            break
        time.sleep(0.7)

    if ok:
        webbrowser.open(f"http://127.0.0.1:{port}/")
        return 0

    msgbox(
        f"VibeMote 未能在 25 秒内启动。\n"
        f"请查看 {os.path.join(base, 'client.log')} 的最后几行，或把程序目录放到杀毒软件白名单后重试。",
        "VibeMote - 启动超时", 0x30)
    return 1


if __name__ == "__main__":
    sys.exit(main())

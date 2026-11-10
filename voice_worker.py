# -*- coding: utf-8 -*-
"""语音 worker：把 BLE 连接单独放进一个短命进程（2026-11-10）。

【为什么要有它】
Windows 的 WinRT 蓝牙设备对象一旦建起来，它内部的 GATT 服务快照就"冻住"了：
进程内再怎么 close 句柄、重建 BluetoothLEDevice、换 UNCACHED 重新发现，
拿回来的仍然只有 1800 / 1801 / 180a / 180f 四个基础服务，ATVV（ab5e0001）
整个不在。小柯的对照实验把这件事钉死了：同一秒里，App 进程连续 28 次只数出
4 个服务，另起一个独立进程直接问 Windows，CACHED 和 UNCACHED 都是 8 个。
所以：**只有跨进程边界才能重置这份快照。**

【怎么用】
主进程（app.py）不再自己连 BLE，而是拉起本文件当"语音 worker"：
  · worker 自己连 BLE、自己解码 ADPCM、自己写虚拟声卡、自己发语音键手势；
  · worker 的 stdout 每行一个 JSON，主进程读它来更新界面和日志；
  · 主进程往 stdin 写 poke / stop / addr / spec，worker 收到立刻照办；
  · worker 发现"连上了、但连续 20 秒（或累计 3 次）只剩 4 个基础服务"→ **自己退出**，
    主进程换一个全新的进程，新进程第一次枚举就是完整 8 个服务；
  · worker 发现"连续 45 秒根本连不上"→ 也**自己退出**（退出码 4），主进程按
    2/15/30/60/120 秒的阶梯换新进程 —— 真机数据：换进程后 8~9 秒就绪。

【刻意不做的事】
· 换进程不是万能的：设备深睡时它救不回来（只能等遥控器上的物理按键）。但也不能
  像 v1.2.1 之前那样"一个进程死等到底" —— 真机 11:37:42~11:46:36 死等了 534 秒，
  用户按了半天语音键毫无反应。现在的取舍是：45 秒换一次进程，换不动就退避，
  按键依然能立刻打断退避。
· 不写文件、不碰注册表、不开端口 —— 它只做一件事：把语音通到虚拟声卡。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 连续只有 4 个基础服务超过这么久 → 判"进程内快照过期"，自己退出让主进程换新进程。
# 20 秒的依据（2026-11-10 小柯改，原来是 45）：一次正常重连只要 3~10 秒；连续 20 秒
# 都是 4 个服务，已经不可能是"刚好赶上设备半睡"。
#
# ★ 原来写成 45 秒是**永远到不了**的：日志里"只看到 4 个基础服务"和"重新发现后枚举到
#   4 个服务"每 5 秒就各来一条，`four_at` 被每条都刷新一次 → 计时窗口无限后延。
#   真机 09:20~09:22 两分半钟 30+ 条 4 服务日志，worker 一次都没回收，用户看到的就是
#   "语音键连不上了"。现在改成两条判据，任一满足就退：
#     · 从**本轮第一次**见到 4 服务起算，超过 FOUR_IDLE_LIMIT 秒（只记第一次，不刷新）
#     · 或者累计 FOUR_HITS_LIMIT 次（条数够了就说明不是偶发）
FOUR_IDLE_LIMIT = 20.0
FOUR_HITS_LIMIT = 3

# ★ 2026-11-10 小柯：「连不上」也要换进程。
#   真机数据（10:37:28 启动的那个实例，11:00~11:50）：
#     掉线 4 次（10:59:43 / 11:14:36 / 11:27:25 / 11:37:36），之后"连不上"的爆发分别
#     持续 281s / 157s / 4s / 534s，中间每 ~17 秒一轮
#     「连接超时（3s）→ 扫一次 → 没扫到广播 → 连接超时（10s）」。
#   两次长爆发都是**换了 worker 进程之后 8~9 秒内就绪**（11:04:34→11:04:36、
#   11:17:25→11:17:27）；11:46:44 那次没换进程，纯靠重试在 534 秒后才自己连上。
#   也就是说：一直不换进程 = 用户按半天语音键都没反应。45 秒的依据是一次正常重连
#   只要 3~10 秒，连着 45 秒连不上，这个进程的 WinRT 快照基本就是坏的。
#   退出码 4 专门表示这一类（见 main() 结尾），主进程据此走"换进程阶梯"。
CONNECT_FAIL_LIMIT = 45.0

_out_lock = threading.Lock()
stop_flag = threading.Event()


def judge(now, connected, four_at, four_hits, down_since,
          four_idle=FOUR_IDLE_LIMIT, four_hits_limit=FOUR_HITS_LIMIT,
          connect_fail=CONNECT_FAIL_LIMIT):
    """worker 该不该退出、退出码是多少？返回 (why, code)，why 为空 = 继续干。

    纯函数（不碰时间、不碰全局），单测直接喂数就行 —— 这段判据是"换进程"的全部
    依据，改错一个数字就会变成"重启风暴"或"永远不换"，所以必须能脱离 BLE 测。

      four_at     本轮**第一次**见到"只有 4 个基础服务"的时刻（0 = 没见过）
      four_hits   本轮累计见到几次
      down_since  连续没连上的起点（0 = 一直连着）
    """
    if connected:
        return "", 0
    if four_at and (now - four_at) >= four_idle:
        return f"连续 {four_idle:.0f} 秒只有 4 个基础服务（ATVV 不在）", 3
    if four_hits >= four_hits_limit:
        return f"连续 {four_hits} 次只有 4 个基础服务（ATVV 不在）", 3
    if down_since and (now - down_since) >= connect_fail:
        return f"连续 {connect_fail:.0f} 秒连不上遥控器", 4
    return "", 0


def emit(obj):
    """给主进程发一行 JSON。父进程逐行解析，所以绝不能在 stdout 打别的东西。

    ★ ensure_ascii=True 是刻意的：Windows 下 Python 的 stdout 默认跟系统 locale 走
      （中文机器是 cp936/GBK），而主进程按 UTF-8 解码 —— 中文会变成一排菱形问号，
      界面上就成了"语音通道 就绪◆◆◆◆◆"。改用纯 ASCII 转义（\\uXXXX）输出，
      任何编码都走得通，主进程 json.loads 一读就还原成正常中文。
    """
    try:
        line = json.dumps(obj, ensure_ascii=True)
    except Exception:
        return
    with _out_lock:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:
            pass


def _read_cfg(argv):
    """配置从命令行来：--cfg <base64(json)>。避开引号/中文/空格的转义问题。"""
    for i, a in enumerate(argv):
        if a == "--cfg" and i + 1 < len(argv):
            try:
                return json.loads(base64.b64decode(argv[i + 1]).decode("utf-8"))
            except Exception as e:
                emit({"t": "log", "m": f"语音worker：配置解析失败 {type(e).__name__}: {e}"})
                return {}
    return {}


def main():
    # ★ 管道两端都锁死 UTF-8。Windows 下 Python 的 stdin/stdout 默认跟系统 locale
    #   （中文机器是 cp936），只要有一头用了别的编码，中文就会在管道里被破坏。
    #   配合 emit() 的 ensure_ascii=True，双保险。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cfg = _read_cfg(sys.argv)
    try:
        import i18n
        i18n.load_from_config()
    except Exception:
        pass

    try:
        import voice
    except Exception as e:
        emit({"t": "fatal", "m": f"语音worker：import voice 失败 {type(e).__name__}: {e}"})
        return 2

    # 只认"连上了、但服务表不全"这一类日志 —— 它是快照过期的特征。
    # 注意：连不上/扫描不到**不**计入，那是设备真睡，换进程没用。
    #
    # ★ four_at 只记"本轮第一次"（见文件头注释：原来被每条日志刷新 → 窗口永远走不到）；
    #   four_hits 记本轮累计次数，两个判据任一满足就退出换进程。
    #   why_box 用来把"为什么退出"从 stdin 线程传给主循环（poke 触发的提前退出）。
    four_at = [0.0]
    four_hits = [0]
    why_box = [""]

    def _mark_four():
        if not four_at[0]:
            four_at[0] = time.time()
        four_hits[0] += 1

    def on_event(kind, payload=None):
        if kind == "log":
            m = str(payload or "")
            if "缺少 ATVV 特征" in m or "只看到 4 个基础服务" in m:
                _mark_four()
            emit({"t": "log", "m": m})
        elif kind == "key":
            emit({"t": "key", "p": payload or {}})
        elif kind == "status":
            p = payload or {}
            # 真正连上就把"4 个服务"的计时清零，避免成功之后再误判
            if p.get("connected"):
                four_at[0] = 0.0
                four_hits[0] = 0
            emit({"t": "status", "p": p})

    role = voice.VoiceRole(
        on_event,
        addr=cfg.get("addr") or "",
        audio=cfg.get("audio"),
        voice_spec=cfg.get("voice"),
        relay=bool(cfg.get("relay", True)),
    )
    role.start()
    emit({"t": "ready", "pid": os.getpid()})

    def stdin_loop():
        """主进程发来的指令。读到 EOF（父进程没了）就等于停机。"""
        try:
            for line in sys.stdin:
                cmd = (line or "").strip()
                if not cmd:
                    continue
                try:
                    obj = json.loads(cmd)
                except Exception:
                    obj = {"c": cmd}
                c = str(obj.get("c") or "")
                if c == "poke":
                    # ★ 2026-11-10 小柯：把 reason 一起传下去（watchdog = 看门狗定时催，
                    #   不是用户按键），否则 worker 里的日志会继续说"遥控器有按键活动"。
                    role.poke(str(obj.get("r") or "key"))
                    # ★ 2026-11-10 小柯：用户按键 = 遥控器现在醒着 = 抢连接的最佳时机。
                    #   如果这个进程已经连续见过 4 服务（≥2 次）却还没连上，说明它的
                    #   服务快照就是坏的 —— 留在原地再 poke 一百次也没用，只会错过窗口。
                    #   直接退出，让监督者立刻换一个新进程去抢。
                    if four_hits[0] >= 2 and not bool(getattr(role, "connected", False)):
                        why_box[0] = ("用户按键时本进程仍只有 4 个基础服务"
                                      f"（本轮第 {four_hits[0]} 次）")
                        stop_flag.set()
                        return
                elif c == "stop":
                    stop_flag.set()
                    return
                elif c == "addr":
                    role.set_addr(str(obj.get("v") or ""))
                    emit({"t": "status", "p": {"connected": bool(role.connected),
                                               "note": role.note}})
                elif c == "spec":
                    role.set_voice_spec(obj.get("v") or {})
        except Exception:
            pass
        stop_flag.set()

    t_in = threading.Thread(target=stdin_loop, daemon=True, name="worker-stdin")
    t_in.start()

    why = "parent-stop"
    code = 0
    down_since = 0.0            # 「连续没连上」的起点（连上就清零）
    while not stop_flag.is_set():
        time.sleep(0.5)
        if not role.is_alive():
            why = "role-thread-ended"
            code = 3
            break
        now = time.time()
        if bool(getattr(role, "connected", False)):
            down_since = 0.0
            continue
        if not down_since:
            down_since = now
        # ★ 注意：只有 judge 给出理由时才覆盖 why —— 否则 "parent-stop" 会被空串
        #   盖掉，退出码就从 0 变成 3（测试 test_worker_rc4 抓到的）。
        _why, _code = judge(now, False, four_at[0], four_hits[0], down_since)
        if _why:
            why, code = _why, _code
            break

    if not why_box[0]:
        why_box[0] = why
    why = why_box[0]

    emit({"t": "exit", "why": why, "pid": os.getpid()})
    try:
        role.stop()
    except Exception:
        pass
    if why == "parent-stop":
        return 0
    # 3 = 服务快照过期（4 个服务）；4 = 一直连不上 —— 两种都要换进程，只是退避不同。
    if why.startswith("连续") and "连不上遥控器" in why:
        return 4
    return code or 3


if __name__ == "__main__":
    sys.exit(main())

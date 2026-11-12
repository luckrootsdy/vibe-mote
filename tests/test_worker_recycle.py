# -*- coding: utf-8 -*-
"""语音 worker 的**回收判据**自测（纯逻辑，不碰 BLE、不启真进程）。

背景：2026-11-10 真机出现"语音键连不上，其余键正常"。独立进程同一秒能枚举到
8 个服务，而 worker 进程只有 4 个 —— 说明进程内的服务快照坏了。可 worker 一直
不回收，因为 `four_at` 被每一条 4 服务日志刷新，45 秒窗口永远走不到。

本测试用假的 VoiceRole 喂日志，验证新判据：
 1. 连续 3 条 4 服务日志 → 立刻退出（rc=3，why 说明是 4 服务）
 2. 只见过 2 条、此时用户按键 poke → 立刻退出换进程（why 说明"用户按键时"）
 3. 中途真正 connected=True → 计数清零，不该退出
 4. 时间判据：只见过 1 条，但已经过了 FOUR_IDLE_LIMIT 秒 → 退出
"""
import io
import json
import os
import queue
import sys
import threading
import time

ROOT = r"D:\01 AI\AI_RemoteMic\vibe-mote-new\vibe-mote-main"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + str(extra)) if extra else ""))
    if not ok:
        fails.append(name)


class FakeStdin:
    """可随时喂一行的 stdin；喂 None 表示 EOF。"""

    def __init__(self):
        self.q = queue.Queue()

    def __iter__(self):
        return self

    def __next__(self):
        item = self.q.get()
        if item is None:
            raise StopIteration
        return item

    def push(self, line):
        self.q.put(line)

    def close(self):
        self.q.put(None)


class FakeRole:
    """只实现 worker 用到的那几个属性。"""

    last = None

    def __init__(self, on_event, **kw):
        FakeRole.last = self
        self.on_event = on_event
        self.connected = False
        self.note = ""
        self.alive = True
        self.pokes = 0

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def poke(self, reason="key"):
        self.pokes += 1
        self.last_reason = reason

    def stop(self):
        self.alive = False

    def set_addr(self, v):
        pass

    def set_voice_spec(self, v):
        pass

    def four(self, n=1):
        for _ in range(n):
            self.on_event("log", "语音：缺少 ATVV 特征（本次枚举到 4 个服务：00001800）")

    def conn(self):
        self.connected = True
        self.on_event("status", {"connected": True, "note": "已连接"})


import voice  # noqa: E402

REAL_ROLE = voice.VoiceRole
voice.VoiceRole = FakeRole
import voice_worker  # noqa: E402


def run_worker(script, timeout=60.0):
    """跑一次 voice_worker.main()，按 script(role, stdin) 驱动，返回 (rc, out, why)。"""
    voice_worker.stop_flag.clear()
    FakeRole.last = None                 # 关键：别把上一轮的假角色当成本轮的
    stdin = FakeStdin()
    buf = io.StringIO()
    old_in, old_out, old_argv = sys.stdin, sys.stdout, sys.argv
    sys.stdin, sys.stdout, sys.argv = stdin, buf, ["voice_worker.py"]
    box = {}
    done = threading.Event()

    def target():
        try:
            box["rc"] = voice_worker.main()
        except SystemExit as e:
            box["rc"] = e.code
        except Exception as e:                      # noqa: BLE001
            box["rc"] = "EXC %s: %s" % (type(e).__name__, e)
        finally:
            done.set()

    t = threading.Thread(target=target, daemon=True)
    t.start()
    role = None
    for _ in range(300):
        if FakeRole.last is not None:
            role = FakeRole.last
            break
        time.sleep(0.02)
    if role is None:
        stdin.close()
        sys.stdin, sys.stdout, sys.argv = old_in, old_out, old_argv
        return "NO-ROLE", "", ""
    try:
        script(role, stdin)
    finally:
        done.wait(timeout)
        stdin.close()
        t.join(2.0)
        # ★ 等 worker 里那条 stdin 线程真正退出。它读到 EOF 后会 stop_flag.set()，
        #   而 stop_flag 是**模块级**的 —— 下一轮刚开始就被它置位的话，新 worker 会
        #   立刻以 parent-stop 退出（本测试第一版就是这么假失败的）。
        time.sleep(0.4)
        sys.stdin, sys.stdout, sys.argv = old_in, old_out, old_argv
    out = buf.getvalue()
    why = ""
    for line in out.splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("t") == "exit":
            why = str(o.get("why") or "")
    return box.get("rc"), out, why

# ---------- 1. 连续 3 条 4 服务日志 → 退出 ----------
def s1(role, stdin):
    role.four(3)


rc, out, why = run_worker(s1)
check("3 条 4 服务日志 → rc=3", rc == 3, rc)
check("why 指明是 4 个基础服务", "4 个基础服务" in why, why)

# ---------- 2. 只见过 2 条，poke → 立刻换进程 ----------
def s2(role, stdin):
    role.four(2)
    time.sleep(0.2)
    stdin.push('{"c": "poke"}')


rc, out, why = run_worker(s2)
check("2 条 + poke → rc=3（换进程）", rc == 3, rc)
check("why 说明是用户按键触发的", "用户按键" in why, why)

# ---------- 3. 中途连上了 → 不退出 ----------
def s3(role, stdin):
    role.four(2)
    role.conn()
    role.four(2)
    time.sleep(0.3)
    stdin.push('{"c": "stop"}')


rc, out, why = run_worker(s3)
check("连上过之后计数清零、不误退", rc == 0, rc)
check("why = parent-stop", why == "parent-stop", why)

# ---------- 4. 时间判据（单条日志 + 超时） ----------
def s4(role, stdin):
    role.four(1)


t0 = time.time()
rc, out, why = run_worker(s4, timeout=40.0)
dt = time.time() - t0
check("单条日志也会在时限后退出（时间判据）", rc == 3, rc)
check("耗时接近 FOUR_IDLE_LIMIT", dt >= voice_worker.FOUR_IDLE_LIMIT - 1.5,
      "%.1fs" % dt)

voice.VoiceRole = REAL_ROLE
print()
print("FAILED:", fails if fails else "无")
sys.exit(1 if fails else 0)

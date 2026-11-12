# -*- coding: utf-8 -*-
"""验证 VoiceWorkerRole 的监督逻辑（不碰蓝牙、不影响运行中的 App）。

验四件事：
  1) 能正常拉起 worker、收到 ready；
  2) worker 报"只有 4 个服务"退出 → **立刻**换新进程（0.5 秒）；
  3) 60 秒内再次同样退出 → 判定"不是快照问题"→ **退避**（不再连续 spawn）；
  4) poke 能写进 worker 的 stdin；stop 能把 worker 干净收掉。
"""
import os
import sys
import time

BASE = r"D:\01 AI\AI_RemoteMic\vibe-mote-new\vibe-mote-main"
sys.path.insert(0, BASE)
os.chdir(BASE)

import voice  # noqa: E402

logs = []


def on_event(kind, payload=None):
    if kind == "log":
        logs.append(str(payload))


class Fake(voice.VoiceWorkerRole):
    def _spawn_args(self):
        return [sys.executable, r"D:\TEMP\opencode\fake_worker.py"]


r = Fake(on_event, addr="11:22:33:44:55:66")
r.start()
time.sleep(1.2)
print("ready =", r.ready, " connected =", r.connected)
r.poke()
time.sleep(6.0)                 # 够看到：1 次退出 + 立刻重建 + 第 2 次退出 + 进入退避

spawns = [m for m in logs if "已启动（pid" in m]
late = [m for m in logs if "退避" in m]
fresh = [m for m in logs if "换一个新进程重置服务快照" in m]
poke_ok = [m for m in logs if "收到 poke" in m]

print("--- 监督者收到的全部日志 ---")
for m in logs:
    print("  |", m)
print("--- 关键判据 ---")
print("spawn 次数        =", len(spawns), "(期望 2：初始 + 立刻重建)")
print("立刻换进程日志     =", len(fresh), "(期望 1)")
print("退避日志           =", len(late), "(期望 1：第 2 次同因退出)")
print("poke 送达          =", len(poke_ok), "(期望 >=1)")
time.sleep(2.0)
print("2 秒后是否又 spawn =", len([m for m in logs if "已启动（pid" in m]) - len(spawns), "(期望 0，说明退避生效)")

r.stop()
time.sleep(0.5)
print("stop 后线程存活    =", r.is_alive(), "(期望 False)")

ok = (len(spawns) == 2 and len(fresh) == 1 and len(late) == 1 and len(poke_ok) >= 1
      and not r.is_alive())
print("结果：", "全部通过" if ok else "有失败，见上面各项")
sys.exit(0 if ok else 1)

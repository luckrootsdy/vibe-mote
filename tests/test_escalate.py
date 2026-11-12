# -*- coding: utf-8 -*-
"""v1.2.2 自动恢复阶梯的自测（不碰 BLE、不动运行中的客户端）。

覆盖：
  1. voice_worker.judge()      —— worker 什么时候自己退出换进程（退出码 3 / 4）
  2. app.watch_escalate()      —— 什么时候升级成"重启整个客户端"（含限流）
  3. VoiceWorkerRole           —— poke 的 reason 语义、respawns 计数、连上即清零
  4. 源码守卫                  —— 阶梯数字和退出码 4 的接线还在
"""
import ast
import io
import os
import sys

ROOT = r"D:\01 AI\AI_RemoteMic\vibe-mote-new\vibe-mote-main"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + str(extra)) if extra else ""))
    if not ok:
        fails.append(name)


# ---------- 0. ast ----------
for f in ("app.py", "voice.py", "voice_worker.py"):
    try:
        ast.parse(io.open(os.path.join(ROOT, f), encoding="utf-8").read())
        check("ast " + f, True)
    except Exception as e:
        check("ast " + f, False, e)

import app          # noqa: E402
import voice        # noqa: E402
import voice_worker  # noqa: E402

# ---------- 1. judge()：worker 该不该退出 ----------
J = voice_worker.judge
check("judge 连上就什么都不做", J(1000.0, True, 900.0, 9, 0.0) == ("", 0),
      J(1000.0, True, 900.0, 9, 0.0))
check("judge 4 服务超 20 秒 → 退出码 3",
      J(1000.0, False, 975.0, 1, 999.0)[1] == 3)
check("judge 4 服务 19 秒 → 继续",
      J(1000.0, False, 981.0, 1, 999.0) == ("", 0))
check("judge 4 服务累计 3 次 → 退出码 3（不等 20 秒）",
      J(1000.0, False, 999.5, 3, 999.0)[1] == 3)
check("judge 4 服务累计 2 次 → 继续",
      J(1000.0, False, 999.5, 2, 999.0) == ("", 0))
w, c = J(1000.0, False, 0.0, 0, 950.0)
check("judge 连不上 50 秒 → 退出码 4", c == 4 and "连不上遥控器" in w, (w, c))
check("judge 连不上 44 秒 → 继续", J(1000.0, False, 0.0, 0, 956.0) == ("", 0))
check("judge 连不上 45 秒整 → 退出码 4", J(1000.0, False, 0.0, 0, 955.0)[1] == 4)
check("judge 还没开始算（down_since=0）→ 继续",
      J(1000.0, False, 0.0, 0, 0.0) == ("", 0))
check("judge 两条都成立时优先报 4 服务（换进程的理由更准）",
      J(1000.0, False, 950.0, 3, 900.0)[1] == 3)
check("judge 常量 45 秒 / 20 秒 / 3 次没被改坏",
      (voice_worker.CONNECT_FAIL_LIMIT, voice_worker.FOUR_IDLE_LIMIT,
       voice_worker.FOUR_HITS_LIMIT) == (45.0, 20.0, 3),
      (voice_worker.CONNECT_FAIL_LIMIT, voice_worker.FOUR_IDLE_LIMIT,
       voice_worker.FOUR_HITS_LIMIT))

# ---------- 2. watch_escalate()：什么时候重启整个客户端 ----------
E = app.watch_escalate
check("escalate 基线（181s / 换过 2 次 / 没重启过 / 很久没试）", E(181, 2, 0, 1e9) is True)
check("escalate 未连上 179 秒 → 不动", E(179, 2, 0, 1e9) is False)
check("escalate 只换过 1 次进程 → 不动（换进程还没用尽）", E(500, 1, 0, 1e9) is False)
check("escalate 已重启 3 次 → 不再重启（限次）", E(500, 5, 3, 1e9) is False)
check("escalate 距上次重启 300 秒 → 不动（限流 10 分钟）", E(500, 5, 1, 300) is False)
check("escalate 距上次重启正好 600 秒 → 可以", E(500, 5, 1, 600) is True)
check("escalate 连上过（down=0）→ 不动", E(0, 5, 0, 1e9) is False)
check("escalate 脏参数不炸", E(None, None, None, None) is False)
check("escalate 阈值就是 180/600/3/2",
      (app.ESC_AFTER, app.ESC_GAP, app.ESC_MAX, app.ESC_MIN_RESPAWNS)
      == (180.0, 600.0, 3, 2),
      (app.ESC_AFTER, app.ESC_GAP, app.ESC_MAX, app.ESC_MIN_RESPAWNS))

# ---------- 2b. 自动重启计数要跨进程（否则每 10 分钟重启一次客户端） ----------
_tmp = os.path.join(r"D:\TEMP\opencode", "_auto_recovery_test.json")
app.RECOVERY_STATE = _tmp
app.clear_recovery_state()
check("没有文件时读出来是 (0, 0.0)", app.read_recovery_state() == (0, 0.0),
      app.read_recovery_state())
app.write_recovery_state(2, 1794280000.5)
check("写进去再读回来一致", app.read_recovery_state() == (2, 1794280000.5),
      app.read_recovery_state())
check("计数 2 时仍然允许第 3 次（上限 3）",
      app.watch_escalate(300, 9, *app.read_recovery_state()[:1], 1e9) is True)
app.write_recovery_state(3, 1794280000.5)
cnt, _last = app.read_recovery_state()
check("计数 3（已到上限）→ 新进程也不会再重启",
      app.watch_escalate(300, 9, cnt, 1e9) is False)
app.clear_recovery_state()
check("连上后清空 → 读出来又是 (0, 0.0)", app.read_recovery_state() == (0, 0.0),
      app.read_recovery_state())
app.RECOVERY_STATE = os.path.join(ROOT, "_auto_recovery.json")

# ---------- 3. VoiceWorkerRole：poke 语义 / respawns / 连上清零 ----------
logs = []
role = voice.VoiceWorkerRole(lambda k, p=None: logs.append((k, p)))
check("新 worker 的 respawns = 0", role.respawns == 0)
check("看门狗 poke 不置位退避打断事件",
      (role.poke("watchdog") or role._poke_evt.is_set()) is False)
role._poke_evt.clear()
role.poke("key")
check("真按键 poke 会置位退避打断事件", role._poke_evt.is_set() is True)
check("poke 的 reason 被记住", role._poke_reason == "key")

role._cf_level = 3
role._handle({"t": "status", "p": {"connected": True}})
check("连上 → 连不上阶梯清零", role._cf_level == 0)
check("连上 → connected 状态同步", role.connected is True)
role._handle({"t": "status", "p": {"connected": False, "note": "x"}})
check("断开不会误清零阶梯（保持 0，等下次判据累加）", role._cf_level == 0)

# 退避阶梯本身（读源码，防止有人把 2/15/30/60/120 改回 60/120/300）
src = io.open(os.path.join(ROOT, "voice.py"), encoding="utf-8").read()
check("voice.py 里有连不上换进程的退避阶梯 [2,15,30,60,120]",
      "[2.0, 15.0, 30.0, 60.0, 120.0]" in src)
check("voice.py 认退出码 4 为「连不上」", "rc == 4" in src)
check("worker 的退出码 4 接线还在",
      "连不上遥控器" in io.open(os.path.join(ROOT, "voice_worker.py"),
                                 encoding="utf-8").read())
check("app.py 自动重启走 launch_new_instance（与手动重启同一条路）",
      "ok, msg = launch_new_instance()" in io.open(
          os.path.join(ROOT, "app.py"), encoding="utf-8").read())

print()
if fails:
    print("FAILED", len(fails), fails)
    sys.exit(1)
print("ALL PASS")

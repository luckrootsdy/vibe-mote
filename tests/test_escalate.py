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
# ★ 2026-11-17：ATVV 被 Windows 拒绝（ACCESS_DENIED）→ 换进程（退出码 3）
check("judge 被拒 2 轮 → 退出码 3（不等 45 秒）",
      J(1000.0, False, 0.0, 0, 999.0, denied_at=998.0, denied_hits=2)[1] == 3)
check("judge 被拒 1 轮 → 继续（偶发不换进程）",
      J(1000.0, False, 0.0, 0, 999.0, denied_at=998.0, denied_hits=1) == ("", 0))
check("judge 被拒 46 秒 → 退出码 3",
      J(1000.0, False, 0.0, 0, 999.0, denied_at=954.0, denied_hits=1)[1] == 3)
check("judge 被拒 44 秒 → 继续",
      J(1000.0, False, 0.0, 0, 999.0, denied_at=956.0, denied_hits=1) == ("", 0))
check("judge 被拒的理由里写明 ACCESS_DENIED",
      "ACCESS_DENIED" in J(1000.0, False, 0.0, 0, 999.0, denied_at=900.0,
                           denied_hits=2)[0])
check("judge 被拒优先于「4 个服务」",
      J(1000.0, False, 950.0, 3, 900.0, denied_at=900.0, denied_hits=2)[0].find(
          "ACCESS_DENIED") >= 0)
check("judge 连上时被拒计数不参与",
      J(1000.0, True, 900.0, 9, 0.0, denied_at=900.0, denied_hits=9) == ("", 0))
check("judge 被拒常量 45 秒 / 2 轮没被改坏",
      (voice_worker.DENIED_IDLE_LIMIT, voice_worker.DENIED_HITS_LIMIT) == (45.0, 2),
      (voice_worker.DENIED_IDLE_LIMIT, voice_worker.DENIED_HITS_LIMIT))

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
# ★ 2026-11-17：ACCESS_DENIED 单独一条阶梯 —— 主进程内跑语音时 respawns 恒为 0，
#   老判据永远不成立（真机 10:12~10:18 就是这样卡住，只能手动重启客户端）。
check("escalate 被拒 2 轮 + 断开 60 秒 + respawns=0 → 重启（这是本次修复的核心）",
      E(60, 0, 0, 1e9, denied=2) is True)
check("escalate 被拒 2 轮但只断开 59 秒 → 再等等", E(59, 0, 0, 1e9, denied=2) is False)
check("escalate 只被拒 1 轮 → 不动（偶发）", E(500, 0, 0, 1e9, denied=1) is False)
check("escalate 被拒也要受限次：已重启 3 次 → 不动",
      E(500, 0, 3, 1e9, denied=9) is False)
check("escalate 被拒也要受限流：距上次 300 秒 → 不动",
      E(500, 0, 1, 300, denied=9) is False)
check("escalate 没被拒时仍要求换过 2 次进程（不误伤设备睡着）",
      E(500, 0, 0, 1e9, denied=0) is False)
check("escalate 被拒阈值就是 60 秒 / 2 轮",
      (app.ESC_DENY_AFTER, app.ESC_DENY_HITS) == (60.0, 2),
      (app.ESC_DENY_AFTER, app.ESC_DENY_HITS))

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

# ★ 2026-11-17：worker 模式下的 denied 计数（主进程从转发日志里数）
check("新 worker 的 denied = 0", role.denied == 0)
role._handle({"t": "log", "m": "语音：Windows 说这台遥控器有 8 个服务，"
                                "但 ATVV(ab5e0001) 被拒绝访问（ACCESS_DENIED）"})
check("日志里出现 ACCESS_DENIED → denied 累加", role.denied == 1, role.denied)
role._handle({"t": "log", "m": "语音：ATVV 一直被系统拒绝（第 2 轮）"})
check("「被系统拒绝」也计入 denied", role.denied == 2, role.denied)
role._handle({"t": "status", "p": {"connected": True}})
check("连上 → denied 清零", role.denied == 0, role.denied)

# VoiceRole（默认的进程内模式）也要有同名接口，否则看门狗读不到
_vr = voice.VoiceRole(lambda k, p=None: None, addr="")
check("VoiceRole.denied 初值 0", _vr.denied == 0)
_vr._denied = 3
check("VoiceRole.denied 读的是 _denied", _vr.denied == 3)

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

# ---------- 4. 源码守卫：ACCESS_DENIED 自救链真的接上了 ----------
_app = io.open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
check("watch_voice 把 denied 传给 watch_escalate", "denied=denied" in _app)
check("watch_voice 读的是角色的 denied", 'getattr(v, "denied"' in _app)
check("被拒时的日志说清「换进程释放独占占用」", "独占占用" in _app)
check("voice.py 的 ACCESS_DENIED 提示：软件自动重启 + 取电池只当最后手段",
      "看门狗会自动重启一次客户端" in src and "取电池只当最后手段" in src)
check("voice.py 不再说「这种状态软件侧救不回来」",
      "软件侧救不回来" not in src)
check("voice_worker.py 的 denied 判据接线还在",
      "denied_at=denied_at[0]" in io.open(
          os.path.join(ROOT, "voice_worker.py"), encoding="utf-8").read())
check("自动重启到上限后要明说「停止自动重启」", "停止自动重启" in _app)
# ★ 2026-11-17：探针门槛必须去掉 —— 真机见过「枚举到 6 个服务但 ATVV 不在」，
#   带 `len(uuids) <= 4` 时探针不跑、denied 永远是 0，会被误判成"半睡"。
check("ATVV 不在时探针无条件跑（没有 len(uuids)<=4 门槛）",
      "len(uuids) <= 4 and" not in src)
check("提示不再写死「4 个基础服务」", "ATVV 不在（本次只枚举到" in src)
# ★ 2026-11-17：计划内重启（restart-app / 自动恢复 / 提权重启）走不到 main 末尾的
#   "删 RUNSTATE"，新实例会把这次**正常**重启误报成「上次没有正常退出，可能崩溃/
#   被强杀」。判据用 `--takeover`（只有本软件的重启会带它，快捷方式启动不带）。
check("计划内重启不再误报「上次没有正常退出」",
      'restarted = bool(getattr(args, "takeover", False))' in _app)

print()
if fails:
    print("FAILED", len(fails), fails)
    sys.exit(1)
print("ALL PASS")

# -*- coding: utf-8 -*-
"""电量读数自测（2026-11-10 小柯）：不碰真设备，全用假 client/chars 驱动。

覆盖：
 1. 正常读到 78% → self.battery=78，日志是人话
 2. 0xFF（未知）→ 不当成 255% 存进去
 3. 读异常 → 不抛、不覆盖旧值、只提示一次
 4. 20 秒节流（force 也不能每 3 秒读一次）
 5. 600 秒刷新节流
 6. 低电量文案（<=10 / <=20 / 正常）三档
 7. VoiceWorkerRole 能从 status 事件接住 battery，且拒绝非法值
"""
import asyncio
import os
import sys

BASE = r"D:\01 AI\AI_RemoteMic\vibe-mote-new\vibe-mote-main"
sys.path.insert(0, BASE)

import voice  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"PASS {name}")
    else:
        fail += 1
        print(f"FAIL {name} {extra}")


class FakeChar:
    def __init__(self, uuid):
        self.uuid = uuid


class FakeClient:
    def __init__(self, payload=b"\x4e", err=None):
        self.payload = payload
        self.err = err
        self.reads = 0

    async def read_gatt_char(self, ch):
        self.reads += 1
        if self.err:
            raise self.err
        return self.payload


CHARS = [FakeChar("00002a19-0000-1000-8000-00805f9b34fb"),
         FakeChar("00002a00-0000-1000-8000-00805f9b34fb")]


def new_role():
    logs = []
    r = voice.VoiceRole(lambda k, p=None: logs.append((k, p)), addr="00:00:00:00:00:00")
    return r, logs


async def main():
    # 1) 正常
    r, logs = new_role()
    c = FakeClient(b"\x4e")
    got = await r._read_battery(c, CHARS, force=True)
    check("读到 78%", got == 78 and r.battery == 78, f"got={got} bat={r.battery}")
    msgs = [p for k, p in logs if k == "log"]
    check("日志含 78%", any("78%" in m for m in msgs), str(msgs))
    check("正常档不提换电池", all("换碱性" not in m for m in msgs), str(msgs))

    # 2) 0xFF
    r, logs = new_role()
    got = await r._read_battery(FakeClient(b"\xff"), CHARS, force=True)
    check("0xFF 当未知", got is None and r.battery is None, f"got={got}")

    # 3) 异常
    r, logs = new_role()
    got = await r._read_battery(FakeClient(err=RuntimeError("boom")), CHARS, force=True)
    check("异常返回 None 不抛", got is None)
    check("异常提示一次", sum("读电量失败" in m for m in
                              [p for k, p in logs if k == "log"]) == 1)
    # 再失败一次不该再刷
    r._battery_at = 0.0
    await r._read_battery(FakeClient(err=RuntimeError("boom")), CHARS, force=True)
    check("异常只提示一次", sum("读电量失败" in m for m in
                               [p for k, p in logs if k == "log"]) == 1)

    # 4) 20 秒节流
    r, logs = new_role()
    c = FakeClient(b"\x50")
    await r._read_battery(c, CHARS, force=True)
    await r._read_battery(c, CHARS, force=True)
    check("20 秒内不重复读", c.reads == 1, f"reads={c.reads}")

    # 5) 600 秒刷新节流（非 force）
    r, logs = new_role()
    c = FakeClient(b"\x50")
    await r._read_battery(c, CHARS, force=True)   # 第 1 次
    r._battery_at = voice.time.time() - 30.0      # 过了 20 秒，但没过 600
    await r._read_battery(c, CHARS)
    check("600 秒内非 force 不读", c.reads == 1, f"reads={c.reads}")
    r._battery_at = voice.time.time() - 601.0
    await r._read_battery(c, CHARS)
    check("超过 600 秒会刷新", c.reads == 2, f"reads={c.reads}")

    # 6) 文案三档
    low = voice.VoiceRole._battery_line(7)
    mid = voice.VoiceRole._battery_line(18)
    hi = voice.VoiceRole._battery_line(88)
    check("7% 文案警告换电池", "换电池" in low, low)
    check("18% 文案提镍氢", "镍氢" in mid, mid)
    check("88% 文案普通", "88%" in hi and "换" not in hi, hi)
    drop = voice.VoiceRole._battery_line(15, 60)
    check("掉电超过 5 点会提示", "上次 60%" in drop, drop)

    # 7) worker 接住 battery
    w = voice.VoiceWorkerRole(lambda k, p=None: None, addr="00:00:00:00:00:00")
    w._handle({"t": "status", "p": {"connected": True, "note": "已连接", "battery": 43}})
    check("worker 接住 43%", w.battery == 43, str(w.battery))
    w._handle({"t": "status", "p": {"connected": True, "battery": 255}})
    check("worker 拒绝 255", w.battery == 43, str(w.battery))
    w._handle({"t": "status", "p": {"connected": True, "battery": None}})
    check("worker 忽略 None", w.battery == 43, str(w.battery))


asyncio.run(main())
print(f"\n{ok} PASS / {fail} FAIL")
sys.exit(0 if fail == 0 else 1)

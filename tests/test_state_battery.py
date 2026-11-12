# -*- coding: utf-8 -*-
"""确认 state_snapshot 把电量带出去了（含 worker/进程内两种角色形状）。"""
import sys

BASE = r"D:\01 AI\AI_RemoteMic\vibe-mote-new\vibe-mote-main"
sys.path.insert(0, BASE)

import app  # noqa: E402


class FakeVoiceKey:
    ok = True


class FakeVoice:
    connected = True
    note = "已连接"
    battery = 37
    voice = FakeVoiceKey()
    ready = True


class FakeRoles:
    voice = FakeVoice()
    keytap = None
    running = True


cfg = {"remote_addr": "42:3F:6A:5D:88:16",
       "voice": {"mode": "hold", "key": "LWIN", "mods": ["CTRL", "LWIN"]},
       "keys": {}}
s = app.state_snapshot(cfg, FakeRoles())
print("battery =", s["voice"].get("battery"))
assert s["voice"]["battery"] == 37, s["voice"]

# 角色没有 battery 属性（老对象/测试桩）时不能抛
class NoBatt:
    connected = False
    note = "未启动"
    voice = FakeVoiceKey()
    ready = False


class R2:
    voice = NoBatt()
    keytap = None
    running = True


s2 = app.state_snapshot(cfg, R2())
print("battery(missing) =", s2["voice"].get("battery"))
assert s2["voice"]["battery"] is None
print("PASS state_snapshot 带出电量，且缺属性不抛")

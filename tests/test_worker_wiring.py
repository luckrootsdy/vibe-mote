# -*- coding: utf-8 -*-
"""worker 接线的静态自测（不碰 BLE、不动运行中的客户端）。

覆盖：
 1. 三个文件 ast 通过
 2. save_config 白名单保住 voice.worker（含 keys.voice 别名那条分支）
 3. our_instances() 不会把 voice_worker.py 当成本目录的 app 实例（接管模式别误杀）
 4. make_voice() 按开关返回 Worker / 进程内两种角色
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


# ---------- 1. ast ----------
for f in ("app.py", "voice.py", "voice_worker.py"):
    try:
        ast.parse(io.open(os.path.join(ROOT, f), encoding="utf-8").read())
        check("ast " + f, True)
    except Exception as e:
        check("ast " + f, False, e)

import app  # noqa: E402

# 测试前用户的真实设置 —— 结束时按它还原（worker 是生产开关，不能被测试改写）
before_worker = bool((app.load_config().get("voice") or {}).get("worker"))

# ---------- 2. save_config 白名单 ----------
cfg = app.load_config()
cfg["voice"]["worker"] = True
app.save_config(cfg)
back = app.load_config()
check("save_config 保住 voice.worker=True", bool(back["voice"].get("worker")) is True,
      back["voice"].get("worker"))

# keys.voice 别名分支：它会把 clean["voice"] 整个替换掉
cfg2 = app.load_config()
cfg2["voice"]["worker"] = True
cfg2.setdefault("keys", {})["voice"] = {"type": "hold", "value": "LWIN",
                                        "mods": ["CTRL", "LWIN"]}
app.save_config(cfg2)
back2 = app.load_config()
check("keys.voice 别名分支也保住 worker", bool(back2["voice"].get("worker")) is True,
      back2["voice"].get("worker"))
# 还原（别把测试写进真配置）—— 还原成**测试前**的值，而不是硬编码 False
# （worker 现在是生产开关，写成 False 等于把用户的自动恢复关掉）。
cfg3 = app.load_config()
cfg3["voice"]["worker"] = before_worker
cfg3.get("keys", {}).pop("voice", None)
app.save_config(cfg3)
check("还原后 voice.worker 回到测试前的值", app.load_config()["voice"].get("worker") is before_worker,
      before_worker)

# ---------- 3. our_instances 别把 worker 当 app 实例 ----------
procs = [
    (100, 1, r'"C:\Python312\python.exe" "%s\app.py" --silent --port 8787' % ROOT),
    (101, 100, r'"C:\Python312\python.exe" "%s\voice_worker.py" --cfg eyJhIjoxfQ==' % ROOT),
    (102, 1, r'"C:\Python312\python.exe" "%s\app.py" --silent' % r"D:\other\vibe-mote\vibe-mote-main" % ()),
]
ours = app.our_instances(procs)
check("our_instances 只认 app.py（101 worker 不在里面）", 101 not in ours and 100 in ours, ours)
check("our_instances 不碰别的目录", 102 not in ours, ours)

# ---------- 4. make_voice 开关 ----------
cfg_w = dict(app.load_config())
cfg_w["voice"] = dict(cfg_w.get("voice") or {}, worker=True)
cfg_n = dict(app.load_config())
cfg_n["voice"] = dict(cfg_n.get("voice") or {}, worker=False)
import voice as voice_mod  # noqa: E402

w = app.make_voice(cfg_w, lambda *a: None)
n = app.make_voice(cfg_n, lambda *a: None)
check("worker=true -> VoiceWorkerRole", isinstance(w, voice_mod.VoiceWorkerRole), type(w).__name__)
check("worker=false -> VoiceRole", isinstance(n, voice_mod.VoiceRole)
      and not isinstance(n, voice_mod.VoiceWorkerRole), type(n).__name__)
check("Worker 有 voice/ready（state_snapshot 要读）",
      hasattr(w, "voice") and hasattr(w.voice, "ok") and hasattr(w, "ready"))

# ---------- 5. poke 能打断退避（纯对象层，不启动进程） ----------
w._poke_evt.clear()
w.poke()
check("poke() 置位打断事件", w._poke_evt.is_set())

print()
print("FAILED:", fails if fails else "无")
sys.exit(1 if fails else 0)

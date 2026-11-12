# -*- coding: utf-8 -*-
"""真跑一遍 voice_worker.main()：连不上 → 退出码 4（换进程）。

做法：把 voice.VoiceRole 换成一个"永远连不上"的假角色，并把 judge() 的 45 秒
门槛临时改成 1.5 秒（否则这个测试要等 45 秒）。跑的是**真的** main()：
真的 stdout 协议、真的 stdin 线程、真的退出码。
"""
import base64
import io
import json
import os
import sys
import time

ROOT = r"D:\01 AI\AI_RemoteMic\vibe-mote-new\vibe-mote-main"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import voice        # noqa: E402
import voice_worker  # noqa: E402


class FakeRole:
    """永远连不上、但线程还活着 —— 就是"进程里 WinRT 快照坏了"的样子。"""

    made = []

    def __init__(self, *a, **kw):
        self.connected = False
        self.note = "fake: never connects"
        self.sessions = 0
        self.frames = 0
        self.stopped = False
        FakeRole.made.append(self)

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def poke(self, reason="key"):
        self.last_reason = reason

    def set_addr(self, addr):
        pass

    def set_voice_spec(self, spec):
        pass

    def is_alive(self):
        return True


voice.VoiceRole = FakeRole
# judge() 的默认参数在 def 时就绑定了 45 秒 —— 这里包一层换成 1.5 秒。
_judge = voice_worker.judge
voice_worker.judge = lambda now, c, fa, fh, ds: _judge(now, c, fa, fh, ds, connect_fail=1.5)

blob = base64.b64encode(json.dumps(
    {"addr": "00:00:00:00:00:00", "audio": {}, "voice": {}, "relay": False}
).encode("utf-8")).decode("ascii")

sys.argv = ["voice_worker.py", "--cfg", blob]


class BlockingStdin:
    """假装父进程一直在（不 EOF）—— 否则 stdin_loop 会立刻 stop_flag，
    测到的就是 parent-stop 而不是"连不上 45 秒"。"""

    def __iter__(self):
        while True:
            time.sleep(3600)
            yield ""


sys.stdin = BlockingStdin()

# 把 stdout 收起来，好检查 exit 事件（emit 用的是 sys.stdout）
buf = io.StringIO()
real_stdout = sys.stdout
sys.stdout = buf
try:
    rc = voice_worker.main()
finally:
    sys.stdout = real_stdout

out = buf.getvalue()
events = []
for line in out.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        events.append(json.loads(line))
    except Exception:
        pass

kinds = [e.get("t") for e in events]
exit_ev = [e for e in events if e.get("t") == "exit"]

fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + str(extra)) if extra else ""))
    if not ok:
        fails.append(name)


check("main() 连不上就退出，退出码 = 4", rc == 4, rc)
check("退出事件里有 why 且说清是「连不上遥控器」",
      bool(exit_ev) and "连不上遥控器" in (exit_ev[0].get("why") or ""),
      exit_ev[0].get("why") if exit_ev else None)
check("stdout 里每行都是合法 JSON（协议没被日志污染）", len(kinds) >= 2, kinds)
check("先发 ready 再发 exit", kinds[0] == "ready" and kinds[-1] == "exit", kinds)
check("退出前把角色停掉了（不硬杀，遥控器才认得出我们走了）",
      bool(FakeRole.made) and FakeRole.made[-1].stopped)


# ---------- 第二个场景：父进程喊 stop → 退出码必须是 0（不是 3） ----------
class OneStopStdin:
    """第一行给 stop，之后一直不 EOF。"""

    def __iter__(self):
        yield '{"c": "stop"}\n'
        while True:
            time.sleep(3600)
            yield ""


voice_worker.stop_flag.clear()
FakeRole.made.clear()
sys.stdin = OneStopStdin()
buf2 = io.StringIO()
sys.stdout = buf2
try:
    rc2 = voice_worker.main()
finally:
    sys.stdout = real_stdout

ev2 = []
for line in buf2.getvalue().splitlines():
    try:
        ev2.append(json.loads(line))
    except Exception:
        pass
exit2 = [e for e in ev2 if e.get("t") == "exit"]
check("父进程 stop → 退出码 0（不能被空 why 盖成 3）", rc2 == 0, rc2)
check("父进程 stop 的 why 就是 parent-stop",
      bool(exit2) and exit2[0].get("why") == "parent-stop",
      exit2[0].get("why") if exit2 else None)

print()
print("FAILED:", fails if fails else "无")
sys.exit(1 if fails else 0)

# -*- coding: utf-8 -*-
"""只读探针：验证 app.our_instances() 能不能认出"stub 的子进程"。"""
import io
import os
import sys

BASE = r"D:\01 AI\AI_RemoteMic\vibe-mote-new\vibe-mote-main"
sys.path.insert(0, BASE)
os.chdir(BASE)

import app  # noqa: E402

procs = app.python_processes()
print("python processes:")
for pid, ppid, cmd in procs:
    print(f"  pid={pid:<7} ppid={ppid:<7} {cmd[:110]}")

print()
print("APPPID file:", app.APPPID, "exists=", os.path.exists(app.APPPID))
try:
    print("  content:", io.open(app.APPPID, encoding="utf-8").read().strip())
except OSError as e:
    print("  (none)", e)

print()
print("our_instances() =", app.our_instances(procs))
print("me =", os.getpid())

# -*- coding: utf-8 -*-
"""确认 pythonw.exe 在 PIPE 下有没有可用的 stdout（worker 要靠它发 JSON）。"""
import subprocess, sys, os

CODE = ("import sys\n"
        "sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
        "sys.stdout.write('hi 中文\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('errline\\n')\n")

for exe in (r"C:\Python312\pythonw.exe", sys.executable):
    print("=== ", exe)
    try:
        p = subprocess.Popen([exe, "-c", CODE], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, stdin=subprocess.PIPE,
                             creationflags=0x08000000)
        o, e = p.communicate(timeout=30)
        print("rc", p.returncode, "out", o, "err", e)
    except Exception as ex:
        print("FAIL", type(ex).__name__, ex)
print("sys.executable =", sys.executable)

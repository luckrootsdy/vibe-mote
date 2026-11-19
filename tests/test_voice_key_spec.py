# -*- coding: utf-8 -*-
"""语音键规格回归：微信输入法的 Ctrl+Win 不能被写成「只按 Win」。"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import voice  # noqa: E402

fails = []


def check(name, cond, got=None):
    if cond:
        print("PASS", name)
    else:
        print("FAIL", name, "->", repr(got))
        fails.append(name)


spec = {"mode": "hold", "key": "LWIN", "mods": ["CTRL", "LWIN"]}
mode, mods, key = voice.spec_to_vk(spec)
check("微信输入法预设解析出 hold 模式", mode == "hold", mode)
check("主键是 Win", key == voice.keys.vk_of("LWIN"), key)
# 修饰键里和主键相同的 LWIN 必须排掉，press() 只按一次 Win
check("Ctrl+Win 不重复按 Win（mods 只剩 CTRL）",
      mods == [voice.keys.vk_of("CTRL")], mods)

vk = voice.VoiceKey(spec)
check("日志标签写成 CTRL+LWIN，不再写 CTRL+LWIN+LWIN",
      vk.label == "CTRL+LWIN", vk.label)

cfg = json.load(io.open(os.path.join(ROOT, "config.json"), encoding="utf-8-sig"))
check("仓库 config.json 是合法 JSON，且 mods 没丢",
      cfg.get("voice", {}).get("mods") == ["CTRL", "LWIN"],
      cfg.get("voice", {}).get("mods"))

html = io.open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
check("按键面板对「按住」也显示修饰键（不再只显示 Win）",
      "case 'hold':\n        list = mods.concat(m.value ? [v] : []);" in html)

print()
if fails:
    print("FAILED", len(fails), fails)
    sys.exit(1)
print("ALL PASS")

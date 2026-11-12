# -*- coding: utf-8 -*-
"""验证 _uncached_at 时间窗语义（不碰正在跑的客户端，只跑同样的表达式）。"""
import time


class T:
    def __init__(self):
        self._uncached_at = 0.0

    def should_rediscover(self, uuids, has_atvv, now=None):
        now = time.time() if now is None else now
        _unc_at = float(getattr(self, "_uncached_at", 0.0))
        fire = (not has_atvv) and len(uuids) <= 4 and (now - _unc_at) > 30.0
        if fire:
            self._uncached_at = now
        return fire

    def poke(self):
        self._uncached_at = 0.0


t = T()
base = time.time()
seq = [
    (0,   "首次遇到 4 个服务",        ["1800", "1801", "180a", "180f"], False, True),
    (5,   "5 秒后再遇到",            ["1800", "1801", "180a", "180f"], False, False),
    (29,  "29 秒后",                ["1800", "1801", "180a", "180f"], False, False),
    (31,  "31 秒后（时间窗过了）",     ["1800", "1801", "180a", "180f"], False, True),
    (32,  "按键 poke 之后立刻",       ["1800", "1801", "180a", "180f"], False, None),
    (33,  "poke 后同秒再一轮",        ["1800", "1801", "180a", "180f"], False, False),
    (40,  "已经 8 个服务",            ["1800", "1801", "180a", "180f", "ae40", "ab5e0001"], True, False),
]
ok = True
for dt, name, uu, has, expect in seq:
    now = base + dt
    if expect is None:
        t.poke()
        got = t.should_rediscover(uu, has, now)
        expect = True
    else:
        got = t.should_rediscover(uu, has, now)
    mark = "OK " if got == expect else "FAIL"
    if got != expect:
        ok = False
    print(f"{mark} t={dt:>3}s {name:<18} rediscover={got} (期望 {expect})")

print("\n全部通过" if ok else "\n有失败")

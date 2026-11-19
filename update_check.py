# -*- coding: utf-8 -*-
"""在线版本检查（VibeMote 自动升级提示）—— 纯标准库，失败静默，绝不阻挡启动。

设计要点（都是为了"用户无感、永不影响正常使用"）：
  · 只查 GitHub Releases 的 latest（稳定版），API 自带跳过 draft/prerelease。
  · 结果缓存到 _update_state.json：界面先显示上次的缓存，真正的联网在后台线程做。
  · 用户点"忽略此版本"也存这里 —— **不写 config.json**，否则界面点一次"保存设置"
    就会把它抹掉（config.json 的 save_config 只保留白名单字段）。
  · 联网失败（无网 / 代理没开 / GitHub 连不上 / 达到 API 限流）一律静默返回
    ok=False，界面据此不弹任何提示 —— 绝不能因为检查更新把用户折腾一番。

仓库地址：https://github.com/luckrootsdy/vibe-mote
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "_update_state.json")

REPO = "luckrootsdy/vibe-mote"
API_LATEST = "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASES_PAGE = "https://github.com/%s/releases" % REPO
RELEASES_LATEST_PAGE = "https://github.com/%s/releases/latest" % REPO
TIMEOUT = 6           # 单个通道超时（秒）—— 后台线程里，6 秒足够也不算久
TTL = 6 * 3600        # 缓存有效期（秒）；进程启动时仍强制联网刷新一次
UA = "VibeMote-UpdateCheck/1.0"

# ★ 2026-11-19：多通道。国内直连 GitHub 常常不通，任一通道成功即可；全失败才静默。
#   顺序：官方 API（直连/系统代理）→ 官方 releases/latest 页 HTML 兜底 → 公开镜像 API。
#   镜像地址是公开代理服务，可能失效 —— 所以只当作"尽力而为"的兜底，
#   每个通道独立超时，任何一个坏掉都不会拖垮整轮检查。
API_PATH = "/repos/%s/releases/latest" % REPO
CHANNELS = [
    ("api", "https://api.github.com" + API_PATH, "json"),
    ("page", RELEASES_LATEST_PAGE, "html"),
    ("mirror-ghproxy", "https://ghproxy.net/https://api.github.com" + API_PATH, "json"),
    ("mirror-ghproxy2", "https://ghproxy.io/https://api.github.com" + API_PATH, "json"),
    ("mirror-fastgit", "https://hub.fastgit.xyz/https://api.github.com" + API_PATH, "json"),
]


def _ver_tuple(s):
    """把 'v1.3.5' / '1.3.5' / '1.3' 变成定长元组；解析不出来返回 None。"""
    m = re.match(r"^v?(\d+(?:\.\d+){0,3})", str(s or "").strip(), re.I)
    if not m:
        return None
    parts = tuple(int(x) for x in m.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def is_newer(latest, current):
    a, b = _ver_tuple(latest), _ver_tuple(current)
    return bool(a and b and a > b)


def _read_state():
    try:
        with open(STATE, encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_state(d):
    tmp = "%s.%d.tmp" % (STATE, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _proxy_string():
    """系统代理字符串（先环境变量，再 WinINET）。没有就返回 ''。"""
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
              "ALL_PROXY", "all_proxy"):
        v = os.environ.get(k)
        if v:
            return str(v).strip()
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            if winreg.QueryValueEx(k, "ProxyEnable")[0]:
                return str(winreg.QueryValueEx(k, "ProxyServer")[0] or "").strip()
    except Exception:
        pass
    return ""


def _opener(proxy):
    """按代理类型造 urllib opener；socks5 需要 PySocks，没有就直连（静默失败）。"""
    if not proxy:
        return urllib.request.build_opener()
    low = proxy.lower()
    if low.startswith("socks"):
        try:
            import socks
            from sockshandler import SocksiPyHandler
            s = re.sub(r"^socks5h?://", "", proxy, flags=re.I).strip("/")
            host, _, port = s.rpartition(":")
            if not host:
                host, port = s, "1080"
            typ = socks.SOCKS5
            return urllib.request.build_opener(SocksiPyHandler(typ, host, int(port)))
        except Exception:
            return urllib.request.build_opener()
    url = proxy if "://" in proxy else ("http://" + proxy)
    h = urllib.request.ProxyHandler({"http": url, "https": url})
    return urllib.request.build_opener(h)


def _parse_json(body):
    data = json.loads(body.decode("utf-8", "replace"))
    tag = str(data.get("tag_name") or "").strip()
    if not _ver_tuple(tag):
        return None
    return {"ok": True, "latest": tag.lstrip("vV"), "tag": tag,
            "page": str(data.get("html_url") or RELEASES_PAGE),
            "published": str(data.get("published_at") or "")}


def _parse_html(body, final_url):
    """releases/latest 会 302 到 /releases/tag/vX.Y.Z；从 URL 或页面里抓 tag。"""
    text = body.decode("utf-8", "replace")
    m = re.search(r"/releases/tag/(v?\d+(?:\.\d+){0,3})", str(final_url or ""))
    if not m:
        m = re.search(r"/releases/tag/(v?\d+(?:\.\d+){0,3})", text)
    if not m:
        m = re.search(r'"tagName":"(v?\d+(?:\.\d+){0,3})"', text)
    if not m or not _ver_tuple(m.group(1)):
        return None
    tag = m.group(1)
    return {"ok": True, "latest": tag.lstrip("vV"), "tag": tag,
            "page": "%s/tag/%s" % (RELEASES_PAGE, tag), "published": ""}


def fetch_latest():
    """多通道查最新稳定版。任一通道成功即返回，全失败返回 ok=False（静默）。"""
    proxy = _proxy_string()
    opener = _opener(proxy)
    errors = []
    for name, url, kind in CHANNELS:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA,
                              "Accept": "application/vnd.github+json"})
            with opener.open(req, timeout=TIMEOUT) as r:
                body = r.read()
                final = r.geturl()
            result = (_parse_json(body) if kind == "json"
                      else _parse_html(body, final))
            if result:
                result["via"] = name
                return result
            errors.append("%s:no-tag" % name)
        except urllib.error.HTTPError as e:
            errors.append("%s:HTTP%s" % (name, e.code))
        except Exception as e:
            errors.append("%s:%s" % (name, type(e).__name__))
    return {"ok": False, "error": "; ".join(errors)}


def check(current, force=False):
    """返回给界面的完整更新信息（不抛异常）。force=True 时无条件联网刷新。"""
    st = _read_state()
    now = time.time()
    cached = st.get("result") if isinstance(st.get("result"), dict) else {}
    fresh = (now - float(st.get("checked_at") or 0)) < TTL
    if not force and fresh and cached:
        result = dict(cached)
    else:
        net = fetch_latest()
        if net.get("ok"):
            result = {"ok": True, "latest": net["latest"], "tag": net["tag"],
                      "page": net["page"], "published": net.get("published", ""),
                      "via": net.get("via")}
            st["result"] = result
            st["checked_at"] = now
            _write_state(st)
        elif cached:
            result = dict(cached)
            result["error"] = net.get("error")
        else:
            result = {"ok": False, "error": net.get("error") or "unavailable"}
    out = dict(result)
    out["current"] = str(current)
    out["releases_page"] = RELEASES_PAGE
    out["available"] = bool(result.get("ok")
                            and is_newer(result.get("latest"), current))
    ignored = st.get("ignore")
    out["ignored"] = bool(ignored and _ver_tuple(ignored) == _ver_tuple(result.get("latest")))
    out["checked_at"] = st.get("checked_at") or None
    return out


def dismiss(version):
    """记住"忽略这个版本"——直到出了更新的版本才会再提示。"""
    st = _read_state()
    st["ignore"] = str(version or "")
    st["ignored_at"] = time.time()
    _write_state(st)


def clear_dismiss():
    st = _read_state()
    st.pop("ignore", None)
    st.pop("ignored_at", None)
    _write_state(st)

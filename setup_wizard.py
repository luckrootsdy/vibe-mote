# -*- coding: utf-8 -*-
"""VibeMote 遥控器 - Windows 安装向导。

用 PyInstaller --onefile --windowed **+ 管理员清单** 打包成单文件 exe，嵌入 .ico 图标。
界面顶部显示品牌 PNG 图片；安装时把程序文件（含**自带的 Python 运行时 runtime\\**）
复制到用户选定的目录，创建桌面 + 开始菜单快捷方式，并可选地拉起官方 VB-CABLE 安装器。

★ 为什么要管理员清单：默认装到 `C:\\Program Files\\…`，写它本来就要管理员；
  而且 VB-CABLE 是**系统级音频驱动**，装它也必须管理员。一次 UAC 全解决，
  好过让用户"装到一半失败"再自己去右键。

打包布局（安装包目录）：
  VibeMote_Setup.exe          <- 本向导（带图标 + 版本信息 + 管理员清单）
  program_files/              <- 被安装的全部程序文件（含 runtime\\ 和 vbcable\\）
  assets/newtop_official2.png <- 向导顶部品牌图
  assets/newtop_official2.ico <- 向导图标（也复制到安装目录）

运行时：程序文件从 exe 同级目录读取；打包测试时若缺省则回退到开发目录。

★ 自包含（2026-11-12）：`program_files\\runtime\\` 是官方 embeddable Python 3.12.9
  + 复制进来的 site-packages，`sys.prefix` 就是它自己。**不要**改回只带 `.venv` ——
  `.venv` 是壳，`pyvenv.cfg` 指向开发机的 `C:\\Python312`，干净机器上起不来
  （`No Python at 'C:\\Python312\\pythonw.exe'`，退出码 103）。
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import winreg
from tkinter import ttk, filedialog, messagebox


# ---------------------------------------------------------------------------
# 路径辅助
# ---------------------------------------------------------------------------
def _exe_dir():
    """打包后的 exe 所在目录；开发模式下为本文件所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def program_src_dir():
    """被安装的程序文件根目录（含 runtime\\ 和 vbcable\\）。

    ⚠ 开发模式下本函数返回**项目目录**（因为 exe 同级没有 program_files），
      而项目目录里既没有 runtime\\ 也没有 vbcable\\ —— 所以任何"拿它当 payload 用"
      的测试/校验脚本，必须先 `W._exe_dir = lambda: <安装包目录>` 覆盖掉，
      否则会误报"安装包不完整"。这是踩过的坑，别怀疑 payload。
    """
    pkg = os.path.join(_exe_dir(), "program_files")
    if os.path.isdir(pkg):
        return pkg
    # 回退：开发模式下直接是本项目目录
    return os.path.dirname(os.path.abspath(__file__))


def asset_png_path():
    pkg = os.path.join(_exe_dir(), "assets", "newtop_official2.png")
    if os.path.isfile(pkg):
        return pkg
    return r"D:\01 AI\AI_RemoteMic\vibe-mote-new\vibe-mote-main_Win64_Setup\newtop_official2.png"


def asset_ico_path():
    pkg = os.path.join(_exe_dir(), "assets", "newtop_official2.ico")
    if os.path.isfile(pkg):
        return pkg
    return r"D:\01 AI\AI_RemoteMic\vibe-mote-new\vibe-mote-main_Win64_Setup\newtop_official2.ico"


# ---------------------------------------------------------------------------
# 虚拟声卡（VB-CABLE）—— 语音功能必需的系统级驱动
#
# ★ 为什么安装向导要管它：干净电脑装完本程序后，**按键正常、语音完全没声音**。
#   用户第一反应是"软件坏了"，实际只是少了这块虚拟声卡。本机一直能用，是因为
#   以前手工装过 —— 这正是最容易漏掉、也最难被用户自己诊断的一项。
#
# 我们只做两件事：把**官网原文件原样**放进包里；**拉起它自己的安装器**。
# 不静默安装、不改它的文件（官方 readme 要求用户自己同意许可）。
# ---------------------------------------------------------------------------
CABLE_SUBDIR = "vbcable"
CABLE_SETUP_NAME = "VBCABLE_Setup_x64.exe"
# app.py 的 CABLE_PACK_FILES 必须与这里一致（同一套 6 个文件）
CABLE_FILES = ("VBCABLE_Setup_x64.exe", "VBCABLE_ControlPanel.exe",
               "vbMmeCable64_win10.inf", "vbaudio_cable64_win10.sys",
               "vbaudio_cable64_win10.cat", "readme.txt")
CABLE_UNINST_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
CABLE_SVC_NAMES = ("VB-Cable", "VBAudioVACMME")


def cable_setup_path(src_root):
    return os.path.join(src_root, CABLE_SUBDIR, CABLE_SETUP_NAME)


def cable_pack_missing(src_root):
    """包里缺哪几个官方文件（空列表 = 齐全）。"""
    d = os.path.join(src_root, CABLE_SUBDIR)
    return [n for n in CABLE_FILES if not os.path.isfile(os.path.join(d, n))]


def cable_installed():
    """这台机器装没装 VB-CABLE —— **只看注册表**，不 import sounddevice。

    两处痕迹任一存在即算装过：
      · 「应用和功能」里那条 UninstallString（官方 Pack45 会写，且带 Publisher 校验）
      · HKLM\\SYSTEM\\CurrentControlSet\\Services 下的两个服务键
    """
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CABLE_UNINST_ROOT) as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                if "vbcable" not in sub.lower().replace(" ", ""):
                    continue
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        CABLE_UNINST_ROOT + "\\" + sub) as sk:
                        pub = str(winreg.QueryValueEx(sk, "Publisher")[0]).strip()
                        if "vb-audio" in pub.lower():
                            return True
                except OSError:
                    continue
    except OSError:
        pass
    for svc in CABLE_SVC_NAMES:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Services" + "\\" + svc):
                return True
        except OSError:
            continue
    return False


def dir_size(path):
    """目录总字节数（算不准也没关系，只用来显示"约 xx MB"）。"""
    total = 0
    for dp, _dn, fn in os.walk(path):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------------------
# 需要安装的文件列表
# 排除：客户端运行时产生的日志/状态/备份、.git、构建产物
# ⚠ `.venv` **不装**：它是开发用的壳（标准库在开发机的 C:\Python312），
#   装过去既占 170MB 又起不来。运行时一律用 runtime\（见文件头注释）。
# ---------------------------------------------------------------------------
SKIP_DIRS = {".git", "build", "dist", "__pycache__", ".venv",
             "_upgrade_backup_20261105"}
# ⚠ `build` / `dist` 是通用名，会**误伤**第三方包里的同名子目录：
#   实测 payload 目录 771 个文件、清单只有 727 个，差的 44 个 =
#   34 个 `.pyc` + 2 个 `.github/*.yml` + 8 个 `pip\_internal\operations\build\*.py`。
#   目前无害（pip 只是随 runtime 带的诊断工具，离线版不装包），
#   但**别**以为"清单少了就是 bug"。
SKIP_NAMES = {
    "client.log",
    "client.log.20261109_pre_fix",
    "_last_heal", ".last_heal",
    "_ready.txt", "_run_state.txt", "_auto_recovery.json",
    "_app_pid.txt",
    ".gitattributes", ".gitignore",
}
SKIP_SUFFIXES = (".pyc", ".pyo",
                 # ★ `.spec` 是 PyInstaller 的构建配方，属于开发文件。
                 #   装到用户机器上没用（没有源码就重建不了），只会让人困惑。
                 ".spec")
SKIP_CONTAINS = ("bak_",)


def collect_program_files(root):
    """返回 [(src_abs, rel_path), ...] 需要安装的文件（全部文件，含 runtime\\ / vbcable\\）。"""
    result = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted([d for d in dn if d not in SKIP_DIRS and not d.startswith(".")])
        for f in sorted(fn):
            if f in SKIP_NAMES:
                continue
            if f.endswith(SKIP_SUFFIXES):
                continue
            if any(s in f for s in SKIP_CONTAINS):
                continue
            src = os.path.join(dp, f)
            rel = os.path.relpath(src, root)
            result.append((src, rel))
    return result


def verify_payload(root):
    """装之前先确认"这个包是能跑的" —— 缺运行时/缺虚拟声卡，宁可当场报错。

    ★ 为什么必须在这里拦：以前包里的 Python 只有 `.venv` 两个转发 exe，
      干净机器上装完双击就是"没反应"（No Python at 'C:\\Python312\\...'），
      而安装向导自己一路绿灯、还提示"安装成功"。**装完才发现是废包**是最坏的一种。
    """
    problems = []
    rt = os.path.join(root, "runtime")
    for n in ("python.exe", "pythonw.exe", "python312.dll", "python312.zip",
              "python312._pth"):
        if not os.path.isfile(os.path.join(rt, n)):
            problems.append(f"runtime\\{n}")
    sp = os.path.join(rt, "Lib", "site-packages")
    if not os.path.isdir(sp):
        problems.append("runtime\\Lib\\site-packages\\")
    else:
        # ⚠ 有的依赖是**单文件模块**而不是包目录：sounddevice 就是 `sounddevice.py`，
        #   typing_extensions 也是。只查目录会把好包误判成"缺 sounddevice"。
        for mod in ("bleak", "sounddevice", "frida", "winrt", "cffi"):
            if not (os.path.exists(os.path.join(sp, mod))
                    or os.path.exists(os.path.join(sp, mod + ".py"))):
                problems.append(f"runtime\\Lib\\site-packages\\{mod}")
    if not os.path.isfile(os.path.join(root, "app.py")):
        problems.append("app.py")
    if problems:
        raise RuntimeError(
            "安装包不完整（这些文件缺失）：\n  " + "\n  ".join(problems) +
            "\n\n请重新下载/重新打包安装包。")
    return True


# ---------------------------------------------------------------------------
# 快捷方式：PowerShell + WScript.Shell（无需 pywin32）
# ---------------------------------------------------------------------------
def _esc(s):
    return str(s).replace("'", "''")


def create_shortcut_wsh(lnk_path, target, args="", workdir="", icon="", icon_index=0, desc=""):
    ps = (
        f"$s = New-Object -ComObject WScript.Shell; "
        f"$l = $s.CreateShortcut('{_esc(lnk_path)}'); "
        f"$l.TargetPath = '{_esc(target)}'; "
        f"$l.Arguments = '{_esc(args)}'; "
        f"$l.WorkingDirectory = '{_esc(workdir)}'; "
    )
    if icon:
        ps += f"$l.IconLocation = '{_esc(icon)},{icon_index}'; "
    ps += f"$l.Description = '{_esc(desc)}'; "
    ps += "$l.Save()"
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def get_special_folder(folder):
    import subprocess as _sp
    names = {"desktop": "Desktop", "startmenu": "StartMenu", "programs": "Programs"}
    ps = f"[Environment]::GetFolderPath('{names[folder]}')"
    r = _sp.run(
        ["powershell.exe", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# 安装逻辑
# ---------------------------------------------------------------------------
class FileLocked(Exception):
    """目标文件被别的进程占着，覆盖不了（典型是 WinError 32）。

    为什么单独做一类异常：以前 `shutil.copy2` 抛什么就原样弹给用户，
    界面上只有一句
        [WinError 32] 另一个程序正在使用此文件，进程无法访问。: '...runtime\\LICENSE.txt'
    —— 用户既不知道是**谁**占着，也不知道该关什么。现在把占用进程一起带出来。
    """

    def __init__(self, paths, procs):
        self.paths = list(paths)
        self.procs = list(procs)
        names = "、".join(sorted({(f"{p[1]} (PID {p[0]})" if p[1] else f"PID {p[0]}")
                                 for p in self.procs}))
        if not names:
            names = "没能识别（可能是杀软 / 索引服务在扫描）"
        more = "" if len(self.paths) <= 3 else f" 等 {len(self.paths)} 个文件"
        super().__init__(
            "下面这些文件正被别的程序占用，没法覆盖：\n  "
            + "\n  ".join(os.path.basename(p) for p in self.paths[:3]) + more
            + "\n\n占用它的进程：" + names
            + "\n\n处理办法：\n"
            "  1) 先关掉上面这些程序（多半是还在运行的 VibeMote 客户端），再点“安装”；\n"
            "  2) 还不行就重启一次电脑，然后重新双击安装包。\n"
            "（安装向导已经自动结束过安装目录里的 VibeMote，剩下的通常是别的软件）")


def _copy_one(src, dst):
    """覆盖复制一个文件：先清掉只读位，失败重试几次。

    ★ 为什么要重试：升级覆盖时目标文件常被"短暂"打开 —— 杀软扫描、
      Windows 索引服务、资源管理器缩略图都会这样。实测这类占用几秒内自己就放开了，
      直接报错等于让用户白点一次"安装"。
    """
    last = None
    for attempt in range(4):
        try:
            if os.path.exists(dst):
                try:
                    os.chmod(dst, stat.S_IWRITE)   # 只读属性会让 copy2 直接 PermissionError
                except OSError:
                    pass
            shutil.copy2(src, dst)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.3 * (attempt + 1))
    raise last


def install_files(src_root, dst_root, file_list, progress_cb=None):
    total = len(file_list)
    locked = []
    for i, (src, rel) in enumerate(file_list, 1):
        dst = os.path.join(dst_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            _copy_one(src, dst)
        except PermissionError:
            locked.append(dst)          # 记下来，装完统一报"谁占着"，别装到一半就弹框
        if progress_cb:
            progress_cb(i, total, rel)
    if locked:
        raise FileLocked(locked, locking_processes(locked))


def grant_users_modify(dst_root):
    """让**普通用户**也能写安装目录。返回 (ok, 输出)。

    ★ 为什么必须做（2026-11-13 实测，不是猜的）：
    默认装到 `C:\\Program Files\\…`，那棵树继承给 `BUILTIN\\Users` 的只有 RX。
    而本程序把 config.json / client.log / _ready.txt / _app_pid.txt / _run_state.txt
    全放在**自己目录**里。以非提权身份写这些文件，实测：
        config.json  -> PermissionError [Errno 13]
        client.log   -> PermissionError（app.py 的 Bus 里被 try/except 吞掉，静默）
        _ready.txt   -> PermissionError
    症状：程序能开、按键全好（frida 走 HID，不写盘），但「扫描遥控器 → 保存」
    存不下来 → 蓝牙地址永远是空 → **语音永远不出字**，而且一条日志都没有。
    v1.3.0 就是栽在这上面（用户反馈"语音不行 其余都ok"）。

    用 SID 而不是名字：`*S-1-5-32-545` = BUILTIN\\Users，中文/日文系统上组名不叫 Users。
    `/T` 是必须的 —— 已经复制进去的文件不会自动继承**事后**加的 ACE。
    """
    try:
        r = subprocess.run(
            ["icacls", dst_root, "/grant", "*S-1-5-32-545:(OI)(CI)M", "/T", "/C", "/Q"],
            capture_output=True, text=True, errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW)
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out.strip()
    except Exception as e:                     # icacls 不在 / 被策略挡住
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# 升级覆盖安装：找到旧副本 + 停掉正在运行的那个
#
# ★ 2026-11-15 用户实测踩到的两个坑，这一节就是治它们的：
#   ① **没记住上次装在哪**：原来装在 `D:\Program Files\VibeMote 遥控器`，向导却默认
#      `C:\Program Files\VibeMote 遥控器`。根因是 `_default_install_dir()` 恒返回
#      `%PROGRAMFILES%`，而且**安装时没往注册表写任何东西** —— "上次装在哪"这条信息
#      根本不存在。现在：装完写 `HKCU/HKLM\Software\VibeMote\InstallDir`；启动时按
#      注册表 → 开始菜单/桌面快捷方式 → 各盘 Program Files 扫描 依次去找。
#   ② **旧版还在跑，覆盖就报 [WinError 32]**：卡在 `runtime\LICENSE.txt`。用户点了
#      "停止运行"只停了语音角色，客户端进程还活着（它的解释器就是安装目录里的
#      `runtime\pythonw.exe`）。现在安装前自动结束该目录下的实例并等它真退出；
#      万一杀不掉，用 Restart Manager 查出"到底是谁占着这个文件"，把进程名给用户。
# ---------------------------------------------------------------------------
REG_KEY = r"Software\VibeMote"
REG_VALUE = "InstallDir"

# 装好之后**不能**被新版本覆盖的文件（用户自己的数据）。
# 只有 payload 里也有的那些才需要备份 —— 目前实际只有 config.json（遥控器蓝牙地址、
# 按键映射、语音设置都在里面，被模板覆盖 = 用户重新配一遍）。
USER_DATA_NAMES = (
    "config.json", "_uninstall_info.json",
    "client.log", "_app_pid.txt", "_run_state.txt", "_ready.txt",
    "_auto_recovery.json", "_deferred_result.txt", "_uninstall_result.json",
    "_last_heal", ".last_heal", "uninstall.log", "elevate_setup.log",
)


def _reg_get(root, path, name):
    try:
        with winreg.OpenKey(root, path) as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except OSError:
        return ""


def _reg_set(root, path, name, value):
    try:
        with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        return True
    except OSError:
        return False


def remember_install_dir(dst_root):
    """把"这次装到哪了"写进注册表，下次升级靠它记起路径。返回 (HKCU 成功, HKLM 成功)。

    两个都写：HKCU 是**不需要管理员**也能写的（万一以后去掉管理员清单），
    HKLM 是**换用户登录也还在**。读的时候先 HKCU（用户最后一次的选择最准）。
    """
    return (_reg_set(winreg.HKEY_CURRENT_USER, REG_KEY, REG_VALUE, dst_root),
            _reg_set(winreg.HKEY_LOCAL_MACHINE, REG_KEY, REG_VALUE, dst_root))


def _looks_like_install(d):
    """这个目录是不是一份 VibeMote 安装（防止把同名空目录当成旧版本）。"""
    if not d or not os.path.isdir(d):
        return False
    if not os.path.isfile(os.path.join(d, "app.py")):
        return False
    marks = ("runtime\\pythonw.exe", "启动遥控器.exe", "_uninstall_info.json")
    return any(os.path.exists(os.path.join(d, m)) for m in marks)


def read_uninstall_info(d):
    """读安装目录里的 `_uninstall_info.json`（没有就返回 {}）。"""
    try:
        with open(os.path.join(d, "_uninstall_info.json"), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _lnk_target(lnk):
    """读一个 .lnk 的 TargetPath 和 WorkingDirectory（PowerShell + WScript.Shell）。

    为什么不只认桌面快捷方式：本机实测**桌面那个不存在**，只有开始菜单里有；
    而且用户可能自己把快捷方式挪走了。所以两个位置都看，读到哪个用哪个。
    """
    if not os.path.isfile(lnk):
        return ""
    ps = ("$s = New-Object -ComObject WScript.Shell; "
          f"$l = $s.CreateShortcut('{_esc(lnk)}'); "
          "$l.TargetPath + \"`n\" + $l.WorkingDirectory")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-Command", ps],
                           capture_output=True, timeout=25,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        return ""
    lines = _decode(r.stdout or b"").splitlines()
    for line in lines:
        line = line.strip().strip('"')
        if line and os.path.isdir(line):
            return line
    return ""


def find_previous_install():
    r"""找"这台电脑上已经装在哪了"。返回 (目录, 来源说明)；没找到返回 ("", "")。

    顺序是有讲究的：注册表最准（我们装的时候自己写的）→ 快捷方式（老版本没写注册表，
       但快捷方式的工作目录就是安装目录）→ 磁盘扫描（连快捷方式都被删了时兜底）。
    ★ 本机就是这么找到的：注册表里什么都没有，靠 `_uninstall_info.json` 标记扫到
      `D:\Program Files\VibeMote 遥控器`（用户 2026-11-15 报"没记住路径"的现场）。
    """
    # ① 注册表：我们自己写的 + 「应用和功能」里别的安装器可能留下的 InstallLocation
    for root, label in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        d = _reg_get(root, REG_KEY, REG_VALUE)
        if _looks_like_install(d):
            return d, f"注册表 {label}\\{REG_KEY}\\{REG_VALUE}"
    uninst = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, uninst) as k:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    d = _reg_get(root, uninst + "\\" + sub, "InstallLocation")
                    if d and _looks_like_install(d):
                        return d, f"注册表「应用和功能」\\{sub}\\InstallLocation"
        except OSError:
            continue

    # ② 快捷方式（工作目录 / 目标所在目录）
    links = []
    try:
        sm = get_special_folder("programs")
        if sm:
            links.append(os.path.join(sm, "VibeMote 遥控器", "启动遥控器.lnk"))
    except Exception:
        pass
    try:
        desk = get_special_folder("desktop")
        if desk:
            links.append(os.path.join(desk, "启动遥控器.lnk"))
    except Exception:
        pass
    for lnk in links:
        d = _lnk_target(lnk)
        if _looks_like_install(d):
            return d, f"快捷方式 {os.path.basename(os.path.dirname(lnk))}\\{os.path.basename(lnk)}"

    # ③ 磁盘扫描：每个盘的 Program Files 及其常见变体
    for d, label in _scan_disks_for_install():
        return d, label
    return "", ""


def _scan_disks_for_install():
    """扫各盘的 Program Files / 根目录（只扫两层，不递归整盘）。"""
    subs = (r"Program Files\VibeMote 遥控器", r"Program Files (x86)\VibeMote 遥控器",
            r"Program Files\VibeMote", "VibeMote 遥控器", "VibeMote")
    try:
        drives = [d.rstrip("\\") for d in os.listdrives()]
    except Exception:
        drives = [f"{c}:\\" for c in "CDEFGHIJ"]
    for drv in drives:
        for sub in subs:
            d = os.path.join(drv + "\\", sub)
            if _looks_like_install(d):
                yield d, f"磁盘扫描 {d}"


def _decode(raw):
    """PowerShell 的重定向输出在中文系统上是 GBK，UTF-8 解会炸 —— 挨个试。"""
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _all_candidate_processes():
    """可能属于本程序的进程：[(pid, name, exe, cmd)]。一次 WMI 查询，约 0.5~1 秒。"""
    ps = ("Get-CimInstance Win32_Process | Where-Object { "
          "$_.Name -match '^(python|pythonw|VibeMote|启动遥控器)' } | "
          "Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        return []
    text = _decode(r.stdout or b"").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for it in data:
        try:
            out.append((int(it.get("ProcessId") or 0), str(it.get("Name") or ""),
                        str(it.get("ExecutablePath") or ""), str(it.get("CommandLine") or "")))
        except Exception:
            continue
    return out


def _pid_alive(pid):
    """进程还活着吗（不用 tasklist，避免再起一个进程 + 编码问题）。"""
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFO
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == 259                                   # STILL_ACTIVE
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        return False


def _pid_from_file(d):
    """安装目录里 `_app_pid.txt` 记的 PID（拿锁的进程自己写的，最可靠）。"""
    try:
        with open(os.path.join(d, "_app_pid.txt"), encoding="utf-8") as f:
            return int(f.read().strip().split()[0])
    except Exception:
        return 0


def list_related_processes(dirs):
    r"""属于这些安装目录的进程：[(pid, name, exe, cmd)]。

    三种认人方式（缺一个就会漏掉真正占着文件的那个）：
      ① 可执行文件在目录里 —— `runtime\pythonw.exe` 起的客户端；
      ② 命令行里带这个目录 —— 从快捷方式/计划任务起的、cwd 不同的；
      ③ 目录里 `_app_pid.txt` 记的 PID —— 且**必须是 python/启动器进程**才认，
         否则 PID 被系统复用后会把无关程序杀掉。
    """
    dirs = [os.path.normcase(os.path.normpath(d)) for d in dirs if d]
    if not dirs:
        return []
    pidfiles = {p for p in (_pid_from_file(d) for d in dirs) if p}
    out = []
    for pid, name, exe, cmd in _all_candidate_processes():
        if not pid or pid == os.getpid():
            continue
        hit = False
        if exe:
            e = os.path.normcase(os.path.normpath(exe))
            hit = any(e.startswith(d + os.sep) for d in dirs)
        if not hit and cmd:
            c = os.path.normcase(cmd)
            hit = any(d in c for d in dirs)
        if not hit and pid in pidfiles:
            low = name.lower()
            hit = low.startswith("python") or low.startswith("启动") or low.startswith("vibemote")
        if hit:
            out.append((pid, name, exe, cmd))
    return out


def stop_running_instances(dirs, log_cb=None):
    """结束这些目录里的 VibeMote，并**等它们真的退出**。返回 (结束的, 还活着的)。

    ★ 用户 2026-11-15 的原话："应该是自动停止原来相同的程序，然后在原来的路径上
      覆盖安装。" 所以这里不问、不弹框，直接做；只把过程写在进度行上。
    ★ 必须 `taskkill /F /T`：客户端会拉起 voice_worker 子进程（孙进程），
      只杀父进程的话子进程继续攥着安装目录里的文件。
    """
    procs = list_related_processes(dirs)
    if log_cb and procs:
        log_cb("正在结束旧版本：" + "、".join(f"{n or '进程'} (PID {p})" for p, n, _e, _c in procs))
    for pid, _name, _exe, _cmd in procs:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=25,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass
    deadline = time.time() + 15
    while time.time() < deadline:
        if not any(_pid_alive(p[0]) for p in procs):
            break
        time.sleep(0.25)
    time.sleep(0.4)                       # 让内核把文件句柄收干净，再开始覆盖
    return procs, [p for p in procs if _pid_alive(p[0])]


def locking_processes(paths):
    """这些文件正被哪些进程占用：[(pid, 应用名)]。查不到就返回 []（不影响主流程）。

    ★ 用 Windows 自带的 Restart Manager（`rstrtmgr.dll`，就是"文件被占用，请关闭
      以下程序"那个对话框背后的 API）。为什么不自己猜：占用者可能是客户端、可能是
      杀软、可能是索引服务 —— 猜不出来，但系统知道。实测它能把 PID + 应用名报出来。
    """
    paths = [os.path.abspath(p) for p in paths if p][:32]
    if not paths:
        return []
    try:
        import ctypes
        from ctypes import wintypes
        rm = ctypes.WinDLL("rstrtmgr")
    except Exception:
        return []

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD),
                    ("ProcessStartTime", wintypes.FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [("Process", RM_UNIQUE_PROCESS),
                    ("strAppName", wintypes.WCHAR * 256),
                    ("strServiceShortName", wintypes.WCHAR * 64),
                    ("ApplicationType", ctypes.c_uint),
                    ("AppStatus", wintypes.ULONG),
                    ("TSSessionId", wintypes.DWORD),
                    ("bRestartable", wintypes.BOOL)]

    rm.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
                                  wintypes.LPWSTR]
    rm.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT,
                                       ctypes.POINTER(wintypes.LPCWSTR), wintypes.UINT,
                                       ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p]
    rm.RmGetList.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.UINT),
                             ctypes.POINTER(wintypes.UINT),
                             ctypes.POINTER(RM_PROCESS_INFO),
                             ctypes.POINTER(wintypes.DWORD)]
    rm.RmEndSession.argtypes = [wintypes.DWORD]

    handle = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(256)
    if rm.RmStartSession(ctypes.byref(handle), 0, key) != 0:
        return []
    try:
        arr = (wintypes.LPCWSTR * len(paths))(*paths)
        if rm.RmRegisterResources(handle, len(paths), arr, 0, None, 0, None) != 0:
            return []
        need = wintypes.UINT(0)
        have = wintypes.UINT(0)
        reasons = wintypes.DWORD(0)
        rc = rm.RmGetList(handle, ctypes.byref(need), ctypes.byref(have), None,
                          ctypes.byref(reasons))
        if rc not in (0, 234):            # 234 = ERROR_MORE_DATA（正常，第一次就是它）
            return []
        n = max(int(need.value), 1)
        buf = (RM_PROCESS_INFO * n)()
        have = wintypes.UINT(n)
        if rm.RmGetList(handle, ctypes.byref(need), ctypes.byref(have), buf,
                        ctypes.byref(reasons)) != 0:
            return []
        out = []
        for i in range(int(have.value)):
            pid = int(buf[i].Process.dwProcessId)
            app = (buf[i].strAppName or "").strip()
            if pid and pid != os.getpid():
                out.append((pid, app))
        return out
    finally:
        try:
            rm.RmEndSession(handle)
        except Exception:
            pass


def _backup_user_data(dst_root, file_list, tmp_dir):
    """升级前把"用户自己的文件"存到临时目录（只存 payload 里也有的那几个）。"""
    payload = {rel for _src, rel in file_list}
    saved = []
    for name in USER_DATA_NAMES:
        if name not in payload:
            continue                      # payload 里没有 → 本来就不会被覆盖
        p = os.path.join(dst_root, name)
        if not os.path.isfile(p):
            continue
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            shutil.copy2(p, os.path.join(tmp_dir, name))
            saved.append(name)
        except OSError:
            pass
    return saved


def _restore_user_data(dst_root, saved, tmp_dir):
    """把刚才存起来的用户文件盖回去。返回真的盖回去的名单。"""
    back = []
    for name in saved:
        try:
            shutil.copy2(os.path.join(tmp_dir, name), os.path.join(dst_root, name))
            back.append(name)
        except OSError:
            pass
    return back


_SINGLETON_HANDLE = None


def ensure_single_instance():
    r"""同一个向导只许跑一个。已在跑 → 返回 False。

    ★ 2026-11-15 现场：用户连点了两次安装包，**两个向导同时往同一个目录复制**，
      结果两边都在 `runtime\LICENSE.txt` 上撞 WinError 32，界面全卡死。
      这不是"文件被占用"的锅，是自己撞自己。用命名互斥体挡在最前面。
    """
    try:
        import ctypes
        global _SINGLETON_HANDLE
        _SINGLETON_HANDLE = ctypes.windll.kernel32.CreateMutexW(None, False,
                                                               "Global\\VibeMoteSetupWizard")
        return ctypes.windll.kernel32.GetLastError() != 183      # ERROR_ALREADY_EXISTS
    except Exception:
        return True


# ★ 版本号只有一个真源（2026-11-15 踩过）：这里写死过一次 "1.3.1"，
#   而 InstallerApp.APP_VERSION 已经升到 1.3.2 —— 结果 `_uninstall_info.json`
#   里记的版本永远是旧的（卸载列表里显示错版本），而且发布前那道 e2e 闸门
#   也跟着"假失败"。**升版本时只改这一行**（和 app.py 的 APP_VERSION 保持一致）。
WIZARD_VERSION = "1.3.7"


def write_uninstall_info(dst_root, app_name="VibeMote 遥控器", version=None):
    info = {"app_name": app_name, "version": version or WIZARD_VERSION,
            "install_dir": dst_root, "installed_files": []}
    for dp, dn, fn in os.walk(dst_root):
        for f in fn:
            fp = os.path.join(dp, f)
            info["installed_files"].append(os.path.relpath(fp, dst_root))
    with open(os.path.join(dst_root, "_uninstall_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# GUI
#
# 步骤：0 欢迎 → 1 安装位置 → 2 虚拟声卡 → 3 复制文件 → 4 完成
# ---------------------------------------------------------------------------
class InstallerApp:
    APP_NAME = "VibeMote 遥控器"
    APP_VERSION = WIZARD_VERSION
    PUBLISHER = "VibeMote"

    def __init__(self, root):
        self.root = root
        self.root.title(f"{self.APP_NAME} 安装向导")
        self.root.resizable(False, False)
        self.root.geometry("640x520")

        self._src_root = program_src_dir()
        self._cable_installed = cable_installed()
        self._cable_missing = cable_pack_missing(self._src_root)
        self._cable_launched = False
        self._size_bytes = None
        # icacls 授权的结果（装完才知道）。None = 还没装 / 没跑。
        self._acl_ok = None
        self._acl_msg = ""
        # 升级覆盖安装：上次装在哪 / 版本号 / 这次结束了哪些进程 / 保留了哪些用户文件
        self._prev_dir, self._prev_src = find_previous_install()
        self._prev_info = read_uninstall_info(self._prev_dir) if self._prev_dir else {}
        self._stopped = []
        self._kept = []
        self._remembered = (False, False)

        self._build_banner()
        self.content = tk.Frame(self.root)
        self.content.pack(fill="both", expand=True, padx=24, pady=16)

        self._build_bottom()          # 先创建底部按钮，再显示首页（首页引用 btn_back）
        self._stage = 0
        self._install_dir = self._default_install_dir()
        self._show_welcome()

    def _default_install_dir(self):
        """默认装到**上次装的那个目录**（没有旧副本时才用 %PROGRAMFILES%）。

        ★ 2026-11-15 用户反馈："我原来安装在 D 盘的，你现在没记住。"
          升级覆盖安装的唯一正确默认值就是"原来的位置" —— 装到别处会变成两份，
          用户桌面上的快捷方式还会指向旧的那份。
        """
        if self._prev_dir:
            return self._prev_dir
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        return os.path.join(pf, self.APP_NAME)

    def _build_banner(self):
        self.banner = tk.Frame(self.root, height=150)
        self.banner.pack(fill="x")
        self.banner.pack_propagate(False)
        self.img_label = tk.Label(self.banner)
        self.img_label.pack(expand=True)
        try:
            self.photo = tk.PhotoImage(file=asset_png_path())
            self.img_label.config(image=self.photo)
        except Exception:
            self.img_label.config(text=self.APP_NAME, font=("Microsoft YaHei", 20, "bold"), fg="#c00000")

    def _build_bottom(self):
        self.bottom = tk.Frame(self.root)
        self.bottom.pack(fill="x", padx=24, pady=12)
        self.btn_back = tk.Button(self.bottom, text="上一步", width=12, state="disabled", command=self._prev)
        self.btn_back.pack(side="left")
        self.btn_next = tk.Button(self.bottom, text="下一步", width=12, command=self._next)
        self.btn_next.pack(side="right")
        self.btn_cancel = tk.Button(self.bottom, text="取消", width=12, command=self.root.quit)
        self.btn_cancel.pack(side="right", padx=(0, 10))

    def _clear_content(self):
        for w in self.content.winfo_children():
            w.destroy()

    # ---------------------------------------------------------------- 0 欢迎
    def _show_welcome(self):
        self._clear_content()
        self.btn_back.config(state="disabled")
        self.btn_next.config(text="下一步 >", state="normal")
        self.btn_cancel.config(state="normal")
        tk.Label(self.content, text=self.APP_NAME, font=("Microsoft YaHei", 16, "bold")).pack(pady=(4, 2))
        tk.Label(self.content, text=f"版本 {self.APP_VERSION}", fg="#666666").pack(pady=(0, 10))
        txt = (
            "本向导将把 VibeMote 遥控器客户端安装到您的计算机。\n\n"
            "安装包**自带 Python 运行时和全部依赖**，目标电脑不需要装 Python，\n"
            "也不需要联网。\n\n"
            "语音功能需要一块虚拟声卡（VB-CABLE）。安装包已带上它的官方原文件，\n"
            "本向导会替你启动它的安装器 —— 装完**需要重启一次电脑**。\n\n"
            "点击“下一步”继续。"
        )
        tk.Label(self.content, text=txt, justify="left", wraplength=560).pack(pady=6)

    # ------------------------------------------------------------ 1 安装位置
    def _show_dir(self):
        self._clear_content()
        self.btn_back.config(state="normal")
        self.btn_next.config(text="下一步 >", state="normal")
        tk.Label(self.content, text="选择安装位置", font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", pady=(0, 10))
        row = tk.Frame(self.content)
        row.pack(fill="x", pady=6)
        self.ent_dir = tk.Entry(row, width=52)
        self.ent_dir.insert(0, self._install_dir)
        self.ent_dir.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="浏览…", width=10, command=self._browse).pack(side="left", padx=(8, 0))

        # ★ 升级覆盖安装：明确告诉用户"我认出你的旧版本了，就装在这里"
        if self._prev_dir:
            ver = self._prev_info.get("version") or "版本未知"
            tk.Label(self.content,
                     text=f"✓ 检测到已安装的旧版本（{ver}），已按上次的位置填好。\n"
                          f"  来源：{self._prev_src}\n"
                          "  直接点“下一步”= 升级覆盖安装：会自动结束正在运行的旧版，\n"
                          "  保留你的 config.json（遥控器地址、按键映射、语音设置），"
                          "其它文件覆盖成新版。",
                     fg="#0a7a2f", wraplength=560, justify="left").pack(anchor="w", pady=(8, 0))
        else:
            tk.Label(self.content,
                     text="（这台电脑上没找到已安装的旧版本，这是一次全新安装。）",
                     fg="#666666", wraplength=560, justify="left").pack(anchor="w", pady=(8, 0))

        if self._size_bytes is None:
            self._size_bytes = dir_size(self._src_root)
        need_mb = self._size_bytes / 1e6
        try:
            free_mb = shutil.disk_usage(self._install_dir if os.path.isdir(self._install_dir)
                                        else os.path.splitdrive(self._install_dir)[0] + "\\").free / 1e6
        except Exception:
            free_mb = None
        hint = f"所需空间约 {need_mb:.0f} MB（含自带的 Python 运行环境）。"
        if free_mb is not None:
            hint += f"  该磁盘剩余 {free_mb:.0f} MB。"
        color = "#666666"
        if free_mb is not None and free_mb < need_mb + 100:
            hint += "\n⚠ 空间不足，请换一个磁盘或先清理。"
            color = "#c00000"
        tk.Label(self.content, text=hint, fg=color, wraplength=560,
                 justify="left").pack(anchor="w", pady=(12, 0))
        tk.Label(self.content, text="（安装到 C:\\Program Files 需要管理员权限，"
                                    "本向导启动时已经请求过）",
                 fg="#666666", wraplength=560, justify="left").pack(anchor="w", pady=(8, 0))

    # ------------------------------------------------------------ 2 虚拟声卡
    def _show_cable(self):
        self._clear_content()
        self.btn_back.config(state="normal")
        self.btn_next.config(text="安装", state="normal")
        tk.Label(self.content, text="虚拟声卡（VB-CABLE）",
                 font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", pady=(0, 8))

        if self._cable_installed:
            tk.Label(self.content,
                     text="✓ 这台电脑已经装过 VB-CABLE，跳过这一步。",
                     fg="#0a7a2f", wraplength=560, justify="left").pack(anchor="w", pady=6)
            tk.Label(self.content,
                     text="（如果没有声音，可以在客户端界面上点「检查环境」，"
                          "它会告诉你缺哪一项。）",
                     fg="#666666", wraplength=560, justify="left").pack(anchor="w", pady=4)
            self._cable_wanted = tk.IntVar(value=0)
            return

        tk.Label(self.content,
                 text="语音键要把遥控器麦克风的声音送进电脑，靠的是一块虚拟声卡。\n"
                      "没装它的时候，**按键正常、语音完全没声音** —— 很容易被当成软件坏了。",
                 wraplength=560, justify="left").pack(anchor="w", pady=4)

        if self._cable_missing:
            tk.Label(self.content,
                     text="⚠ 安装包里缺这些官方文件：\n  " + "\n  ".join(self._cable_missing) +
                          "\n请到 https://vb-audio.com/Cable/ 下载官方包，解压后把整个包里的文件\n"
                          "放进程序目录的 vbcable\\ 文件夹，再点客户端界面上的「安装虚拟声卡」。",
                     fg="#c00000", wraplength=560, justify="left").pack(anchor="w", pady=8)
            self._cable_wanted = tk.IntVar(value=0)
            tk.Label(self.content,
                     text="本次先跳过（装完本程序后，界面会引导你装虚拟声卡）。",
                     fg="#666666", wraplength=560, justify="left").pack(anchor="w", pady=4)
            return

        self._cable_wanted = tk.IntVar(value=1)
        tk.Checkbutton(self.content,
                       text="现在启动 VB-CABLE 的官方安装器（推荐）",
                       variable=self._cable_wanted).pack(anchor="w", pady=(10, 4))
        tk.Label(self.content,
                 text="说明：\n"
                      "· 用的是 VB-Audio 官网原版安装器，**一个字节都没改**，版权归作者所有；\n"
                      "· 它会自己弹一个窗口，请点里面的「Install Driver」，并同意它的许可；\n"
                      "· 装完**必须重启一次电脑**才算装好（官方 readme 的要求）；\n"
                      "· 用着顺手的话，建议去 https://vb-cable.com/ 给作者捐一份。",
                 fg="#444444", wraplength=560, justify="left").pack(anchor="w", pady=4)

    # ------------------------------------------------------------ 3 进度
    def _show_progress(self):
        self._clear_content()
        self.btn_back.config(state="disabled")
        self.btn_next.config(state="disabled")
        self.btn_cancel.config(state="disabled")
        tk.Label(self.content, text="正在安装", font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", pady=(0, 10))
        self.lbl_file = tk.Label(self.content, text="准备中…", fg="#666666")
        self.lbl_file.pack(anchor="w")
        self.pb = ttk.Progressbar(self.content, length=560, mode="determinate")
        self.pb.pack(fill="x", pady=12)
        self.lbl_pct = tk.Label(self.content, text="0%", fg="#666666")
        self.lbl_pct.pack(anchor="e")
        self.root.after(100, self._do_install)

    # ------------------------------------------------------------ 4 完成
    def _show_finish(self):
        self._clear_content()
        self.btn_back.config(state="disabled")
        self.btn_next.config(text="完成", state="normal")
        self.btn_cancel.config(state="normal")
        tk.Label(self.content, text="安装完成", font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", pady=(0, 10))

        # 升级覆盖安装做了什么，当场说清楚（用户下次才不会怀疑"我的配置还在吗"）
        bits = []
        if self._stopped:
            bits.append("已自动结束旧版本进程：" + "、".join(
                f"{n or '进程'} (PID {p})" for p, n, _e, _c in self._stopped))
        if self._kept:
            bits.append("保留了你的 " + "、".join(self._kept) + "（升级没有覆盖配置）")
        if bits:
            tk.Label(self.content, text="\n".join(bits), fg="#0a7a2f",
                     wraplength=560, justify="left").pack(anchor="w", pady=(0, 8))

        msg = (
            f"{self.APP_NAME} 已安装到：\n{self._install_dir}\n\n"
            "桌面和开始菜单里都有“启动遥控器”图标，双击即可启动。\n"
            "第一次启动后请做一件事：界面「设置」里点“扫描遥控器”，\n"
            "选中你自己的遥控器地址（安装包里没有预置任何地址）。"
        )
        if self._remembered[0] or self._remembered[1]:
            msg += "\n\n下次双击新版本安装包时，默认还是这个目录（已写进注册表）。"
        tk.Label(self.content, text=msg, justify="left", wraplength=560).pack(pady=(4, 8))

        # ★ 2026-11-13：授权失败必须当场说出来。否则用户后面看到的是
        #   "保存没反应 / 语音不行"，而我们这边完全不知道权限没设上。
        if self._acl_ok is False:
            tk.Label(self.content,
                     text="⚠ 没能把安装目录授权给普通用户（icacls 失败）。\n"
                          "后果：「设置」里保存会失败，语音会因为存不下遥控器蓝牙地址"
                          "而一直不出字（按键不受影响）。\n"
                          "补救：把程序改装到用户目录（例如 D:\\VibeMote），"
                          "或让管理员手动执行：\n"
                          f'  icacls "{self._install_dir}" /grant *S-1-5-32-545:(OI)(CI)M /T\n'
                          f"（原始输出：{self._acl_msg[:200]}）",
                     fg="#c00000", wraplength=560, justify="left").pack(anchor="w", pady=(4, 8))

        if self._cable_launched:
            tk.Label(self.content,
                     text="⚠ 还有两件事：\n"
                          "1) 刚才弹出的 VB-CABLE 窗口里，请点「Install Driver」并同意它的许可\n"
                          "   （如果窗口被挡住了，去开始菜单打开 VB-CABLE 也行）；\n"
                          "2) 装完后**重启一次电脑**，虚拟声卡才会出现。\n"
                          "重启后双击“启动遥控器”，在界面上点「检查环境」，全绿就说明好了。",
                     fg="#c00000", wraplength=560, justify="left").pack(anchor="w", pady=(4, 8))
        elif not self._cable_installed:
            tk.Label(self.content,
                     text="⚠ 这台电脑还没装虚拟声卡（VB-CABLE），语音会没有声音。\n"
                          "启动客户端后，界面上会引导你一键安装（安装包里有官方原文件）。\n"
                          "装完要重启一次电脑。",
                     fg="#c00000", wraplength=560, justify="left").pack(anchor="w", pady=(4, 8))

        self.var_launch = tk.IntVar(value=0 if self._cable_launched else 1)
        tk.Checkbutton(self.content, text="现在启动 VibeMote", variable=self.var_launch).pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------------------ 导航
    def _prev(self):
        if self._stage > 0:
            self._stage -= 1
            self._render()

    def _next(self):
        if self._stage == 0:
            self._stage = 1
            self._render()
        elif self._stage == 1:
            self._install_dir = self.ent_dir.get().strip() or self._default_install_dir()
            self._stage = 2
            self._render()
        elif self._stage == 2:
            self._stage = 3
            self._render()
        elif self._stage == 4:
            if self.var_launch.get():
                exe = os.path.join(self._install_dir, "启动遥控器.exe")
                if os.path.isfile(exe):
                    os.startfile(exe)
            self.root.quit()

    def _render(self):
        if self._stage == 0:
            self._show_welcome()
        elif self._stage == 1:
            self._show_dir()
        elif self._stage == 2:
            self._show_cable()
        elif self._stage == 3:
            self._show_progress()
        elif self._stage == 4:
            self._show_finish()

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self._install_dir, title="选择安装文件夹")
        if d:
            self._install_dir = d
            self.ent_dir.delete(0, tk.END)
            self.ent_dir.insert(0, d)
            self._size_bytes = None
            self._render()

    # ------------------------------------------------------------ 安装
    def _do_install(self):
        try:
            src_root = self._src_root
            verify_payload(src_root)          # 缺运行时/依赖 → 当场报错，别装出个废包
            files = collect_program_files(src_root)

            def progress(i, total, rel):
                self.pb["maximum"] = total
                self.pb["value"] = i
                self.lbl_file.config(text=rel)
                self.lbl_pct.config(text=f"{i * 100 // total}%")
                self.root.update_idletasks()

            def say(text):
                self.lbl_file.config(text=text)
                self.lbl_pct.config(text="")
                self.root.update_idletasks()

            # ① 先停掉旧副本 —— 升级覆盖安装的第一步。
            #    不做这一步，覆盖 runtime\ 里的文件就撞 [WinError 32]（用户 2026-11-15 的现场：
            #    卡在 runtime\LICENSE.txt，"停止运行"只停了语音角色，客户端进程还活着）。
            dirs = [self._install_dir]
            if self._prev_dir and (os.path.normcase(os.path.normpath(self._prev_dir))
                                   != os.path.normcase(os.path.normpath(self._install_dir))):
                dirs.append(self._prev_dir)   # 用户改装了别的目录：旧那份也得停，否则它还占着端口
            say("检查正在运行的 VibeMote …")
            self._stopped, left = stop_running_instances(dirs, log_cb=say)
            if left:
                names = "、".join(f"{n or '进程'} (PID {p})" for p, n, _e, _c in left)
                raise RuntimeError(
                    "旧版本的 VibeMote 还在运行，结束不掉：" + names + "\n\n"
                    "请手动关掉它（任务管理器 → 详细信息 → 结束 pythonw.exe），"
                    "或者重启一次电脑后再双击安装包。\n"
                    "（它占着安装目录里的文件，不关掉就覆盖不了）")

            # ② 用户数据先存起来：payload 里的 config.json 是**模板**（遥控器地址为空），
            #    直接覆盖 = 用户的地址/按键映射/语音设置全丢。升级不该动这些。
            say("备份你的配置（config.json）…")
            tmp_dir = tempfile.mkdtemp(prefix="vibemote_upgrade_")
            saved = _backup_user_data(self._install_dir, files, tmp_dir)
            try:
                install_files(src_root, self._install_dir, files, progress)
            finally:
                self._kept = _restore_user_data(self._install_dir, saved, tmp_dir)
                shutil.rmtree(tmp_dir, ignore_errors=True)

            # ★ 2026-11-13：把安装目录授权给普通用户（Users = Modify）。
            #   不做这一步，装到 C:\Program Files 的普通用户写不了 config.json ——
            #   表现就是"按键全好、语音永远不行"（遥控器蓝牙地址存不下来）。
            #   详见 grant_users_modify() 的注释。
            self.lbl_file.config(text="设置目录权限（让普通用户能保存配置）…")
            self.root.update_idletasks()
            self._acl_ok, self._acl_msg = grant_users_modify(self._install_dir)

            # 升级场景：老版本装过 .venv（170MB 的死重），现在有 runtime 了就可以删掉。
            # 删不掉也无所谓（可能被占着）—— 启动器已经优先 runtime，不影响使用。
            old_venv = os.path.join(self._install_dir, ".venv")
            if (os.path.isdir(old_venv)
                    and os.path.isfile(os.path.join(self._install_dir, "runtime", "pythonw.exe"))):
                self.lbl_file.config(text="清理旧版本留下的 .venv …")
                self.root.update_idletasks()
                shutil.rmtree(old_venv, ignore_errors=True)

            write_uninstall_info(self._install_dir, app_name=self.APP_NAME,
                                 version=self.APP_VERSION)

            # ★ 记住这次的安装位置：下次双击新版本安装包时，默认目录就是这里
            #   （用户 2026-11-15 报"我装 D 盘你没记住"的根治办法）。
            self._remembered = remember_install_dir(self._install_dir)

            # 复制图标到安装目录（快捷方式用）
            try:
                shutil.copy2(asset_ico_path(), os.path.join(self._install_dir, "启动遥控器.ico"))
            except Exception:
                pass

            exe_target = os.path.join(self._install_dir, "启动遥控器.exe")

            # 桌面快捷方式
            desk = get_special_folder("desktop")
            if desk:
                create_shortcut_wsh(
                    os.path.join(desk, "启动遥控器.lnk"),
                    exe_target, workdir=self._install_dir,
                    icon=os.path.join(self._install_dir, "启动遥控器.ico"),
                    desc=f"{self.APP_NAME} {self.APP_VERSION}",
                )

            # 开始菜单
            start = os.path.join(get_special_folder("programs"), self.APP_NAME)
            os.makedirs(start, exist_ok=True)
            create_shortcut_wsh(
                os.path.join(start, "启动遥控器.lnk"),
                exe_target, workdir=self._install_dir,
                icon=os.path.join(self._install_dir, "启动遥控器.ico"),
                desc=f"{self.APP_NAME} {self.APP_VERSION}",
            )

            # ★ 卸载快捷方式必须指向**包内解释器 + uninstall.py**，不能只指 uninstall.py：
            #   干净机器上没有 .py 的文件关联，双击一个 .py 只会弹"选择打开方式"，
            #   用户会以为卸载坏了。runtime\pythonw.exe 一定在（verify_payload 保证了）。
            un_py = os.path.join(self._install_dir, "uninstall.py")
            un_pyw = os.path.join(self._install_dir, "runtime", "pythonw.exe")
            if os.path.isfile(un_pyw):
                un_target, un_args = un_pyw, f'"{un_py}" --gui'
            else:
                venv_pyw = os.path.join(self._install_dir, ".venv", "Scripts", "pythonw.exe")
                if os.path.isfile(venv_pyw):
                    un_target, un_args = venv_pyw, f'"{un_py}" --gui'
                else:
                    un_target, un_args = un_py, "--gui"
            create_shortcut_wsh(
                os.path.join(start, "卸载 VibeMote.lnk"),
                un_target, args=un_args, workdir=self._install_dir,
                icon=os.path.join(self._install_dir, "启动遥控器.ico"),
                desc=f"卸载 {self.APP_NAME}",
            )

            # 最后才拉起官方虚拟声卡安装器（用户勾了才做）。
            # 本进程已经是管理员（spec 里 uac_admin=True），所以不用再弹 UAC。
            if self._cable_wanted.get() and not self._cable_installed:
                setup = cable_setup_path(self._install_dir)
                if os.path.isfile(setup):
                    try:
                        subprocess.Popen([setup], cwd=os.path.dirname(setup))
                        self._cable_launched = True
                    except Exception as e:
                        messagebox.showwarning(
                            "虚拟声卡安装器没能启动",
                            f"{e}\n\n装完本程序后，可以在客户端界面上点「安装虚拟声卡」重试。")

            self._stage = 4
            self._render()
        except Exception as e:
            messagebox.showerror("安装失败", str(e))
            self.btn_cancel.config(state="normal")
            self.btn_next.config(state="normal")


def main():
    root = tk.Tk()
    if not ensure_single_instance():
        root.withdraw()
        messagebox.showwarning(
            "安装向导已经在运行",
            "已经有一个安装向导窗口开着。\n\n"
            "两个向导同时往同一个目录复制文件，会互相把文件锁住"
            "（表现就是 [WinError 32] 卡在某个文件上）。\n\n"
            "请先切到那个窗口（任务栏上的“VibeMote 遥控器 安装向导”），"
            "或者关掉它再重新双击安装包。")
        root.destroy()
        return
    app = InstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

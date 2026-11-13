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
import subprocess
import sys
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
def install_files(src_root, dst_root, file_list, progress_cb=None):
    total = len(file_list)
    for i, (src, rel) in enumerate(file_list, 1):
        dst = os.path.join(dst_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        if progress_cb:
            progress_cb(i, total, rel)


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


def write_uninstall_info(dst_root, app_name="VibeMote 遥控器", version="1.3.1"):
    info = {"app_name": app_name, "version": version, "install_dir": dst_root,
            "installed_files": []}
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
    APP_VERSION = "1.3.1"
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

        self._build_banner()
        self.content = tk.Frame(self.root)
        self.content.pack(fill="both", expand=True, padx=24, pady=16)

        self._build_bottom()          # 先创建底部按钮，再显示首页（首页引用 btn_back）
        self._stage = 0
        self._install_dir = self._default_install_dir()
        self._show_welcome()

    def _default_install_dir(self):
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
        msg = (
            f"{self.APP_NAME} 已安装到：\n{self._install_dir}\n\n"
            "桌面和开始菜单里都有“启动遥控器”图标，双击即可启动。\n"
            "第一次启动后请做一件事：界面「设置」里点“扫描遥控器”，\n"
            "选中你自己的遥控器地址（安装包里没有预置任何地址）。"
        )
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

            install_files(src_root, self._install_dir, files, progress)

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
    app = InstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

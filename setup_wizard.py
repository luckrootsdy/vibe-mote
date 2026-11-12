# -*- coding: utf-8 -*-
"""VibeMote 遥控器 - Windows 安装向导。

用 PyInstaller --onefile --windowed 打包成单文件 exe，嵌入 .ico 图标。
界面顶部显示品牌 PNG 图片；安装时把程序文件（含 .venv 虚拟环境，
可在无 Python 电脑上运行）复制到用户选定的目录，并创建桌面 +
开始菜单快捷方式。

打包布局（安装包目录）：
  VibeMote_Setup.exe          <- 本向导（带图标）
  program_files/              <- 被安装的全部程序文件（含 .venv）
  assets/newtop_official2.png <- 向导顶部品牌图
  assets/newtop_official2.ico <- 向导图标（也复制到安装目录）

运行时：程序文件从 exe 同级目录读取；打包测试时若缺省则回退到开发目录。
"""
import json
import os
import shutil
import subprocess
import sys
import tkinter as tk
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
    """被安装的程序文件根目录（含 .venv）。"""
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
# 需要安装的文件列表
# 排除：客户端运行时产生的日志/状态/备份、.git、构建产物
# .venv 保留（其他电脑可能没有 Python，必须自带）
# ---------------------------------------------------------------------------
SKIP_DIRS = {".git", "build", "dist", "__pycache__", "_upgrade_backup_20261105"}
SKIP_NAMES = {
    "client.log",
    "client.log.20261109_pre_fix",
    "_last_heal", ".last_heal",
    "_ready.txt", "_run_state.txt", "_auto_recovery.json",
    "_app_pid.txt",
    ".gitattributes", ".gitignore",
}
SKIP_SUFFIXES = (".pyc", ".pyo")
SKIP_CONTAINS = ("bak_",)


def collect_program_files(root):
    """返回 [(src_abs, rel_path), ...] 需要安装的文件（全部文件，含 .venv）。"""
    result = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted([d for d in dn if d not in SKIP_DIRS and (d == ".venv" or not d.startswith("."))])
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


def write_uninstall_info(dst_root, app_name="VibeMote 遥控器", version="1.2.3"):
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
# ---------------------------------------------------------------------------
class InstallerApp:
    APP_NAME = "VibeMote 遥控器"
    APP_VERSION = "1.2.3"
    PUBLISHER = "VibeMote"

    def __init__(self, root):
        self.root = root
        self.root.title(f"{self.APP_NAME} 安装向导")
        self.root.resizable(False, False)
        self.root.geometry("640x480")

        self._build_banner()
        self.content = tk.Frame(self.root)
        self.content.pack(fill="both", expand=True, padx=24, pady=16)

        self._stage = 0
        self._install_dir = self._default_install_dir()
        self._show_welcome()
        self._build_bottom()

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

    def _show_welcome(self):
        self._clear_content()
        self.btn_back.config(state="disabled")
        self.btn_next.config(text="下一步 >", state="normal")
        self.btn_cancel.config(state="normal")
        tk.Label(self.content, text=self.APP_NAME, font=("Microsoft YaHei", 16, "bold")).pack(pady=(10, 4))
        tk.Label(self.content, text=f"版本 {self.APP_VERSION}", fg="#666666").pack(pady=(0, 16))
        txt = (
            "本向导将把 VibeMote 遥控器客户端安装到您的计算机。\n"
            "安装包已包含全部运行所需文件（含 Python 环境），\n"
            "无需在目标电脑上单独安装 Python。\n\n"
            "安装完成后，桌面和开始菜单会出现“启动遥控器”图标，\n"
            "双击即可启动，实现“拿起来就用”。\n\n"
            "点击“下一步”继续。"
        )
        tk.Label(self.content, text=txt, justify="left", wraplength=560).pack(pady=10)

    def _show_dir(self):
        self._clear_content()
        self.btn_back.config(state="normal")
        self.btn_next.config(text="安装", state="normal")
        tk.Label(self.content, text="选择安装位置", font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", pady=(0, 10))
        row = tk.Frame(self.content)
        row.pack(fill="x", pady=6)
        self.ent_dir = tk.Entry(row, width=52)
        self.ent_dir.insert(0, self._install_dir)
        self.ent_dir.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="浏览…", width=10, command=self._browse).pack(side="left", padx=(8, 0))
        hint = tk.Label(self.content, text="所需空间约 200 MB（含 Python 运行环境）。",
                        fg="#666666", wraplength=560, justify="left")
        hint.pack(anchor="w", pady=(12, 0))

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

    def _show_finish(self):
        self._clear_content()
        self.btn_back.config(state="disabled")
        self.btn_next.config(text="完成", state="normal")
        self.btn_cancel.config(state="normal")
        tk.Label(self.content, text="安装完成", font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", pady=(0, 10))
        msg = (
            f"{self.APP_NAME} 已成功安装到：\n{self._install_dir}\n\n"
            "您可以在桌面和开始菜单中找到“启动遥控器”图标，\n"
            "双击即可启动客户端。\n\n"
            "点击“完成”退出向导。"
        )
        tk.Label(self.content, text=msg, justify="left", wraplength=560).pack(pady=10)
        self.var_launch = tk.IntVar(value=1)
        tk.Checkbutton(self.content, text="现在启动 VibeMote", variable=self.var_launch).pack(anchor="w", pady=(8, 0))

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
        elif self._stage == 3:
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
            self._show_progress()
        elif self._stage == 3:
            self._show_finish()

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self._install_dir, title="选择安装文件夹")
        if d:
            self._install_dir = d
            self.ent_dir.delete(0, tk.END)
            self.ent_dir.insert(0, d)

    def _do_install(self):
        try:
            src_root = program_src_dir()
            files = collect_program_files(src_root)

            def progress(i, total, rel):
                self.pb["maximum"] = total
                self.pb["value"] = i
                self.lbl_file.config(text=rel)
                self.lbl_pct.config(text=f"{i * 100 // total}%")
                self.root.update_idletasks()

            install_files(src_root, self._install_dir, files, progress)
            write_uninstall_info(self._install_dir)

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
            create_shortcut_wsh(
                os.path.join(start, "卸载 VibeMote.lnk"),
                os.path.join(self._install_dir, "uninstall.py"),
                args="--gui", workdir=self._install_dir,
                desc=f"卸载 {self.APP_NAME}",
            )

            self._stage = 3
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

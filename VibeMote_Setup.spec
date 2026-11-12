# -*- mode: python ; coding: utf-8 -*-
# VibeMote 安装向导（VibeMote_Setup.exe）
# 打包：C:\Python312\python.exe -m PyInstaller --clean --noconfirm VibeMote_Setup.spec
#   （--distpath / --workpath 指到 D:\TEMP\opencode 下，别把 build/ dist/ 留在项目里）
#
# ★ uac_admin=True 是**必须**的，别去掉：
#   · 默认装到 C:\Program Files\… —— 写它本来就要管理员；
#   · VB-CABLE 是系统级音频驱动，装它也要管理员；
#   · 没有它的话，装到一半才抛 PermissionError，用户看到的是"安装失败"却不知道为什么。
#   代价只是启动时多一次 UAC 提示。

a = Analysis(
    ['setup_wizard.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VibeMote_Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['D:/01 AI/AI_RemoteMic/vibe-mote-new/vibe-mote-main_Win64_Setup/newtop_official2.ico'],
    uac_admin=True,
    # PE 版本信息：不挂这个，文件属性里"版本/产品名/公司"全是空的（2026-11-12 实测全空）
    version='D:/01 AI/AI_RemoteMic/vibe-mote-new/vibe-mote-main/version_setup.txt',
)

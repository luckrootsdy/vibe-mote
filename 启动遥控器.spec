# -*- mode: python ; coding: utf-8 -*-
# VibeMote 启动器（启动遥控器.exe）
# 打包：C:\Python312\python.exe -m PyInstaller --clean --noconfirm 启动遥控器.spec
#   （--distpath / --workpath 指到 D:\TEMP\opencode 下，别把 build/ dist/ 留在项目里）

a = Analysis(
    ['start_remote.py'],
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
    name='启动遥控器',
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
    # PE 版本信息：不挂这个，文件属性里"版本/产品名/公司"全是空的（2026-11-12 实测全空）
    version='D:/01 AI/AI_RemoteMic/vibe-mote-new/vibe-mote-main/version_launcher.txt',
)

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('configs', 'configs'), ('docs', 'docs')]
binaries = []
hiddenimports = ['drone', 'drone.app', 'drone.app.desktop', 'drone.app.service', 'drone.app.links', 'drone.app.commission', 'drone.app.config', 'drone.chain', 'drone.hive', 'drone.fast_lane', 'drone.clean_slate', 'drone.tools', 'drone.ollama_brain', 'drone.library_bridge', 'drone.controllers', 'drone.buzzer', 'drone.work_order', 'customtkinter']
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['G:/AI-Home/projects/ai-worker-drone-0.5b/drone/app/desktop.py'],
    pathex=['G:/AI-Home/projects/ai-worker-drone-0.5b'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name='DroneHive',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='DroneHive',
)

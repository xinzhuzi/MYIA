# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['myia.secrets']
hiddenimports += collect_submodules('myia')


a = Analysis(
    ['/Users/zhengbingjin/Project/Github/MYIA/desktop/entry.py'],
    pathex=[],
    binaries=[],
    datas=[('/Users/zhengbingjin/Project/Github/MYIA/myia-classifier/myia_classifier/data/keywords.json', 'myia_classifier/data')],
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
    a.binaries,
    a.datas,
    [],
    name='myia',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

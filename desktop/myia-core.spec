# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

# SPECPATH = spec 所在目录(desktop/);仓库根取其上一级——路径相对化,
# 任何机器 checkout 后 pyinstaller myia-core.spec 均可跑(不再依赖本机绝对路径)。
_REPO_ROOT = os.path.dirname(SPECPATH)
datas = [(os.path.join(_REPO_ROOT, 'myia-classifier', 'myia_classifier', 'data', 'keywords.json'), 'myia_classifier/data')]
binaries = []
hiddenimports = ['myia.secrets']
hiddenimports += collect_submodules('myia')
tmp_ret = collect_all('ocrmac')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('rapidocr_onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('openai')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    [os.path.join(SPECPATH, 'entry.py')],
    pathex=[],
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
    a.binaries,
    a.datas,
    [],
    name='myia-core',
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

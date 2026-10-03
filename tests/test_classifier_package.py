"""Standalone-package contract for myia-classifier (task 10-01-v10-classifier-pypi).

The classifier ships as its own zero-dependency distribution (PyPI name
``myia-classifier``, import name ``myia_classifier``); ``shishi.classify`` is
only a compatibility shim re-exporting its API. These tests pin the
standalone surface from the *installed* distribution: direct import and
classification, packaged keyword data, distribution metadata, zero runtime
dependencies, and shim identity (same objects, not copies).
"""

from __future__ import annotations

import pytest
from importlib import metadata
from pathlib import Path

import myia_classifier
from myia_classifier import classify_item, classify_title, load_table


def test_classify_item_standalone_import_and_classify():
    result = classify_item({"title": "羊毛速薅，注册送5000额度，签到1000到2000"})
    assert result.category == "token"
    assert result.matched, "命中追溯应随独立包一起提供"


def test_keyword_data_file_ships_inside_the_package():
    data_path = Path(myia_classifier.__file__).resolve().parent / "data" / "keywords.json"
    assert data_path.is_file(), f"关键词数据文件必须随包分发: {data_path}"
    table = load_table(data_path)
    assert classify_title("bybit普通用户的100% AI订阅返现将在10.5取消", table).category == "credit-card"


def test_distribution_metadata_version_and_trove_classifiers():
    dist = metadata.metadata("shishi-classifier")
    assert myia_classifier.__version__ == "0.0.1"
    assert metadata.version("shishi-classifier") == myia_classifier.__version__
    assert dist["License"] == "MIT"
    assert "License :: OSI Approved :: MIT License" in dist.get_all("Classifier") or []
    assert "Typing :: Typed" in (dist.get_all("Classifier") or [])


def test_distribution_declares_zero_runtime_dependencies():
    # PRD 铁律: myia-classifier 零重依赖(PyYAML 仅 load_rules 惰性引用,不列为依赖)
    assert metadata.requires("shishi-classifier") in (None, [])


def test_myia_classify_shim_reexports_standalone_objects():
    import shishi.classify as shim
    import shishi.classify.custom as shim_custom
    import myia_classifier.custom as real_custom

    assert shim.classify_item is myia_classifier.classify_item
    assert shim_custom._BIN_OPS is real_custom._BIN_OPS  # monkeypatch 必须改到同一张表


def test_built_wheel_contains_keyword_data_file(tmp_path):
    """真实构建 wheel 并断言 keywords.json 在产物内(防单点护栏失明)。

    pyproject 的 ``artifacts`` 是「数据文件进 wheel」的唯一承重防线(v0.2
    已踩过 wheel 缺文件的坑):hatch 配置或 .gitignore 否则规则一旦变动,
    缺数据的 wheel 会静默发布,所有 pip 用户首跑即 ClassifyDataError。
    本测试把「构建产物内容」变成 CI 可见断言;无 uv 的环境显式 skip
    (缺口如实暴露,不假装覆盖)。
    """
    import shutil
    import subprocess
    import zipfile

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv 不可用:无法在 CI 验证 wheel 产物内容(请安装 uv)")
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [uv, "build", str(repo_root / "myia-classifier"),
         "--out-dir", str(tmp_path)],
        check=True, capture_output=True,
    )
    wheels = list(tmp_path.glob("*.whl"))
    assert wheels, "uv build 未产出 wheel"
    with zipfile.ZipFile(wheels[0]) as bundle:
        names = bundle.namelist()
    data_files = [name for name in names if "/data/" in name and name.endswith(".json")]
    assert data_files, f"wheel 缺关键词数据文件(artifacts 防线失效?): {names[:10]}"

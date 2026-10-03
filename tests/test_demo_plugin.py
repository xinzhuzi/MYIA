"""开箱 demo 插件(plugins/myia-demo.yaml)的发布铁律(v1.1.1)。

demo 件是装机首跑「运行第一个插件」的数据来源(README 五屏截图同源),
发布口径(prd 10-03-v111-release 需求 1):
- 永远可通过真实 schema 入口加载(load_category_file,fail-fast);
- 零 ``env:`` / ``keychain:`` 引用 —— stdout 通道零凭据,明文/引用两头都不沾;
- classify.builtin 关闭直通 + push 仅 stdout 无 target(desktop/fixture 惯例)。
"""

from pathlib import Path

from shishi.schema import load_category_file

PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins"
DEMO_YAML = PLUGINS_DIR / "myia-demo.yaml"


def test_demo_plugin_loads_through_schema():
    """真实 schema 入口可加载:目录扫描(test_plugins)之外的专门钉子。"""
    config = load_category_file(DEMO_YAML)
    assert config.id == "myia-demo"
    assert config.sources, "demo 插件必须声明至少一个源"
    assert config.sources[0].engine == "direct_api"


def test_demo_plugin_has_zero_credential_references():
    """零凭据铁律:文件级零 ``env:``/``keychain:`` 引用(含注释,防回归)。"""
    text = DEMO_YAML.read_text(encoding="utf-8")
    assert "env:" not in text, "demo 件不允许出现 env: 引用(含注释措辞)"
    assert "keychain:" not in text, "demo 件不允许出现 keychain: 引用(含注释措辞)"


def test_demo_plugin_is_zero_credential_passthrough():
    """结构侧:builtin 分类关闭直通;push 仅 stdout 通道且无 target。"""
    config = load_category_file(DEMO_YAML)
    assert config.classify.builtin is False
    assert [push.channel for push in config.push] == ["stdout"]
    assert all(push.target is None for push in config.push)

"""yaml.* 编辑协议补充用例(工作流契约核对 low 级缺口收口,任务 10-03-yaml-editor)。

独立成文件:tests/test_desktop_sidecar_protocol.py 是多会话共享热点(混提交风险),
本文件只补三个已核实的覆盖缺口——明文凭据坏内容(prd AC3 子场景)、超长 stem、
save 侧 content 上限;夹具与断言惯例全部复用主文件(rpc / _editor_plugins /
EDITOR_YAML),跑法同门:uv run --no-sync python -m pytest tests/test_yaml_editor_protocol_gaps.py -q
"""

from test_desktop_sidecar_protocol import EDITOR_YAML, _editor_plugins, rpc

#: 在夹具源上注入无 env:/keychain: 前缀的 Authorization 头 → 触发凭据禁明文铁律
_PLAINTEXT = EDITOR_YAML.replace(
    '    url: "http://127.0.0.1:9/x"\n',
    '    url: "http://127.0.0.1:9/x"\n    headers:\n      Authorization: "Bearer plaintext-no-ref"\n',
)


def test_yaml_validate_plaintext_credential_is_error_finding(tmp_path, monkeypatch):
    """明文凭据 → validate 出 error 级 credential_plaintext,干跑零写入。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("demo.yaml", EDITOR_YAML))
    _, responses, _ = rpc({"id": 1, "method": "yaml.validate",
                           "params": {"file": str(plugins / "demo.yaml"), "content": _PLAINTEXT}})
    result = responses[0]["result"]
    assert result["valid"] is False
    codes = [(finding["code"], finding["level"]) for finding in result["findings"]]
    assert ("credential_plaintext", "error") in codes
    assert (plugins / "demo.yaml").read_text(encoding="utf-8") == EDITOR_YAML


def test_yaml_save_plaintext_credential_zero_write(tmp_path, monkeypatch):
    """同内容走 save → category_invalid 明细含 credential_plaintext,目标零变更。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("demo.yaml", EDITOR_YAML))
    path = plugins / "demo.yaml"
    before = path.read_text(encoding="utf-8")
    _, responses, _ = rpc({"id": 1, "method": "yaml.save",
                           "params": {"file": str(path), "content": _PLAINTEXT,
                                      "expected_mtime": path.stat().st_mtime}})
    error = responses[0]["error"]
    assert error["code"] == "category_invalid"
    assert any(item["code"] == "credential_plaintext" for item in error["data"]["errors"])
    assert path.read_text(encoding="utf-8") == before


def test_yaml_create_stem_over_64_rejected(tmp_path, monkeypatch):
    """新建 stem 超长(品类 id 正则上限 64)→ invalid_file_stem,文件不出现。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("demo.yaml", EDITOR_YAML))
    long_name = "a" * 65 + ".yaml"
    _, responses, _ = rpc({"id": 1, "method": "yaml.save",
                           "params": {"file": str(plugins / long_name), "content": EDITOR_YAML,
                                      "expected_mtime": None}})
    assert responses[0]["error"]["code"] == "invalid_file_stem"
    assert not (plugins / long_name).exists()


def test_yaml_save_content_over_1mib_rejected(tmp_path, monkeypatch):
    """save 侧对称上限:content > 1 MiB → file_too_large,目标零变更。"""
    plugins = _editor_plugins(tmp_path, monkeypatch, ("demo.yaml", EDITOR_YAML))
    path = plugins / "demo.yaml"
    before = path.read_text(encoding="utf-8")
    _, responses, _ = rpc({"id": 1, "method": "yaml.save",
                           "params": {"file": str(path), "content": "x" * (1024 * 1024 + 1),
                                      "expected_mtime": path.stat().st_mtime}})
    assert responses[0]["error"]["code"] == "file_too_large"
    assert path.read_text(encoding="utf-8") == before

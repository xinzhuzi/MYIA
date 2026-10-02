"""Tests for the keychain credential store (PRD 10-01-v02-secrets-keychain).

Covers the acceptance list: canonical ``myia/<scope>/<name>`` namespace
validation / set-get-delete-list round trips on the injectable mock backend /
env: + keychain: resolution through ``resolve_credential`` (incl. Bearer scheme
round-trip) / structured errors for missing backend (Linux 服务器回退引导) /
plaintext refusal with one sample per credential position (headers,
post_body, engine_options, push.target) / engine construction expanding
keychain headers via ``FetchContext.keychain_backend``.

Windows DPAPI shares the keyring code path and is condition-tested in
:class:`TestWindowsDPAPI` (skipped off-Windows). All unit tests run on
:class:`InMemoryKeychainBackend` — 零真实钥匙串触碰,真实往返见任务日志.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from myia import secrets as secrets_store
from myia.engines.fetch_base import BaseEngine, FetchContext, resolve_headers
from myia.schema import (
    CredentialResolveError,
    LoadError,
    load_category,
    resolve_credential,
)
from myia.secrets import (
    INDEX_ACCOUNT,
    SECRET_SERVICE,
    InMemoryKeychainBackend,
    SecretError,
    delete_secret,
    get_backend,
    get_secret,
    list_secrets,
    resolve_keychain_ref,
    set_secret,
    validate_secret_name,
)

from conftest import make_client, make_source

CANONICAL_NAME = "myia/selftest/cookie"


# ---------------------------------------------------------------------------
# Fixtures (每个测试独立后端,零共享状态)
# ---------------------------------------------------------------------------


@pytest.fixture()
def backend() -> InMemoryKeychainBackend:
    """A fresh in-memory backend per test (mock 钥匙串)."""
    return InMemoryKeychainBackend()


@pytest.fixture()
def default_backend() -> InMemoryKeychainBackend:
    """Backend injected into the module slot; reset afterwards (不泄漏到别的测试)."""
    injected = InMemoryKeychainBackend()
    secrets_store.set_backend(injected)
    yield injected
    secrets_store.reset_backend()


def category_data(**overrides: Any) -> dict[str, Any]:
    """A minimal valid category config with keyword overrides."""
    data: dict[str, Any] = {
        "id": "demo",
        "name": "演示品类",
        "schedule": "0 9 * * *",
        "sources": [
            {
                "name": "demo-source",
                "engine": "direct_api",
                "url": "https://example.com/api",
                "extract": {"type": "json_path", "fields": {"url": "$.url", "title": "$.title"}},
            }
        ],
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Name validation: myia/<scope>/<name> 命名空间
# ---------------------------------------------------------------------------


class TestValidateSecretName:
    def test_validate_secret_name_canonical_form_passes(self):
        assert validate_secret_name("myia/stocks/linuxsb_cookie") == "myia/stocks/linuxsb_cookie"

    def test_validate_secret_name_allows_dots_dashes_underscore_in_name(self):
        assert validate_secret_name("myia/monitor/X-Api-Key.2") == "myia/monitor/X-Api-Key.2"

    def test_validate_secret_name_rejects_flat_legacy_name(self):
        with pytest.raises(SecretError) as excinfo:
            validate_secret_name("linuxsb_cookie")
        assert excinfo.value.code == "invalid_secret_name"
        assert "myia/<scope>/<name>" in str(excinfo.value)

    def test_validate_secret_name_rejects_missing_scope_segment(self):
        with pytest.raises(SecretError) as excinfo:
            validate_secret_name("myia/only-two-parts")
        assert excinfo.value.code == "invalid_secret_name"

    def test_validate_secret_name_rejects_uppercase_scope(self):
        with pytest.raises(SecretError) as excinfo:
            validate_secret_name("myia/Stocks/cookie")
        assert excinfo.value.code == "invalid_secret_name"

    def test_validate_secret_name_rejects_non_string(self):
        with pytest.raises(SecretError) as excinfo:
            validate_secret_name(123)  # type: ignore[arg-type]
        assert excinfo.value.code == "invalid_secret_name"


# ---------------------------------------------------------------------------
# set / get / delete / list 往返(mock 后端)
# ---------------------------------------------------------------------------


class TestSecretCrud:
    def test_set_then_get_returns_value(self, backend):
        set_secret(CANONICAL_NAME, "cookie-value", backend=backend)
        assert get_secret(CANONICAL_NAME, backend=backend) == "cookie-value"

    def test_set_overwrites_existing_value(self, backend):
        set_secret(CANONICAL_NAME, "old", backend=backend)
        set_secret(CANONICAL_NAME, "new", backend=backend)
        assert get_secret(CANONICAL_NAME, backend=backend) == "new"

    def test_set_invalid_name_raises_structured(self, backend):
        with pytest.raises(SecretError) as excinfo:
            set_secret("flat_name", "v", backend=backend)
        assert excinfo.value.code == "invalid_secret_name"

    def test_get_missing_raises_secret_not_found_with_guidance(self, backend):
        with pytest.raises(SecretError) as excinfo:
            get_secret(CANONICAL_NAME, backend=backend)
        assert excinfo.value.code == "secret_not_found"
        assert "myia secret set" in str(excinfo.value)
        assert "env:" in str(excinfo.value)

    def test_delete_removes_item_and_second_delete_reports_not_found(self, backend):
        set_secret(CANONICAL_NAME, "v", backend=backend)
        delete_secret(CANONICAL_NAME, backend=backend)
        with pytest.raises(SecretError) as excinfo:
            get_secret(CANONICAL_NAME, backend=backend)
        assert excinfo.value.code == "secret_not_found"
        with pytest.raises(SecretError) as excinfo:
            delete_secret(CANONICAL_NAME, backend=backend)
        assert excinfo.value.code == "secret_not_found"

    def test_delete_missing_raises_secret_not_found(self, backend):
        with pytest.raises(SecretError) as excinfo:
            delete_secret(CANONICAL_NAME, backend=backend)
        assert excinfo.value.code == "secret_not_found"


class TestSecretList:
    def test_list_returns_sorted_names_after_set(self, backend):
        set_secret("myia/demo/b", "2", backend=backend)
        set_secret("myia/demo/a", "1", backend=backend)
        assert list_secrets(backend=backend) == ["myia/demo/a", "myia/demo/b"]

    def test_list_empty_on_fresh_backend(self, backend):
        assert list_secrets(backend=backend) == []

    def test_delete_removes_name_from_index(self, backend):
        set_secret("myia/demo/a", "1", backend=backend)
        set_secret("myia/demo/b", "2", backend=backend)
        delete_secret("myia/demo/a", backend=backend)
        assert list_secrets(backend=backend) == ["myia/demo/b"]

    def test_list_reconciles_stale_index_entries(self, backend):
        """Keychain Access 手删的项:索引里还在、钥匙链里没了 -> list 剔除并自愈."""
        set_secret("myia/demo/a", "1", backend=backend)
        set_secret("myia/demo/ghost", "gone", backend=backend)
        backend.delete_password(SECRET_SERVICE, "myia/demo/ghost")
        assert list_secrets(backend=backend) == ["myia/demo/a"]
        # 自愈:索引里 ghost 已被剔除
        assert json.loads(backend.get_password(SECRET_SERVICE, INDEX_ACCOUNT)) == ["myia/demo/a"]

    def test_list_with_corrupt_index_self_heals_to_empty(self, backend):
        backend.set_password(SECRET_SERVICE, INDEX_ACCOUNT, "not-json{")
        assert list_secrets(backend=backend) == []

    def test_index_lives_inside_keychain_not_on_disk(self, backend):
        """索引是钥匙链内的一项(名字可入索引,值永不)。"""
        set_secret(CANONICAL_NAME, "v", backend=backend)
        assert backend.get_password(SECRET_SERVICE, CANONICAL_NAME) == "v"
        index = json.loads(backend.get_password(SECRET_SERVICE, INDEX_ACCOUNT))
        assert set(index) == {CANONICAL_NAME}


# ---------------------------------------------------------------------------
# 后端注入与发现
# ---------------------------------------------------------------------------


class TestBackendDiscovery:
    def test_set_backend_injects_override_until_reset(self):
        injected = InMemoryKeychainBackend()
        secrets_store.set_backend(injected)
        try:
            assert get_backend() is injected
        finally:
            secrets_store.reset_backend()

    def test_get_backend_raises_structured_when_keyring_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "keyring", None)  # import keyring -> ImportError
        secrets_store.reset_backend()
        with pytest.raises(SecretError) as excinfo:
            get_backend()
        assert excinfo.value.code == "keychain_backend_unavailable"
        assert "env:" in str(excinfo.value)  # 回退引导: env: 为主

    def test_backend_operation_failure_wraps_as_keychain_operation_failed(self):
        class LockedBackend:
            """模拟钥匙链被锁/无后端等底层故障。"""

            def get_password(self, service: str, username: str) -> str | None:
                raise OSError("keychain locked")

            def set_password(self, service: str, username: str, password: str) -> None:
                raise OSError("keychain locked")

            def delete_password(self, service: str, username: str) -> None:
                raise OSError("keychain locked")

        locked = LockedBackend()
        with pytest.raises(SecretError) as set_exc:
            set_secret(CANONICAL_NAME, "v", backend=locked)
        assert set_exc.value.code == "keychain_operation_failed"
        with pytest.raises(SecretError) as get_exc:
            get_secret(CANONICAL_NAME, backend=locked)
        assert get_exc.value.code == "keychain_operation_failed"


# ---------------------------------------------------------------------------
# resolve_keychain_ref(schema 层入口)
# ---------------------------------------------------------------------------


class TestResolveKeychainRef:
    def test_resolve_keychain_ref_returns_value(self, backend):
        set_secret(CANONICAL_NAME, "cookie-value", backend=backend)
        assert resolve_keychain_ref(CANONICAL_NAME, backend=backend) == "cookie-value"

    def test_resolve_keychain_ref_missing_raises_secret_not_found(self, backend):
        with pytest.raises(SecretError) as excinfo:
            resolve_keychain_ref(CANONICAL_NAME, backend=backend)
        assert excinfo.value.code == "secret_not_found"


# ---------------------------------------------------------------------------
# 三态解析:env: / keychain: 经 resolve_credential(默认后端注入)
# ---------------------------------------------------------------------------


class TestResolveCredentialKeychain:
    def test_resolve_credential_keychain_ref_returns_secret(self, default_backend):
        default_backend.set_password(SECRET_SERVICE, CANONICAL_NAME, "cookie-value")
        assert resolve_credential(f"keychain:{CANONICAL_NAME}") == "cookie-value"

    def test_resolve_credential_keychain_bearer_scheme_round_trips(self, default_backend):
        default_backend.set_password(SECRET_SERVICE, CANONICAL_NAME, "cookie-value")
        assert resolve_credential(f"Bearer keychain:{CANONICAL_NAME}") == "Bearer cookie-value"

    def test_resolve_credential_keychain_missing_reports_secret_not_found(self, default_backend):
        with pytest.raises(CredentialResolveError) as excinfo:
            resolve_credential("keychain:myia/selftest/missing")
        assert excinfo.value.code == "secret_not_found"

    def test_resolve_credential_keychain_flat_legacy_name_reports_invalid_namespace(
        self, default_backend
    ):
        with pytest.raises(CredentialResolveError) as excinfo:
            resolve_credential("keychain:linuxsb_cookie")
        assert excinfo.value.code == "invalid_secret_name"
        assert "myia/<scope>/<name>" in str(excinfo.value)

    def test_resolve_credential_keychain_backend_unavailable_is_structured(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "keyring", None)
        secrets_store.reset_backend()
        with pytest.raises(CredentialResolveError) as excinfo:
            resolve_credential("keychain:myia/selftest/cookie")
        assert excinfo.value.code == "keychain_backend_unavailable"
        assert "env:" in str(excinfo.value)

    def test_resolve_credential_env_ref_still_resolves_from_environment(self, monkeypatch):
        monkeypatch.setenv("MYIA_SECRETS_TEST_TOKEN", "env-value")
        assert resolve_credential("env:MYIA_SECRETS_TEST_TOKEN") == "env-value"

    def test_resolve_credential_env_missing_reports_env_var_missing(self, monkeypatch):
        monkeypatch.delenv("MYIA_SECRETS_TEST_MISSING", raising=False)
        with pytest.raises(CredentialResolveError) as excinfo:
            resolve_credential("env:MYIA_SECRETS_TEST_MISSING")
        assert excinfo.value.code == "env_var_missing"

    def test_resolve_credential_plain_value_reports_invalid_credential_ref(self):
        with pytest.raises(CredentialResolveError) as excinfo:
            resolve_credential("sessionid=abc123")
        assert excinfo.value.code == "invalid_credential_ref"


# ---------------------------------------------------------------------------
# YAML 加载:命名空间 keychain: 引用是合法语法;明文凭据按凭据位逐一拒跑
# ---------------------------------------------------------------------------


class TestLoadTimeCredentialRefs:
    def test_namespaced_keychain_refs_load(self):
        data = category_data(
            sources=[
                {
                    "name": "demo-source",
                    "url": "https://example.com/api",
                    "headers": {"Cookie": "keychain:myia/stocks/linuxsb_cookie"},
                    "extract": {"type": "json_path", "fields": {"url": "$.u", "title": "$.t"}},
                }
            ],
            push=[{"channel": "telegram", "target": "keychain:myia/stocks/bot_token"}],
        )
        config = load_category(data)
        assert config.sources[0].headers["Cookie"] == "keychain:myia/stocks/linuxsb_cookie"
        assert config.push[0].target == "keychain:myia/stocks/bot_token"

    def test_plaintext_header_refused(self):
        data = category_data()
        data["sources"][0]["headers"] = {"Cookie": "sessionid=abc123"}
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "credential_plaintext"
        assert detail.path.endswith("headers.Cookie")

    def test_plaintext_post_body_refused(self):
        data = category_data()
        data["sources"][0].update(
            {
                "method": "POST",
                "post_body": {"username": "u", "password": "hunter2"},
            }
        )
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "credential_plaintext"
        assert "post_body.password" in detail.path

    def test_plaintext_engine_options_refused(self):
        data = category_data()
        data["sources"][0]["engine_options"] = {"firecrawl": {"api_key": "fc-plain-value"}}
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "credential_plaintext"
        assert "engine_options.firecrawl.api_key" in detail.path

    def test_plaintext_source_extension_param_refused(self):
        """源级扩展参数(engine_options 之外)同样在明文检查面内。"""
        data = category_data()
        data["sources"][0]["access_secret"] = "topsecret"
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "credential_plaintext"
        assert "access_secret" in detail.path

    def test_plaintext_push_target_refused(self):
        data = category_data()
        data["push"] = [{"channel": "telegram", "target": "123456:PLAIN-TOKEN"}]
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "credential_plaintext"
        assert detail.path.endswith("push[0].target")

    def test_plaintext_enrich_base_url_refused(self):
        """enrich 端点凭据位(v0.2 实装):base_url/api_key 只许 env:/keychain: 引用。"""
        data = category_data()
        data["enrich"] = {"enabled": True, "base_url": "https://api.llm.example/v1"}
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "credential_plaintext"
        assert detail.path.endswith("enrich.base_url")

    def test_plaintext_enrich_api_key_refused(self):
        data = category_data()
        data["enrich"] = {"enabled": True, "base_url": "env:MYIA_LLM_BASE", "api_key": "sk-plain-key"}
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        detail = excinfo.value.errors[0]
        assert detail.error_type == "credential_plaintext"
        assert detail.path.endswith("enrich.api_key")

    def test_enrich_endpoint_refs_load(self):
        """规范引用合法加载(与 resolve 期 EnrichSettings 契约衔接)。"""
        data = category_data()
        data["enrich"] = {
            "enabled": True,
            "base_url": "env:MYIA_LLM_BASE",
            "api_key": "keychain:myia/enrich/glm_key",
        }
        config = load_category(data)
        assert config.enrich.base_url == "env:MYIA_LLM_BASE"
        assert config.enrich.api_key == "keychain:myia/enrich/glm_key"

    def test_plugin_node_with_plaintext_token_is_refused_wholesale(self):
        """plugin remote token 凭据位(v0.3 已建模):明文 token 加载期带字段路径
        拒载(credential_plaintext @ $.plugin.modes.remote.token)——secrets-keychain
        PRD 验收第 3 条;v0.2 的「整节 unknown_field fail-closed」按本测试钉住的
        升级路径兑现为精确到字段的结构化检查。"""
        data = category_data()
        data["plugin"] = {"modes": {"remote": {"token": "ghp_PLAINTEXT_TOKEN"}}}
        with pytest.raises(LoadError) as excinfo:
            load_category(data)
        detail = next(
            detail
            for detail in excinfo.value.errors
            if detail.path == "$.plugin.modes.remote.token"
        )
        assert detail.error_type == "credential_plaintext"


# ---------------------------------------------------------------------------
# fetch_base:引擎构造期经注入后端展开 keychain 头
# ---------------------------------------------------------------------------


def _never_requested(request: Any) -> Any:
    pytest.fail("不应发起任何请求")


class TestFetchBaseKeychainResolution:
    def test_resolve_headers_expands_keychain_ref_via_injected_backend(self, backend):
        set_secret(CANONICAL_NAME, "cookie-value", backend=backend)
        resolved = resolve_headers(
            {
                "Cookie": f"keychain:{CANONICAL_NAME}",
                "Authorization": f"Bearer keychain:{CANONICAL_NAME}",
                "User-Agent": "UA/1.0",
            },
            backend=backend,
        )
        assert resolved["Cookie"] == "cookie-value"
        assert resolved["Authorization"] == "Bearer cookie-value"
        assert resolved["User-Agent"] == "UA/1.0"

    def test_resolve_headers_keychain_missing_is_structured(self, backend):
        with pytest.raises(CredentialResolveError) as excinfo:
            resolve_headers({"Cookie": f"keychain:{CANONICAL_NAME}"}, backend=backend)
        assert excinfo.value.code == "secret_not_found"

    def test_engine_construction_expands_keychain_header_from_context_backend(self, backend):
        set_secret(CANONICAL_NAME, "cookie-value", backend=backend)
        source = make_source(headers={"Cookie": f"keychain:{CANONICAL_NAME}"})
        context = FetchContext(client=make_client(_never_requested), keychain_backend=backend)
        engine = BaseEngine(source, context)
        assert engine._headers["Cookie"] == "cookie-value"

    def test_engine_construction_without_backend_uses_module_default(self, default_backend):
        default_backend.set_password(SECRET_SERVICE, CANONICAL_NAME, "cookie-value")
        source = make_source(headers={"Cookie": f"keychain:{CANONICAL_NAME}"})
        context = FetchContext(client=make_client(_never_requested))
        engine = BaseEngine(source, context)
        assert engine._headers["Cookie"] == "cookie-value"


# ---------------------------------------------------------------------------
# Windows DPAPI 代码路径(同一 keyring 抽象;仅 Windows 实机运行,CI 跳过)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI 代码路径,仅 Windows 实机可验")
class TestWindowsDPAPI:
    def test_windows_backend_set_get_delete_list_round_trip(self):
        backend = get_backend()
        name = "myia/selftest/win_probe"
        set_secret(name, "probe-value", backend=backend)
        try:
            assert get_secret(name, backend=backend) == "probe-value"
            assert name in list_secrets(backend=backend)
        finally:
            delete_secret(name, backend=backend)
        with pytest.raises(SecretError) as excinfo:
            get_secret(name, backend=backend)
        assert excinfo.value.code == "secret_not_found"

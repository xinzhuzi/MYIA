"""myia-credhunter 插件密钥库(keystore:猎→存→验通路)契约测试。

任务 10-03-aipocket-fusion 插件侧 keystore 配套测试:零网络(ghhunt 走
注入的 httpx.AsyncClient + 异步 MockTransport;run_from_keystore 走注入
transport + 注入 Pacer)、零真实凭据(fixture 均为人工合成脱敏键,不对应
任何真实账号)、零真等待。

钉住的契约:

1. **keystore 往返**:record→load 字段齐备(version/records 结构、
   apikey/provider/first_seen/last_seen/hit_count/sources/last_check/
   check_state)、hit_count 累计与 sources 去重、sources 上限 20、文件
   600 权限、原子写不残留 tmp、update_check 回填、坏文件结构化错;
2. **红线断言(Q9)**:ghhunt 产出的 item 序列化(json.dumps)后不含
   全文密钥串,keystore 文件里有(全文只进库,数据域不出本机);
   未注入 keystore 的 hunt 零落盘;适配器面缺省注入走 MYIA_HOME;
3. **run_from_keystore**:注入 transport 三态回填(final_verified/
   rejected/transient → check_state/last_check)、Q7 balance 默认关/
   显式开、limit 截断(未验条目不动)、Q8 串行(共享 Pacer 按供应商
   组 wait)、返回值掩码-only;
4. **路径解析**:``MYIA_HOME`` 环境变量优先、cwd 兜底;适配器
   ``run_credcheck_keystore`` 缺省路径与结构化错误面。

测试纪律:全部模块经 ``myia.cli._import_plugin_adapter`` 加载(compile+
exec,与宿主同款);registry 用 adapter.specs 的真实数据文件(25 规格)。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from myia.cli import _import_plugin_adapter

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = REPO_ROOT / "plugins"

# ---------------------------------------------------------------------------
# 合成脱敏 fixture 键(人工构造,不对应任何真实账号;形态只服务于归因分支)
# ---------------------------------------------------------------------------
#: xai 前缀键(ghhunt 指纹库可命中;provider=xai)。
KEY_XAI = "xai-Hs5vBn8mKj2wQr7tYc3f"
#: openai 官方前缀键(specs 前缀归因 → https://api.openai.com/v1)。
KEY_LIVE = "sk-proj-live0q7wXc5vNk2mJb4tYd"
KEY_DEAD = "sk-proj-dead0q7wXc5vNk2mJb4tYe"
KEY_SICK = "sk-proj-sick0q7wXc5vNk2mJb4tYf"
#: 不可归因键(unknown → no_api_url,零网络即可 rejected)。
KEY_OPAQUE = "opaque-synth-key-0q7wXc5vNk2m"

EVIDENCE_URL = "https://github.com/octo/demo/blob/bl0b11/config/.env"
MODELS_URL = "https://api.openai.com/v1/models"
GRANTS_URL = "https://api.openai.com/dashboard/billing/credit_grants"

_ADAPTER: Any = None


def adapter() -> Any:
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = _import_plugin_adapter(PLUGINS_DIR, "myia-credhunter")
    return _ADAPTER


@pytest.fixture()
def ks() -> Any:
    return adapter().keystore


@pytest.fixture()
def fnd() -> Any:
    return adapter().findings


@pytest.fixture()
def cc() -> Any:
    return adapter().credcheck


@pytest.fixture()
def registry() -> Any:
    return adapter().specs.ProviderResolver(adapter().specs.load_specs())


@pytest.fixture()
def store_path(tmp_path: Path) -> Path:
    """tmp 下的密钥库路径(测试一律显式注入,永不落仓库根)。"""
    return tmp_path / "credhunter-keystore.json"


# ---------------------------------------------------------------------------
# 测试替身:RecordingPacer(零等待 + 记录组名)与 FakeTransport(零网络)
# ---------------------------------------------------------------------------


class RecordingPacer:
    """零等待限速替身:记录每次请求前的 wait(组名)供 Q8 串行断言。"""

    def __init__(self) -> None:
        self.waits: list[str] = []

    def wait(self, group: str) -> None:
        self.waits.append(group)


class FakeTransport:
    """按 (method, url) 顺序消费的假传输:命中即弹一条路由,未命中 404。"""

    def __init__(self, routes: list[tuple[str, str, int, str]] | None = None) -> None:
        self.routes: list[tuple[str, str, int, str]] = list(routes or [])
        self.calls: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Any = None,
        json_body: Any = None,
    ) -> Any:
        self.calls.append((method, url))
        for index, (want_method, want_url, status, body) in enumerate(self.routes):
            if want_method == method and want_url == url:
                self.routes.pop(index)
                return cc_module().HttpResponse(status_code=status, body=body, headers={})
        return cc_module().HttpResponse(status_code=404, body='{"error":"route_miss"}', headers={})


def cc_module() -> Any:
    return adapter().credcheck


def seed_store(ks_module: Any, fnd_module: Any, path: Path, keys: tuple[str, ...]) -> None:
    """按声明顺序把合成键入库(openai 归因,证据 URL 单一)。"""
    store = ks_module.Keystore(path)
    for key in keys:
        store.record(fnd_module.key_fingerprint(key), key, "openai", EVIDENCE_URL)


# ---------------------------------------------------------------------------
# 契约一:keystore 往返
# ---------------------------------------------------------------------------


class TestKeystoreRoundtrip:
    def test_record_then_load_fields_complete(self, ks, fnd, store_path):
        store = ks.Keystore(store_path)
        fingerprint = fnd.key_fingerprint(KEY_XAI)
        store.record(fingerprint, KEY_XAI, "xai", EVIDENCE_URL)
        records = store.load()
        assert set(records) == {fingerprint}
        record = records[fingerprint]
        assert record["apikey"] == KEY_XAI
        assert record["provider"] == "xai"
        assert record["first_seen"] == record["last_seen"]
        assert record["hit_count"] == 1
        assert record["sources"] == [EVIDENCE_URL]
        assert record["last_check"] is None
        assert record["check_state"] is None

    def test_hit_count_accumulates_and_sources_dedupe(self, ks, fnd, store_path):
        store = ks.Keystore(store_path)
        fingerprint = fnd.key_fingerprint(KEY_XAI)
        other_url = "https://github.com/octo/demo/commit/c0ffee"
        store.record(fingerprint, KEY_XAI, "xai", EVIDENCE_URL)
        store.record(fingerprint, KEY_XAI, "xai", EVIDENCE_URL)  # 同 URL 去重
        store.record(fingerprint, KEY_XAI, "xai", other_url)
        record = store.load()[fingerprint]
        assert record["hit_count"] == 3
        assert record["sources"] == [EVIDENCE_URL, other_url]  # 保序去重

    def test_sources_cap_at_twenty(self, ks, fnd, store_path):
        store = ks.Keystore(store_path)
        fingerprint = fnd.key_fingerprint(KEY_XAI)
        for index in range(25):
            store.record(fingerprint, KEY_XAI, "xai", f"https://example.test/h{index}")
        record = store.load()[fingerprint]
        assert len(record["sources"]) == ks.SOURCES_LIMIT  # 前 20 条,超限丢弃

    def test_persisted_shape_and_0600_no_tmp_residue(self, ks, fnd, store_path):
        store = ks.Keystore(store_path)
        store.record(fnd.key_fingerprint(KEY_XAI), KEY_XAI, "xai", EVIDENCE_URL)
        store.record(fnd.key_fingerprint(KEY_LIVE), KEY_LIVE, "openai", EVIDENCE_URL)
        raw = json.loads(store_path.read_text(encoding="utf-8"))
        assert raw["version"] == 1
        assert set(raw["records"]) == {fnd.key_fingerprint(KEY_XAI), fnd.key_fingerprint(KEY_LIVE)}
        if os.name == "posix":  # chmod 600(posix 语义)
            assert stat.S_IMODE(os.stat(store_path).st_mode) == 0o600
        assert list(store_path.parent.glob("*.tmp")) == []  # 原子写不残留 tmp

    def test_nested_parent_created(self, ks, tmp_path):
        nested = tmp_path / "myia-home" / "credhunter-keystore.json"
        ks.Keystore(nested).record("fp-1", "value", "unknown", "")
        assert nested.is_file()

    def test_update_check_backfills(self, ks, fnd, store_path):
        store = ks.Keystore(store_path)
        fingerprint = fnd.key_fingerprint(KEY_XAI)
        store.record(fingerprint, KEY_XAI, "xai", EVIDENCE_URL)
        store.update_check(fingerprint, "final_verified")
        record = store.load()[fingerprint]
        assert record["check_state"] == "final_verified"
        assert isinstance(record["last_check"], str) and record["last_check"]
        assert record["hit_count"] == 1  # 回填不动猎取侧计数

    def test_update_check_unknown_fingerprint_structured(self, ks, store_path):
        store = ks.Keystore(store_path)
        with pytest.raises(ks.KeystoreError) as excinfo:
            store.update_check("sha256-nonexistent", "rejected")
        assert excinfo.value.code == "fingerprint_unknown"

    def test_invalid_json_structured_error(self, ks, store_path):
        store_path.write_text("{not-json", encoding="utf-8")
        with pytest.raises(ks.KeystoreError) as excinfo:
            ks.Keystore(store_path).load()
        assert excinfo.value.code == "keystore_invalid"

    def test_wrong_version_structured_error(self, ks, store_path):
        store_path.write_text(json.dumps({"version": 99, "records": {}}), encoding="utf-8")
        with pytest.raises(ks.KeystoreError) as excinfo:
            ks.Keystore(store_path).load()
        assert excinfo.value.code == "keystore_invalid"


# ---------------------------------------------------------------------------
# 契约二:路径解析(MYIA_HOME 优先 / cwd 兜底)
# ---------------------------------------------------------------------------


class TestDefaultPathResolution:
    def test_myia_home_env_wins(self, ks, monkeypatch, tmp_path):
        monkeypatch.setenv("MYIA_HOME", str(tmp_path))
        assert ks.default_keystore_path() == tmp_path / "credhunter-keystore.json"

    def test_cwd_fallback(self, ks, monkeypatch, tmp_path):
        monkeypatch.delenv("MYIA_HOME", raising=False)
        monkeypatch.chdir(tmp_path)
        assert ks.default_keystore_path() == tmp_path / "credhunter-keystore.json"


# ---------------------------------------------------------------------------
# 契约三:ghhunt 命中入库 + Q9 红线(items 序列化零全文,库里有全文)
# ---------------------------------------------------------------------------


class _AsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler: Callable[[httpx.Request], Any]) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._handler(request)


def make_client(handler: Callable[[httpx.Request], Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_AsyncTransport(handler))


def _json_response(payload: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _code_entry() -> dict[str, Any]:
    """一条公开仓库 code 搜索命中(.env 路径,text_matches 埋合成键)。"""
    return {
        "name": ".env",
        "path": "config/.env",
        "sha": "bl0b11",
        "html_url": EVIDENCE_URL,
        "repository": {"full_name": "octo/demo", "private": False},
        "text_matches": [{"fragment": f'x = "{KEY_XAI}"', "object_url": EVIDENCE_URL}],
    }


def _blob_payload() -> dict[str, Any]:
    raw = f'x = "{KEY_XAI}"'.encode("utf-8")
    return {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64", "size": len(raw)}


async def _github_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/search/code":
        return _json_response({"items": [_code_entry()]})
    if request.url.path.startswith("/repos/octo/demo/git/blobs/"):
        return _json_response(_blob_payload())
    if request.url.path == "/search/commits":
        return _json_response({"items": []})
    return _json_response({})


def run_hunt(**kwargs: Any) -> dict[str, Any]:
    gh = adapter().ghhunt

    async def _go() -> dict[str, Any]:
        async with make_client(_github_handler) as client:
            return await gh.hunt(client=client, **kwargs)

    return asyncio.run(_go())


class TestHuntKeystore:
    def test_items_masked_keystore_holds_full_key(self, ks, fnd, store_path):
        """红线:items 序列化后零全文密钥;全文只在密钥库(库文件里有)。"""
        store = ks.Keystore(store_path)
        result = run_hunt(tokens=["tok-test"], queries=["sk- filename:.env"], keystore=store)
        assert result["items"], "合成 fixture 应产出命中"
        for item in result["items"]:
            assert KEY_XAI not in json.dumps(item, ensure_ascii=False), f"全文密钥漏进 item:{item}"
            assert fnd.find_full_key_leak(item, KEY_XAI) == []
        # 库里有全文:同键两路命中(泳道扫描 + 工件二次加工)→ hit_count 2、来源去重
        fingerprint = fnd.key_fingerprint(KEY_XAI)
        record = store.load()[fingerprint]
        assert record["apikey"] == KEY_XAI
        assert record["provider"] == "xai"
        assert record["hit_count"] == 2
        assert record["sources"] == [EVIDENCE_URL]
        assert KEY_XAI in store_path.read_text(encoding="utf-8")  # 库文件确有全文

    def test_no_keystore_means_no_file(self, ks, monkeypatch, tmp_path):
        """未注入 keystore 的 hunt 是纯扫描:零落盘(引擎/测试自主决定)。"""
        monkeypatch.delenv("MYIA_HOME", raising=False)
        monkeypatch.chdir(tmp_path)
        result = run_hunt(tokens=["tok-test"], queries=["sk- filename:.env"])
        assert result["items"]
        assert not (tmp_path / "credhunter-keystore.json").exists()

    def test_adapter_fetch_hunt_defaults_to_myia_home(self, ks, monkeypatch, tmp_path):
        """适配器面缺省注入:MYIA_HOME 下的库收到全文,600 权限。"""
        monkeypatch.setenv("MYIA_HOME", str(tmp_path))
        path = tmp_path / "credhunter-keystore.json"

        async def _go() -> dict[str, Any]:
            async with make_client(_github_handler) as client:
                return await adapter().fetch_hunt(
                    github_tokens=["tok-test"], queries=["sk- filename:.env"], client=client
                )

        payload = asyncio.run(_go())
        assert payload["items"]
        assert path.is_file()
        assert KEY_XAI in path.read_text(encoding="utf-8")
        if os.name == "posix":
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        # 引擎面 payload 同样掩码-only(Q9 对外口径不变)
        assert KEY_XAI not in json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 契约四:run_from_keystore(三态回填 / Q7 / limit / Q8 / 掩码)
# ---------------------------------------------------------------------------


class TestRunFromKeystore:
    def test_three_states_backfilled(self, ks, fnd, cc, registry, store_path):
        seed_store(ks, fnd, store_path, (KEY_LIVE, KEY_DEAD, KEY_SICK))
        transport = FakeTransport(
            [
                ("GET", MODELS_URL, 200, '{"data": [{"id": "gpt-4o-mini"}]}'),
                ("GET", MODELS_URL, 401, '{"error": {"message": "bad key"}}'),
                ("GET", MODELS_URL, 500, '{"error": "boom"}'),
            ]
        )
        pacer = RecordingPacer()
        outcome = cc.run_from_keystore(
            store_path, registry=registry, transport=transport, pacer=pacer
        )
        assert outcome["counts"] == {"checked": 3, "final_verified": 1, "rejected": 1, "transient": 1}
        assert [entry["validation_state"] for entry in outcome["results"]] == [
            "final_verified", "rejected", "transient",
        ]
        assert [entry["fingerprint"] for entry in outcome["results"]] == [
            fnd.key_fingerprint(KEY_LIVE), fnd.key_fingerprint(KEY_DEAD), fnd.key_fingerprint(KEY_SICK),
        ]
        # 状态逐条回填入库(last_check 同步落盘,check_state 三态词表)
        records = ks.Keystore(store_path).load()
        expected = {
            fnd.key_fingerprint(KEY_LIVE): "final_verified",
            fnd.key_fingerprint(KEY_DEAD): "rejected",
            fnd.key_fingerprint(KEY_SICK): "transient",
        }
        for fingerprint, state in expected.items():
            assert records[fingerprint]["check_state"] == state
            assert records[fingerprint]["last_check"]
        # Q8:串行 + 按供应商组 wait(3 条各一发 models 探测)
        assert pacer.waits == ["openai", "openai", "openai"]

    def test_q7_balance_default_off_explicit_on(self, ks, fnd, cc, registry, store_path):
        seed_store(ks, fnd, store_path, (KEY_LIVE,))
        # 默认关:结果 balance 恒 None(未探测)
        quiet = cc.run_from_keystore(
            store_path,
            registry=registry,
            transport=FakeTransport([("GET", MODELS_URL, 200, '{"data": [{"id": "gpt-4o-mini"}]}')]),
            pacer=RecordingPacer(),
        )
        assert all(entry["balance"] is None for entry in quiet["results"])
        assert quiet["results"][0]["validation_state"] == "final_verified"
        # 显式开:final_verified 条目拿到余额证据(110.5 → "110.5")
        loud = cc.run_from_keystore(
            store_path,
            balance=True,
            registry=registry,
            transport=FakeTransport(
                [
                    ("GET", MODELS_URL, 200, '{"data": [{"id": "gpt-4o-mini"}]}'),
                    ("GET", GRANTS_URL, 200, '{"total_granted": 110.5}'),
                ]
            ),
            pacer=RecordingPacer(),
        )
        balance = loud["results"][0]["balance"]
        assert balance is not None and balance["balance_usd"] == "110.5"

    def test_limit_truncates_and_leaves_rest_untouched(self, ks, fnd, cc, registry, store_path):
        seed_store(ks, fnd, store_path, (KEY_LIVE, KEY_DEAD))
        outcome = cc.run_from_keystore(
            store_path,
            limit=1,
            registry=registry,
            transport=FakeTransport([("GET", MODELS_URL, 200, '{"data": [{"id": "gpt-4o-mini"}]}')]),
            pacer=RecordingPacer(),
        )
        assert outcome["records"] == 2
        assert outcome["counts"]["checked"] == 1
        records = ks.Keystore(store_path).load()
        assert records[fnd.key_fingerprint(KEY_LIVE)]["check_state"] == "final_verified"
        assert records[fnd.key_fingerprint(KEY_DEAD)]["check_state"] is None  # 未验不动

    def test_results_masked_only(self, ks, fnd, cc, registry, store_path):
        seed_store(ks, fnd, store_path, (KEY_LIVE, KEY_DEAD, KEY_SICK))
        outcome = cc.run_from_keystore(
            store_path,
            registry=registry,
            transport=FakeTransport(
                [
                    ("GET", MODELS_URL, 200, '{"data": [{"id": "gpt-4o-mini"}]}'),
                    ("GET", MODELS_URL, 401, '{"error": "no"}'),
                    ("GET", MODELS_URL, 500, '{"error": "boom"}'),
                ]
            ),
            pacer=RecordingPacer(),
        )
        dumped = json.dumps(outcome, ensure_ascii=False)
        for key in (KEY_LIVE, KEY_DEAD, KEY_SICK):
            assert key not in dumped, "Q9:全文密钥永不进 run_from_keystore 返回值"
        assert outcome["results"][0]["masked_apikey"] == fnd.mask_apikey(KEY_LIVE)

    def test_bad_record_raises_value_error(self, ks, cc, registry, store_path):
        store_path.write_text(
            json.dumps({"version": 1, "records": {"sha256-broken": {"provider": "openai"}}}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="apikey"):
            cc.run_from_keystore(store_path, registry=registry, transport=FakeTransport(), pacer=RecordingPacer())


# ---------------------------------------------------------------------------
# 契约五:适配器 CLI 面(run_credcheck_keystore)
# ---------------------------------------------------------------------------


class TestAdapterCredcheckKeystoreFace:
    def test_dual_faces_present(self):
        current = adapter()
        for name in ("run_credcheck", "run_credcheck_keystore"):
            assert callable(getattr(current, name, None)), f"适配器缺少入口 {name}"
        assert hasattr(current, "keystore")  # 密钥库子模块经适配器可达

    def test_payload_shape_and_backfill_zero_network(self, ks, fnd, store_path):
        """不可归因键 → no_api_url rejected(零网络);payload 掩码-only。"""
        seed_store(ks, fnd, store_path, (KEY_OPAQUE,))
        payload = adapter().run_credcheck_keystore(keystore_path=str(store_path))
        assert payload["plugin"] == "myia-credhunter"
        assert payload["capability"] == "credcheck"
        assert payload["status"] == "success"
        assert payload["source"] == "keystore"
        assert payload["keystore_path"] == str(store_path)
        assert payload["keystore_records"] == 1
        assert payload["counts"] == {"checked": 1, "rejected": 1}
        assert payload["results"][0]["fingerprint"] == fnd.key_fingerprint(KEY_OPAQUE)
        assert KEY_OPAQUE not in json.dumps(payload, ensure_ascii=False)  # Q9
        records = ks.Keystore(store_path).load()
        assert records[fnd.key_fingerprint(KEY_OPAQUE)]["check_state"] == "rejected"

    def test_default_path_from_myia_home(self, ks, monkeypatch, tmp_path):
        monkeypatch.setenv("MYIA_HOME", str(tmp_path))
        payload = adapter().run_credcheck_keystore()  # 空库:零探测、零网络
        assert payload["keystore_path"] == str(tmp_path / "credhunter-keystore.json")
        assert payload["counts"]["checked"] == 0
        assert (tmp_path / "credhunter-keystore.json").is_file()

    def test_invalid_keystore_structured_error(self, ks, store_path):
        store_path.write_text("{not-json", encoding="utf-8")
        with pytest.raises(adapter().CredhunterError) as excinfo:
            adapter().run_credcheck_keystore(keystore_path=str(store_path))
        assert excinfo.value.code == "keystore_invalid"

    def test_limit_passthrough(self, ks, fnd, store_path, monkeypatch):
        """limit 透传:被裁条目不入 results、不回填(经 monkeypatch 探测函数核验)。"""
        cc = adapter().credcheck
        seed_store(ks, fnd, store_path, (KEY_LIVE, KEY_DEAD))
        calls: list[str] = []

        def fake_check_credential(**kwargs: Any) -> Any:
            calls.append(kwargs["apikey"])
            outcome = cc.ModelsProbeOutcome(models=("gpt-x",), status_code=200, provider="openai", key_state="active")
            state, error = cc._classify(outcome)
            return cc.ValidationResult(provider="openai", validation_state=state, error=error,
                                       key_state="active", status_code=200, models=("gpt-x",))

        monkeypatch.setattr(cc, "check_credential", fake_check_credential)
        payload = adapter().run_credcheck_keystore(keystore_path=str(store_path), limit=1)
        assert calls == [KEY_LIVE]
        assert payload["counts"] == {"checked": 1, "final_verified": 1}
        records = ks.Keystore(store_path).load()
        assert records[fnd.key_fingerprint(KEY_LIVE)]["check_state"] == "final_verified"
        assert records[fnd.key_fingerprint(KEY_DEAD)]["check_state"] is None


# 加载纪律自检:本测试文件与适配器共享同一子模块对象(canonical 名复用)。
def test_shared_canonical_modules() -> None:
    assert "myia_credhunter_keystore" in sys.modules
    assert sys.modules["myia_credhunter_keystore"] is adapter().keystore

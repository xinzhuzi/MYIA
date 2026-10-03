"""Tests for the full CLI (PRD 10-01-v02-cli-full): init / test / list / doctor + secret.

Covers the PRD acceptance list:

- 四命令 + ``--json`` 契约测试(stdout 恒为单份可解析 JSON);
- ``myia init`` 输出被解析为 JSON 成功(契约测试,两种模式同一产物);
- doctor 诊断三类预置故障:明文凭据 / 源连续失败 / keychain 引用不存在;
- 「指纹未跳过但产出 0 条」的源在 run 状态 success 下仍判 degraded
  (ZOL gpu-prices 2026-10-01 静默 0 条事故的对位用例);
- 源健康度判据:ok(产出/指纹未变)/ degraded(静默 0 条、基线腰斩、
  未满死线的失败)/ dead(连续 3 轮失败)/ unknown(无记录);
- ``myia test`` 试抓:提取字段 + 指纹 + 去重键预览,不推送不入库;
- ``myia secret`` set/list/delete 往返(注入 mock 钥匙串,值永不回显)。

所有 I/O 走 httpx.MockTransport 与注入的内存钥匙串;存储用 tmp_path /
:memory:,零真实网络、零真实钥匙串、零共享状态。
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any

import httpx
import pytest

from myia import cli as cli_module
from myia import secrets as secrets_store
from myia.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_FETCH_ALL_FAILED,
    EXIT_OK,
    EXIT_PARTIAL,
    main,
)
from myia.secrets import InMemoryKeychainBackend
from myia.store import SQLiteStore

VALID_YAML = """
id: demo
name: 演示品类
schedule: "0 9 * * *"
timezone: Asia/Shanghai
sources:
  - name: api
    engine: direct_api
    url: "https://api.demo.local/list"
    headers:
      Accept: "application/json"
    rate_limit:
      qps: 1000.0
    retry: 0
    extract:
      type: json_path
      fields:
        title: "$.data[*].title"
        url: "$.data[*].url"
push:
  - channel: stdout
"""

KEYCHAIN_YAML = """
id: monitor
name: 变更监控
schedule: "*/15 * * * *"
timezone: Asia/Shanghai
sources:
  - name: watches
    engine: direct_api
    url: "https://monitor.example.local/api/watch"
    headers:
      X-Api-Key: "keychain:myia/monitor/token"
    rate_limit:
      qps: 1000.0
    retry: 0
    extract:
      type: json_path
      fields:
        title: "$[*].label"
        url: "$[*].url"
"""

#: 第二个源(默认 500,供部分失败/源过滤用例按需改写 URL)。
SECOND_SOURCE = """
  - name: second
    engine: direct_api
    url: "https://api.demo.local/down"
    rate_limit:
      qps: 1000.0
    retry: 0
    extract:
      type: json_path
      fields:
        title: "$.data[*].title"
        url: "$.data[*].url"
"""

TWO_SOURCES_YAML = VALID_YAML.replace(
    "push:\n  - channel: stdout", f"{SECOND_SOURCE}push:\n  - channel: stdout"
)

PLAINTEXT_YAML = """
id: bad
name: 明文品类
schedule: "0 9 * * *"
timezone: Asia/Shanghai
sources:
  - name: api
    engine: direct_api
    url: "https://api.demo.local/list"
    headers:
      Cookie: "sid=123456"
    extract:
      type: json_path
      fields:
        title: "$.data[*].title"
        url: "$.data[*].url"
push:
  - channel: stdout
"""

#: telegram 推送通道品类(bot token 固定解析自 env:TELEGRAM_BOT_TOKEN,
#: target 指向 chat id 引用;doctor 的 getUpdates 同 token 竞争提示用)。
TELEGRAM_YAML = """
id: tg-{tag}
name: TG 品类
schedule: "0 9 * * *"
timezone: Asia/Shanghai
sources:
  - name: api
    engine: direct_api
    url: "https://api.demo.local/list"
    rate_limit:
      qps: 1000.0
    retry: 0
    extract:
      type: json_path
      fields:
        title: "$.data[*].title"
        url: "$.data[*].url"
push:
  - channel: telegram
    target: "env:MYIA_TG_CHAT_ID"
"""


# ---------------------------------------------------------------------------
# Fixtures & helpers (每个测试独立目录/数据库/钥匙串,零共享状态)
# ---------------------------------------------------------------------------


@pytest.fixture()
def keychain_backend():
    """Inject a fresh in-memory keychain backend; reset afterwards(不泄漏)."""
    backend = InMemoryKeychainBackend()
    secrets_store.set_backend(backend)
    yield backend
    secrets_store.reset_backend()


def make_plugin_dir(tmp_path):
    """A plugins directory holding one valid category YAML; returns its path."""
    directory = tmp_path / "plugins"
    directory.mkdir()
    (directory / "demo.yaml").write_text(VALID_YAML, encoding="utf-8")
    return directory


def write_plugin(tmp_path, name: str, text: str):
    """Write one plugin YAML into tmp_path and return its path string."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def source_stats(
    name: str,
    *,
    item_count: int = 0,
    skip_reason: str | None = None,
    failed: bool = False,
    engine: str = "direct_api",
) -> dict[str, Any]:
    """One ``runs.stats.sources`` entry in the pipeline's real shape."""
    return {
        "source": name,
        "engine": engine,
        "url": f"https://api.demo.local/{name}",
        "item_count": item_count,
        "attempts": 1,
        "duration_seconds": 0.01,
        "skipped": skip_reason is not None,
        "skip_reason": skip_reason,
        "failures": [],
        "error": {"error_type": "network", "message": "模拟失败"} if failed else None,
        "failed": failed,
    }


def seed_runs(db_path, category: str, histories: list[tuple[str, list[dict[str, Any]]]]) -> None:
    """Persist runs oldest-first: each item is (run_status, stats_sources)."""
    store = SQLiteStore(db_path)
    try:
        for status, sources in histories:
            run_id = store.start_run(category)
            store.finish_run(run_id, status=status, stats={"status": status, "sources": sources})
    finally:
        store.close()


def mock_client_factory(responder):
    """Replace cli._build_async_client with a MockTransport-backed factory."""

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("proxy", None)  # MockTransport 不走真实代理,代理参数仅影响生产路径
        return httpx.AsyncClient(transport=httpx.MockTransport(responder), **kwargs)

    return factory


def api_responder(request: httpx.Request) -> httpx.Response:
    """robots 404(fail-open)+ JSON API 两连发。"""
    if request.url.path == "/robots.txt":
        return httpx.Response(404, text="")
    return httpx.Response(
        200,
        json={"data": [{"title": "公开论坛标题", "url": "https://example.com/t1"},
                       {"title": "AI 资讯", "url": "https://example.com/t2"}]},
    )


# ---------------------------------------------------------------------------
# myia init:结构化信息清单(AI 消费,非人机问答)
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_json_contract_parses(self, capsys):
        """契约测试:myia init 输出被解析为 JSON 成功,含必填/可选/规则/下一步。"""
        code = main(["init", "--json"])

        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "init"
        assert payload["required_inputs"]
        assert {item["id"] for item in payload["required_inputs"]} >= {"identity", "schedule", "sources"}
        assert {item["id"] for item in payload["optional_inputs"]} >= {"dedup", "push", "storage"}
        assert any("明文" in rule for rule in payload["rules"])
        assert payload["next_steps"]

    def test_init_human_mode_stdout_is_still_json(self, capsys):
        """init 的交付物就是 JSON:人类模式 stdout 恒为同一份 JSON,说明走 stderr。"""
        code = main(["init"])

        assert code == EXIT_OK
        captured = capsys.readouterr()
        assert json.loads(captured.out)["command"] == "init"
        assert "myia test" in captured.err


# ---------------------------------------------------------------------------
# 源健康度状态机(list/doctor 判据)
# ---------------------------------------------------------------------------


class TestSourceHealth:
    def test_silent_zero_with_success_run_is_degraded(self, tmp_path, capsys):
        """ZOL 对位用例:run 级 success,源级静默 0 条 → degraded(success 不掩盖)。"""
        db = tmp_path / "myia.db"
        seed_runs(db, "demo", [("success", [source_stats("api", item_count=0, skip_reason=None)])])

        code = main(["list", "--plugins-dir", str(make_plugin_dir(tmp_path)), "--db", str(db), "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        source = payload["plugins"][0]["sources"][0]
        assert source["health"]["state"] == "degraded"
        assert source["health"]["latest"]["run_status"] == "success"
        assert "0 条" in source["health"]["reason"]

    def test_fingerprint_unchanged_zero_is_ok(self, tmp_path, capsys):
        """指纹未变(304/validators_match)导致 0 条:合理跳过,判 ok。"""
        db = tmp_path / "myia.db"
        seed_runs(db, "demo", [("success", [source_stats("api", item_count=0, skip_reason="not_modified")])])

        main(["list", "--plugins-dir", str(make_plugin_dir(tmp_path)), "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        source = payload["plugins"][0]["sources"][0]
        assert source["health"]["state"] == "ok"
        assert source["fingerprint_skips"] == {"observed": 1, "skipped": 1}

    def test_items_ok_state(self, tmp_path, capsys):
        """本轮有产出:ok。"""
        db = tmp_path / "myia.db"
        seed_runs(db, "demo", [("success", [source_stats("api", item_count=7)])])

        main(["list", "--plugins-dir", str(make_plugin_dir(tmp_path)), "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["plugins"][0]["sources"][0]["health"]["state"] == "ok"

    def test_three_consecutive_failures_are_dead(self, tmp_path, capsys):
        """连续 3 轮采集失败:dead。"""
        db = tmp_path / "myia.db"
        seed_runs(db, "demo", [("failed", [source_stats("api", failed=True)]) for _ in range(3)])

        main(["list", "--plugins-dir", str(make_plugin_dir(tmp_path)), "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["plugins"][0]["sources"][0]["health"]["state"] == "dead"

    def test_two_failures_are_not_dead(self, tmp_path, capsys):
        """失败不足 3 轮:degraded(未满死线),不到 dead。"""
        db = tmp_path / "myia.db"
        seed_runs(db, "demo", [("failed", [source_stats("api", failed=True)]) for _ in range(2)])

        main(["list", "--plugins-dir", str(make_plugin_dir(tmp_path)), "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["plugins"][0]["sources"][0]["health"]["state"] == "degraded"

    def test_baseline_drop_needs_five_history_samples(self, tmp_path, capsys):
        """基线判据:被评判轮**之前**满 5 个历史样本且本轮 <50% → degraded。"""
        db = tmp_path / "myia.db"
        histories = [("success", [source_stats("api", item_count=10)]) for _ in range(5)]
        histories.append(("success", [source_stats("api", item_count=3)]))
        seed_runs(db, "demo", histories)

        main(["list", "--plugins-dir", str(make_plugin_dir(tmp_path)), "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        source = payload["plugins"][0]["sources"][0]
        # 5 个历史样本(不含被评判轮),基线 10.0,3 < 5.0 → degraded
        assert source["health"]["state"] == "degraded"
        assert source["health"]["baseline"] == 10.0

    def test_baseline_drop_excludes_judged_round_from_baseline(self, tmp_path, capsys):
        """回归:被评判的最新一轮不得计入自身基线——否则 50% 阈值被稀释到
        ~44%(4 历史 10 + 本轮 5:旧算法基线 (10*4+5)/5=9,5 ≥ 4.5 漏报)。"""
        db = tmp_path / "myia.db"
        histories = [("success", [source_stats("api", item_count=10)]) for _ in range(4)]
        histories.append(("success", [source_stats("api", item_count=5)]))  # 48.8% < 50%
        seed_runs(db, "demo", histories)

        main(["list", "--plugins-dir", str(make_plugin_dir(tmp_path)), "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        source = payload["plugins"][0]["sources"][0]
        # 历史样本只有 4 个(<5)→ 基线判据不参与,只看 0 条判据 → ok
        assert source["health"]["state"] == "ok"
        assert source["health"]["baseline"] is None

    def test_baseline_drop_below_five_samples_stays_ok(self, tmp_path, capsys):
        """样本不足 5 次:只看 0 条判据,腰斩不触发。"""
        db = tmp_path / "myia.db"
        histories = [("success", [source_stats("api", item_count=10)]) for _ in range(3)]
        histories.append(("success", [source_stats("api", item_count=3)]))
        seed_runs(db, "demo", histories)

        main(["list", "--plugins-dir", str(make_plugin_dir(tmp_path)), "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["plugins"][0]["sources"][0]["health"]["state"] == "ok"

    def test_no_history_is_unknown(self, tmp_path, capsys):
        """store 无运行记录:unknown,不误报。"""
        db = tmp_path / "myia.db"
        SQLiteStore(str(db)).close()

        main(["list", "--plugins-dir", str(make_plugin_dir(tmp_path)), "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["plugins"][0]["sources"][0]["health"]["state"] == "unknown"

    def test_list_missing_dir_exit_one(self, tmp_path, capsys):
        """插件目录不存在:结构化报错,退出码 1。"""
        code = main(["list", "--plugins-dir", str(tmp_path / "nope"), "--json"])

        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "plugins_dir"


# ---------------------------------------------------------------------------
# myia doctor:结构化诊断 + 三类预置故障
# ---------------------------------------------------------------------------


class TestDoctor:
    def test_healthy_repo_exit_zero_and_contract(self, tmp_path, capsys):
        """健康仓库:exit 0、healthy=true、无 findings;--json 顶层契约完整。"""
        db = tmp_path / "myia.db"
        seed_runs(db, "demo", [("success", [source_stats("api", item_count=4)])])
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)

        code = main(["doctor", yaml_path, "--db", str(db), "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        for key in ("command", "generated_at", "db", "healthy", "plugins", "credentials", "proxy", "findings", "summary"):
            assert key in payload
        assert payload["healthy"] is True
        assert payload["findings"] == []
        plugin = payload["plugins"][0]
        assert plugin["loaded"] is True
        assert plugin["sources"][0]["health"]["state"] == "ok"
        assert plugin["sources"][0]["engine_hint"] is None

    def test_next_fire_time_present(self, tmp_path, capsys):
        """调度下次触发时间:ISO 可解析且在未来。"""
        from datetime import datetime

        db = tmp_path / "myia.db"
        SQLiteStore(str(db)).close()
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)

        main(["doctor", yaml_path, "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        next_fire = payload["plugins"][0]["next_fire_at"]
        assert next_fire is not None
        assert datetime.fromisoformat(next_fire) > datetime.now(UTC)

    def test_detects_plaintext_credential(self, tmp_path, capsys):
        """故障样例 1(明文凭据):加载期拒载,finding 携带字段路径与错误类。"""
        db = tmp_path / "myia.db"
        SQLiteStore(str(db)).close()
        yaml_path = write_plugin(tmp_path, "bad.yaml", PLAINTEXT_YAML)
        assert "Cookie" in PLAINTEXT_YAML  # 夹具自检:确实夹带了明文

        code = main(["doctor", yaml_path, "--db", str(db), "--json"])
        assert code == EXIT_OK  # 诊断完成即 0,问题落在 findings
        payload = json.loads(capsys.readouterr().out)
        assert payload["healthy"] is False
        plugin = payload["plugins"][0]
        assert plugin["loaded"] is False
        error = plugin["load_errors"][0]
        assert error["error_type"] == "credential_plaintext"
        assert error["path"] == "$.sources[0].headers.Cookie"
        codes = [finding["code"] for finding in payload["findings"]]
        assert "credential_plaintext" in codes

    def test_detects_dead_source(self, tmp_path, capsys):
        """故障样例 2(源连续失败):连续 3 轮失败 → dead + error 级 finding。"""
        db = tmp_path / "myia.db"
        seed_runs(db, "demo", [("failed", [source_stats("api", failed=True)]) for _ in range(3)])
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)

        code = main(["doctor", yaml_path, "--db", str(db), "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["plugins"][0]["sources"][0]["health"]["state"] == "dead"
        finding = next(f for f in payload["findings"] if f["code"] == "source_dead")
        assert finding["severity"] == "error"
        assert "source:api" in finding["scope"]

    def test_detects_silent_zero_despite_success_run(self, tmp_path, capsys):
        """验收对位用例(doctor 侧):run 级 success,静默 0 条 → doctor 仍报 degraded。"""
        db = tmp_path / "myia.db"
        seed_runs(db, "demo", [("success", [source_stats("api", item_count=0, skip_reason=None)])])
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)

        main(["doctor", yaml_path, "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        source = payload["plugins"][0]["sources"][0]
        assert source["health"]["state"] == "degraded"
        assert source["health"]["latest"]["run_status"] == "success"
        finding = next(f for f in payload["findings"] if f["code"] == "source_degraded")
        assert finding["severity"] == "warning"
        assert payload["healthy"] is True  # warning 不翻转 healthy,但 findings 必须可见

    def test_detects_missing_keychain_ref(self, tmp_path, capsys, keychain_backend):
        """故障样例 3(keychain 引用不存在):entry.exists=false + 修复指引 finding。"""
        db = tmp_path / "myia.db"
        SQLiteStore(str(db)).close()
        yaml_path = write_plugin(tmp_path, "monitor.yaml", KEYCHAIN_YAML)

        code = main(["doctor", yaml_path, "--db", str(db), "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        entry = payload["credentials"]["entries"][0]
        assert entry["kind"] == "keychain"
        assert entry["name"] == "myia/monitor/token"
        assert entry["exists"] is False
        finding = next(f for f in payload["findings"] if f["code"] == "keychain_ref_missing")
        assert finding["severity"] == "error"
        assert "shishi secret set myia/monitor/token" in finding["message"]
        assert payload["healthy"] is False

    def test_existing_keychain_ref_no_finding(self, tmp_path, capsys, keychain_backend):
        """钥匙链里存在同名凭据:exists=true,无 finding(healthy 保持 true)。"""
        secrets_store.set_secret("myia/monitor/token", "token-value", backend=keychain_backend)
        db = tmp_path / "myia.db"
        SQLiteStore(str(db)).close()
        yaml_path = write_plugin(tmp_path, "monitor.yaml", KEYCHAIN_YAML)

        main(["doctor", yaml_path, "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["credentials"]["entries"][0]["exists"] is True
        assert payload["findings"] == []
        assert payload["healthy"] is True
        captured = capsys.readouterr()
        assert "token-value" not in captured.out + captured.err  # 值永不入输出

    def test_env_ref_missing_is_warning(self, tmp_path, capsys, monkeypatch):
        """env: 引用未设置:warning 级 finding(env 缺失常是调度环境差异)。"""
        monkeypatch.delenv("MYIA_T_DOCTOR_TOKEN", raising=False)
        db = tmp_path / "myia.db"
        SQLiteStore(str(db)).close()
        yaml_text = VALID_YAML.replace(
            'Accept: "application/json"', 'Authorization: "Bearer env:MYIA_T_DOCTOR_TOKEN"'
        )
        yaml_path = write_plugin(tmp_path, "demo.yaml", yaml_text)

        main(["doctor", yaml_path, "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        entry = payload["credentials"]["entries"][0]
        assert entry == {
            "kind": "env",
            "name": "MYIA_T_DOCTOR_TOKEN",
            "ref": "Bearer env:MYIA_T_DOCTOR_TOKEN",
            "paths": [{"plugin": "demo", "path": "$.sources[0].headers.Authorization"}],
            "plugins": ["demo"],
            "exists": False,
        }
        finding = next(f for f in payload["findings"] if f["code"] == "env_ref_missing")
        assert finding["severity"] == "warning"
        assert payload["healthy"] is True  # warning 不翻转 healthy

    def test_enrich_budget_and_cache_rows(self, tmp_path, capsys):
        """enrich 节:预算/缓存状态可见(缓存行数取自 store)。"""
        enrich_yaml = VALID_YAML + (
            "enrich:\n  enabled: true\n  budget_per_run: 12345\n"
        )
        yaml_path = write_plugin(tmp_path, "demo.yaml", enrich_yaml)
        db = tmp_path / "myia.db"
        store = SQLiteStore(str(db))
        store.set_enrich_cache("https://example.com/a", "glm-4-flash", "value@p1",
                               {"scores": {"value": 8}, "score": 8.0})
        store.close()

        main(["doctor", yaml_path, "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        enrich = payload["plugins"][0]["enrich"]
        assert enrich["enabled"] is True
        assert enrich["budget_per_run"] == 12345
        assert enrich["cache"] is True
        assert enrich["cache_rows"] == 1

    def test_proxy_connectivity_ok(self, tmp_path, capsys, monkeypatch):
        """代理连通性:--config 提供 pools 声明时逐池探测(exit_ip/延迟可见,凭据打码)。"""
        monkeypatch.setenv("MYIA_T_PROXY_USER", "alice")
        monkeypatch.setenv("MYIA_T_PROXY_PASS", "hunter2")
        pools_file = tmp_path / "myia.yaml"
        pools_file.write_text(
            "pools:\n  main: \"http://env:MYIA_T_PROXY_USER:env:MYIA_T_PROXY_PASS@proxy.example.test:8080\"\n",
            encoding="utf-8",
        )

        def responder(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "api.ipify.org"
            return httpx.Response(200, json={"ip": "203.0.113.7"})

        monkeypatch.setattr(cli_module, "_build_async_client", mock_client_factory(responder))
        db = tmp_path / "myia.db"
        SQLiteStore(str(db)).close()
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)

        code = main(["doctor", yaml_path, "--db", str(db), "--config", str(pools_file), "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        pool = payload["proxy"]["pools"][0]
        assert pool["pool"] == "main"
        assert pool["ok"] is True
        assert pool["exit_ip"] == "203.0.113.7"
        assert pool["proxy_url"] == "http://***@proxy.example.test:8080"
        assert "hunter2" not in capsys.readouterr().out  # 凭据零入库
        assert payload["healthy"] is True

    def test_proxy_unreachable_is_warning(self, tmp_path, capsys, monkeypatch):
        """代理不通:warning 级 finding(代理挂 ≠ 源死)。"""
        pools_file = tmp_path / "myia.yaml"
        pools_file.write_text("pools:\n  main: \"http://proxy.example.test:8080\"\n", encoding="utf-8")

        def responder(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        monkeypatch.setattr(cli_module, "_build_async_client", mock_client_factory(responder))
        db = tmp_path / "myia.db"
        SQLiteStore(str(db)).close()
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)

        main(["doctor", yaml_path, "--db", str(db), "--config", str(pools_file), "--json"])
        payload = json.loads(capsys.readouterr().out)
        pool = payload["proxy"]["pools"][0]
        assert pool["ok"] is False
        assert pool["error_type"] == "proxy_network"
        finding = next(f for f in payload["findings"] if f["scope"] == "proxy:main")
        assert finding["severity"] == "warning"

    def test_scans_plugins_dir_by_default(self, tmp_path, capsys):
        """缺省扫描 --plugins-dir:明文插件与健康插件同场体检。"""
        directory = tmp_path / "plugins"
        directory.mkdir()
        (directory / "demo.yaml").write_text(VALID_YAML, encoding="utf-8")
        (directory / "bad.yaml").write_text(PLAINTEXT_YAML, encoding="utf-8")
        db = tmp_path / "myia.db"
        SQLiteStore(str(db)).close()

        main(["doctor", "--plugins-dir", str(directory), "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        loaded = {plugin["file"]: plugin["loaded"] for plugin in payload["plugins"]}
        assert loaded == {str(directory / "bad.yaml"): False, str(directory / "demo.yaml"): True}
        assert payload["healthy"] is False

    def test_multiple_telegram_categories_flagged_as_poll_conflict(self, tmp_path, capsys):
        """≥2 个品类配 telegram 通道(素材 12):warning 级 getUpdates 同 token 竞争提示。"""
        directory = tmp_path / "plugins"
        directory.mkdir()
        (directory / "tg-a.yaml").write_text(
            TELEGRAM_YAML.format(tag="a"), encoding="utf-8"
        )
        (directory / "tg-b.yaml").write_text(
            TELEGRAM_YAML.format(tag="b"), encoding="utf-8"
        )
        db = tmp_path / "myia.db"
        SQLiteStore(str(db)).close()

        code = main(["doctor", "--plugins-dir", str(directory), "--db", str(db), "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        finding = next(f for f in payload["findings"] if f["code"] == "telegram_token_poll_conflict")
        assert finding["severity"] == "warning"  # 提示不判错:是否常驻由部署形态决定
        assert finding["scope"] == "feedback"
        assert "tg-a" in finding["message"] and "tg-b" in finding["message"]
        assert "409" in finding["message"]
        assert payload["healthy"] is True  # warning 不翻转 healthy

    def test_single_telegram_category_no_conflict_finding(self, tmp_path, capsys):
        """仅一个品类配 telegram 通道:无竞争提示(单轮询方是合法形态)。"""
        directory = tmp_path / "plugins"
        directory.mkdir()
        (directory / "tg-a.yaml").write_text(
            TELEGRAM_YAML.format(tag="a"), encoding="utf-8"
        )
        (directory / "demo.yaml").write_text(VALID_YAML, encoding="utf-8")
        db = tmp_path / "myia.db"
        SQLiteStore(str(db)).close()

        main(["doctor", "--plugins-dir", str(directory), "--db", str(db), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert not any(
            f["code"] == "telegram_token_poll_conflict" for f in payload["findings"]
        )  # 仅一个轮询方是合法形态(target 的 env 未设置另有 env_ref_missing,不在此断言)


# ---------------------------------------------------------------------------
# myia test:单源试抓(不推送不入库)
# ---------------------------------------------------------------------------


class TestTrialFetch:
    def test_trial_fetch_success_contract(self, tmp_path, capsys, monkeypatch):
        """试抓成功:提取字段 + 指纹判定 + 去重键预览;不触碰持久库。"""
        monkeypatch.setattr(cli_module, "_build_async_client", mock_client_factory(api_responder))
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)

        code = main(["test", yaml_path, "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "test"
        assert payload["status"] == "success"
        source = payload["sources"][0]
        assert source["ok"] is True
        assert source["engine"] == "direct_api"
        assert source["item_count"] == 2
        assert source["fingerprint"]["verdict"] == "changed_or_first_fetch"
        assert source["items"][0]["fields"]["title"] == "公开论坛标题"
        assert source["items"][0]["dedup_key"] == "https://example.com/t1"  # 缺省 {url}
        assert "pushes" not in payload and "push" not in payload  # 试抓无推送环节

    def test_trial_fetch_source_filter(self, tmp_path, capsys, monkeypatch):
        """--source 只试抓指定源;未知源名 → 退出码 1 + 可用源清单。"""
        monkeypatch.setattr(cli_module, "_build_async_client", mock_client_factory(api_responder))
        yaml_path = write_plugin(tmp_path, "two.yaml", TWO_SOURCES_YAML)

        code = main(["test", yaml_path, "--source", "second", "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert [source["source"] for source in payload["sources"]] == ["second"]

        code = main(["test", yaml_path, "--source", "nope", "--json"])
        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["errors"][0]["error_type"] == "source_not_found"
        assert "api" in payload["errors"][0]["message"] and "second" in payload["errors"][0]["message"]

    def test_trial_fetch_all_failed_exit_two(self, tmp_path, capsys, monkeypatch):
        """全部源失败:退出码 2,引擎链逐级结构化失败记录可见。"""
        always_500 = mock_client_factory(lambda request: httpx.Response(500, text="boom"))
        monkeypatch.setattr(cli_module, "_build_async_client", always_500)
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)

        code = main(["test", yaml_path, "--json"])
        assert code == EXIT_FETCH_ALL_FAILED
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "failed"
        source = payload["sources"][0]
        assert source["ok"] is False
        assert source["fingerprint"]["verdict"] == "unknown"
        attempted = [failure["engine"] for failure in source["failures"]]
        assert "direct_api" in attempted and "static_html" in attempted
        assert all(failure["error_type"] for failure in source["failures"])

    def test_trial_fetch_partial_exit_three(self, tmp_path, capsys, monkeypatch):
        """部分源失败:退出码 3。"""

        def responder(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404, text="")
            if request.url.path == "/down":
                return httpx.Response(500, text="boom")
            return httpx.Response(
                200, json={"data": [{"title": "标题", "url": "https://example.com/t"}]}
            )

        monkeypatch.setattr(cli_module, "_build_async_client", mock_client_factory(responder))
        yaml_path = write_plugin(tmp_path, "two.yaml", TWO_SOURCES_YAML)

        code = main(["test", yaml_path, "--json"])
        assert code == EXIT_PARTIAL
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "partial"
        assert [source["ok"] for source in payload["sources"]] == [True, False]

    def test_trial_fetch_persists_nothing(self, tmp_path, capsys, monkeypatch):
        """试抓零持久化:命令结束后磁盘上没有新库文件。"""
        monkeypatch.setattr(cli_module, "_build_async_client", mock_client_factory(api_responder))
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)

        main(["test", yaml_path, "--json"])
        assert not (tmp_path / "myia.db").exists()

    def test_trial_fetch_config_error_exit_one(self, tmp_path, capsys):
        """YAML 拒载:退出码 1(先于任何网络)。"""
        yaml_path = write_plugin(tmp_path, "bad.yaml", PLAINTEXT_YAML)

        code = main(["test", yaml_path, "--json"])
        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["errors"][0]["error_type"] == "credential_plaintext"

    def test_trial_fetch_preview_cap(self, tmp_path, capsys, monkeypatch):
        """条目预览封顶 5 条,多余标记 items_truncated。"""
        payload_data = {"data": [{"title": f"标题{i}", "url": f"https://example.com/t{i}"} for i in range(8)]}

        def responder(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404, text="")
            return httpx.Response(200, json=payload_data)

        monkeypatch.setattr(cli_module, "_build_async_client", mock_client_factory(responder))
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)

        main(["test", yaml_path, "--json"])
        payload = json.loads(capsys.readouterr().out)
        source = payload["sources"][0]
        assert len(source["items"]) == 5
        assert source["items_truncated"] is True
        assert source["item_count"] == 8


# ---------------------------------------------------------------------------
# myia secret:钥匙链凭据管理(mock 钥匙串,零真实触碰)
# ---------------------------------------------------------------------------


class TestSecret:
    SECRET_NAME = "myia/selftest/cookie"
    SECRET_VALUE = "s3cret-value-42"

    def test_set_list_delete_roundtrip(self, tmp_path, capsys, keychain_backend):
        """写入→列出→删除往返;输出只有引用名,值永不回显。"""
        code = main(["secret", "set", self.SECRET_NAME, "--value", self.SECRET_VALUE, "--json"])
        assert code == EXIT_OK
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload == {"command": "secret", "action": "set", "name": self.SECRET_NAME, "stored": True}
        # --value 暴露面警告走 stderr(--json stdout 仍是单份文档)
        assert "shell history" in captured.err

        code = main(["secret", "list", "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["names"] == [self.SECRET_NAME]

        code = main(["secret", "delete", self.SECRET_NAME, "--json"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["deleted"] is True

        code = main(["secret", "list", "--json"])
        assert json.loads(capsys.readouterr().out)["names"] == []
        captured_all = capsys.readouterr()
        assert self.SECRET_VALUE not in captured_all.out + captured_all.err

    def test_delete_missing_exit_one(self, capsys, keychain_backend):
        """删除不存在的凭据:结构化 secret_not_found,退出码 1。"""
        code = main(["secret", "delete", self.SECRET_NAME, "--json"])

        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "secret"
        assert payload["code"] == "secret_not_found"

    def test_set_noncanonical_name_exit_one(self, capsys, keychain_backend):
        """非规范名(缺 myia/<scope>/ 前缀):结构化 invalid_secret_name + 迁移指引。"""
        code = main(["secret", "set", "linuxsb_cookie", "--value", "x", "--json"])

        assert code == EXIT_CONFIG_ERROR
        payload = json.loads(capsys.readouterr().out)
        assert payload["code"] == "invalid_secret_name"
        assert "myia/<scope>/<name>" in payload["message"]

    def test_human_mode_secret_list(self, capsys, keychain_backend):
        """人类模式:同一信息另一种皮(名字列表,无值)。"""
        secrets_store.set_secret(self.SECRET_NAME, self.SECRET_VALUE, backend=keychain_backend)

        code = main(["secret", "list"])
        assert code == EXIT_OK
        captured = capsys.readouterr()
        assert self.SECRET_NAME in captured.out
        assert self.SECRET_VALUE not in captured.out + captured.err


# ---------------------------------------------------------------------------
# 契约汇总:四命令 --json 单份文档
# ---------------------------------------------------------------------------


class TestJsonContract:
    def test_all_four_commands_emit_single_json_document(self, tmp_path, capsys, monkeypatch):
        """四命令 + --json:stdout 均为单份可被 json 解析的文档(验收项)。"""
        monkeypatch.setattr(cli_module, "_build_async_client", mock_client_factory(api_responder))
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)
        db = tmp_path / "myia.db"
        seed_runs(db, "demo", [("success", [source_stats("api", item_count=4)])])

        for argv in (
            ["init"],
            ["test", yaml_path],
            ["list", "--plugins-dir", str(tmp_path), "--db", str(db)],
            ["doctor", yaml_path, "--db", str(db)],
        ):
            code = main([*argv, "--json"])
            payload = json.loads(capsys.readouterr().out)  # 解析失败即契约破裂
            assert code == EXIT_OK, f"{argv} 退出码 {code}"
            assert payload["command"] == argv[0]

    def test_store_down_still_single_document(self, tmp_path, capsys):
        """回归(2026-10 复盘 high):库损坏时 --json 仍是单份 JSON 文档。

        list 把结构化错误放顶层 ``store_error``(health 如实标「存储无法打开」,
        不伪装「暂无运行记录」);doctor 走 findings。不得出现两份文档拼接。"""
        db = tmp_path / "broken.db"
        db.write_bytes(b"this is not a sqlite database" * 10)
        yaml_path = write_plugin(tmp_path, "demo.yaml", VALID_YAML)

        list_code = main(["list", "--plugins-dir", str(tmp_path), "--db", str(db), "--json"])
        list_payload = json.loads(capsys.readouterr().out)  # 双文档 → Extra data 崩
        assert list_code == EXIT_OK
        assert list_payload["store_error"]["error_type"] == "store_corrupt"
        assert list_payload["plugins"][0]["sources"][0]["health"]["reason"].startswith("存储无法打开")

        doctor_code = main(["doctor", yaml_path, "--db", str(db), "--json"])
        doctor_payload = json.loads(capsys.readouterr().out)
        assert doctor_code == EXIT_OK
        assert doctor_payload["healthy"] is False
        assert any(f["code"] == "store_error" for f in doctor_payload["findings"])
        assert doctor_payload["plugins"][0]["sources"][0]["health"]["reason"].startswith("存储无法打开")


# ---------------------------------------------------------------------------
# 2026-10 复盘修复回归:run 接线全局 proxy pools(PRD:让源级代理真正生效)
# ---------------------------------------------------------------------------

PROXY_YAML = """
id: proxy-demo
name: 代理演示
schedule: "0 9 * * *"
timezone: Asia/Shanghai
sources:
  - name: pool-src
    engine: direct_api
    url: "https://target.example/list"
    proxy: pool:main
    retry: 0
    extract:
      type: json_path
      fields:
        title: "$[*].t"
        url: "$[*].u"
push:
  - channel: stdout
"""


class TestRunProxyPools:
    def test_run_without_config_reports_pools_not_configured(self, tmp_path, capsys):
        """未提供 --config:pool 源在任何 I/O 前结构化报 proxy_pools_not_configured。"""
        yaml_path = write_plugin(tmp_path, "proxy.yaml", PROXY_YAML)
        code = main(["run", yaml_path, "--db", str(tmp_path / "db.sqlite"), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_FETCH_ALL_FAILED
        error_types = {f["error_type"] for f in payload["sources"][0]["failures"]}
        assert "proxy_config" in error_types
        assert any("pools" in f["message"] for f in payload["sources"][0]["failures"])

    def test_run_with_config_mounts_pool_transport(self, tmp_path, capsys):
        """--config 注入 pools 后,失败不再是「未注入声明」而是真实代理链路错误
        (本测试上游 127.0.0.1:9 立即拒连;基座只报 proxy_*)。"""
        yaml_path = write_plugin(tmp_path, "proxy.yaml", PROXY_YAML)
        config_path = tmp_path / "global.yaml"
        config_path.write_text('pools:\n  main: "http://127.0.0.1:9"\n')
        code = main([
            "run", yaml_path, "--db", str(tmp_path / "db.sqlite"),
            "--config", str(config_path), "--json",
        ])
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_FETCH_ALL_FAILED
        error_types = {f["error_type"] for f in payload["sources"][0]["failures"]}
        assert "proxy_config" not in error_types  # 不再是「未注入 pools」
        assert error_types & {"proxy_error", "proxy_network", "proxy_timeout"}

    def test_run_rejects_unknown_config_file(self, tmp_path, capsys):
        yaml_path = write_plugin(tmp_path, "proxy.yaml", PROXY_YAML)
        code = main([
            "run", yaml_path, "--db", str(tmp_path / "db.sqlite"),
            "--config", str(tmp_path / "nope.yaml"), "--json",
        ])
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_CONFIG_ERROR
        assert payload["error"] == "config"

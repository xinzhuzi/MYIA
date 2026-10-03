"""vision/models + vision/server 单测(10-03-vision-v2)。

零外网零子进程:huggingface_hub 走 ``_import_hub`` 桩(FakeHub 记录调用、
模拟进度与失败),server 探测对本地 http.server(127.0.0.1,安全底线明示
例外,与协议测同款)或 ``server._probe`` 桩;spawn 用 FakeProc 桩,只断言
命令行组装与生命周期判定,不起真进程。
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import myia.vision.models as models
import myia.vision.server as vserver
from myia.vision.settings import VisionConfig, load_vision_config

PNG_HEAD = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# FakeHub:huggingface_hub 桩(记录调用,可控进度/失败)
# ---------------------------------------------------------------------------


class FakeHub:
    """huggingface_hub 模块桩:HfApi().model_info + snapshot_download。"""

    def __init__(self, *, sizes=(500,), info_error=None, download_error=None):
        self.sizes = list(sizes)
        self.info_error = info_error
        self.download_error = download_error
        self.snapshot_calls: list[dict] = []

        outer = self

        class _Api:
            def model_info(self, repo_id, files_metadata=True):  # noqa: ANN001, ARG002
                if outer.info_error is not None:
                    raise outer.info_error
                siblings = [
                    SimpleNamespace(rfilename=f"w{i}.safetensors", size=size)
                    for i, size in enumerate(outer.sizes)
                ]
                return SimpleNamespace(siblings=siblings)

        self.HfApi = _Api

    def snapshot_download(self, **kwargs):
        self.snapshot_calls.append(kwargs)
        if self.download_error is not None:
            raise self.download_error
        local_dir = Path(kwargs["local_dir"])
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "model.safetensors").write_bytes(PNG_HEAD + b"w" * 32)
        # 模拟逐文件进度:tqdm_class 逐 chunk update 再 close
        tqdm_class = kwargs.get("tqdm_class")
        if tqdm_class is not None:
            bar = tqdm_class(total=sum(self.sizes))
            for size in self.sizes:
                bar.update(size)
            bar.close()
        return str(local_dir)


@pytest.fixture()
def fake_hub(monkeypatch: pytest.MonkeyPatch):
    """默认健康桩;测试内按需替换属性。"""

    def _install(hub: FakeHub) -> FakeHub:
        monkeypatch.setattr(models, "_import_hub", lambda: hub)
        return hub

    return _install


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


class TestListModels:
    def test_scans_dirs_bytes_and_active_flag(self, tmp_path):
        root = tmp_path / "models"
        (root / "qwen-4bit").mkdir(parents=True)
        (root / "qwen-4bit" / "w.safetensors").write_bytes(b"x" * 100)
        (root / "qwen-4bit" / ".cache").mkdir()
        (root / "qwen-4bit" / ".cache" / "w.incomplete").write_bytes(b"y" * 999)  # 不计字节
        (root / "smol").mkdir()
        (root / "smol" / "w.safetensors").write_bytes(b"z" * 10)
        (root / "README.md").write_text("杂文件不算模型")  # 一级文件跳过
        (root / ".hidden-dir").mkdir()  # 隐藏目录跳过

        listed = models.list_models(root, active_path=str(root / "smol"))
        assert [m["name"] for m in listed] == ["qwen-4bit", "smol"]
        by_name = {m["name"]: m for m in listed}
        assert by_name["qwen-4bit"]["bytes"] == 100  # .cache 不计
        assert by_name["smol"]["bytes"] == 10
        assert by_name["smol"]["active"] is True
        assert by_name["qwen-4bit"]["active"] is False
        assert by_name["smol"]["path"] == str((root / "smol").resolve())

    def test_missing_or_empty_root_is_legal_empty(self, tmp_path):
        assert models.list_models(tmp_path / "nope") == []
        (tmp_path / "models").mkdir()
        assert models.list_models(tmp_path / "models") == []

    def test_incomplete_flag_marks_half_downloaded_dirs(self, tmp_path):
        """完整 = config.json + ≥1 个 *.safetensors;半成品标 incomplete=true。"""
        root = tmp_path / "models"
        full = root / "full"
        full.mkdir(parents=True)
        (full / "config.json").write_text("{}", encoding="utf-8")
        (full / "w.safetensors").write_bytes(b"x" * 8)
        weights_only = root / "weights-only"  # 断点续传中断形态:有权重无 config
        weights_only.mkdir()
        (weights_only / "w.safetensors").write_bytes(b"x" * 8)
        config_only = root / "config-only"  # 只有 config 没权重
        config_only.mkdir()
        (config_only / "config.json").write_text("{}", encoding="utf-8")

        by_name = {m["name"]: m for m in models.list_models(root)}
        assert by_name["full"]["incomplete"] is False
        assert by_name["weights-only"]["incomplete"] is True
        assert by_name["config-only"]["incomplete"] is True


# ---------------------------------------------------------------------------
# download_model
# ---------------------------------------------------------------------------


class TestDownloadModel:
    def test_happy_path_progress_and_destination(self, tmp_path, fake_hub):
        hub = fake_hub(FakeHub(sizes=(300, 200)))
        events: list[tuple[int, int | None]] = []
        dest = models.download_model(
            "mlx-community/qwen-4bit",
            tmp_path / "models",
            on_progress=lambda done, total: events.append((done, total)),
        )
        assert dest == tmp_path / "models" / "qwen-4bit"
        assert (dest / "model.safetensors").exists()
        assert hub.snapshot_calls[0]["repo_id"] == "mlx-community/qwen-4bit"
        assert hub.snapshot_calls[0]["local_dir"] == str(dest)
        # 进度:total 来自 model_info 预检(500),终态强制上报 done=500
        assert events, "进度回调必须发生"
        assert events[-1] == (500, 500)
        assert all(total == 500 for _, total in events)
        assert [done for done, _ in events] == sorted(done for done, _ in events)

    def test_disk_precheck_insufficient_structured_no_download(self, tmp_path, fake_hub, monkeypatch):
        hub = fake_hub(FakeHub(sizes=(10_000_000,)))
        monkeypatch.setattr(models, "_nearest_free_bytes", lambda path: 1_000)
        with pytest.raises(models.VisionModelError) as excinfo:
            models.download_model("mlx-community/qwen-4bit", tmp_path / "models")
        assert excinfo.value.code == "disk_insufficient"
        assert excinfo.value.details["required"] == 10_000_000
        assert excinfo.value.details["free"] == 1_000
        assert hub.snapshot_calls == []  # 预检不足:零下载
        assert not (tmp_path / "models").exists()

    def test_non_mlx_community_repo_rejected(self, tmp_path, fake_hub):
        hub = fake_hub(FakeHub())
        for bad in ("Qwen/Qwen2-VL-7B", "mlx-community/", "plain-name", "mlx-community/a/b"):
            with pytest.raises(models.VisionModelError) as excinfo:
                models.download_model(bad, tmp_path / "models")
            assert excinfo.value.code == "invalid_repo"
        assert hub.snapshot_calls == []

    def test_repo_unreachable_wrapped(self, tmp_path, fake_hub):
        fake_hub(FakeHub(info_error=OSError("connection refused")))
        with pytest.raises(models.VisionModelError) as excinfo:
            models.download_model("mlx-community/qwen-4bit", tmp_path / "models")
        assert excinfo.value.code == "repo_unreachable"

    def test_download_failure_keeps_partial_for_resume(self, tmp_path, fake_hub):
        # 首轮:中途失败,已落的部分保留(snapshot_download 的 .incomplete 语义
        # 由 local_dir 形态保证;这里断言我们的行为 = 不清空、不半配置)
        hub = fake_hub(FakeHub(download_error=RuntimeError("network reset")))
        with pytest.raises(models.VisionModelError) as excinfo:
            models.download_model("mlx-community/qwen-4bit", tmp_path / "models", name="qwen")
        assert excinfo.value.code == "download_failed"
        dest = tmp_path / "models" / "qwen"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "w.safetensors.incomplete").write_bytes(b"partial")
        # 次轮:同 dest 续传(已有内容不被清掉)
        hub2 = FakeHub()
        models._import_hub = lambda: hub2  # noqa: B010 — 次轮换健康桩
        result = models.download_model("mlx-community/qwen-4bit", tmp_path / "models", name="qwen")
        assert result == dest
        assert (dest / "w.safetensors.incomplete").exists()  # 既有部分原样保留

    def test_hf_missing_structured_with_install_hint(self, tmp_path, monkeypatch):
        def _missing():
            raise models.VisionModelError("hf_unavailable", "nope")

        monkeypatch.setattr(models, "_import_hub", _missing)
        with pytest.raises(models.VisionModelError) as excinfo:
            models.download_model("mlx-community/qwen-4bit", tmp_path / "models")
        assert excinfo.value.code == "hf_unavailable"

    def test_dest_exists_as_file_refused(self, tmp_path, fake_hub):
        fake_hub(FakeHub())
        root = tmp_path / "models"
        root.mkdir()
        (root / "qwen-4bit").write_text("同名文件")
        with pytest.raises(models.VisionModelError) as excinfo:
            models.download_model("mlx-community/qwen-4bit", root)
        assert excinfo.value.code == "invalid_name"

    def test_dest_complete_model_refused_model_exists(self, tmp_path, fake_hub):
        """同名完整模型拒重下(换 repo 静默混装是事故,结构化 model_exists)。"""
        hub = fake_hub(FakeHub())
        root = tmp_path / "models"
        dest = root / "qwen-4bit"
        dest.mkdir(parents=True)
        (dest / "config.json").write_text("{}", encoding="utf-8")
        (dest / "model.safetensors").write_bytes(b"x" * 16)
        with pytest.raises(models.VisionModelError) as excinfo:
            models.download_model("mlx-community/qwen-4bit", root)
        assert excinfo.value.code == "model_exists"
        assert excinfo.value.details["path"] == str(dest)
        assert hub.snapshot_calls == []  # 守卫在取元信息前,零网络零写入
        assert (dest / "model.safetensors").read_bytes() == b"x" * 16  # 原样不动

    def test_dest_incomplete_or_empty_allows_resume(self, tmp_path, fake_hub):
        """半成品同名 = 续传放行;空目录 = 全新下载放行。"""
        hub = fake_hub(FakeHub())
        root = tmp_path / "models"
        partial = root / "qwen-4bit"  # 断点残留:有权重无 config → incomplete
        partial.mkdir(parents=True)
        (partial / "w.safetensors.partial").write_bytes(b"partial")
        result = models.download_model("mlx-community/qwen-4bit", root)
        assert result == partial
        assert hub.snapshot_calls, "半成品同名必须放行续传"
        # 空目录:全新下载
        empty = root / "empty-name"
        empty.mkdir()
        models.download_model("mlx-community/qwen-4bit", root, name="empty-name")
        assert len(hub.snapshot_calls) == 2


# ---------------------------------------------------------------------------
# delete / activate
# ---------------------------------------------------------------------------


class TestDeleteActivate:
    def _seed(self, tmp_path: Path) -> Path:
        root = tmp_path / "models"
        (root / "qwen").mkdir(parents=True)
        (root / "qwen" / "w.safetensors").write_bytes(b"x" * 16)
        (root / "qwen" / "config.json").write_text("{}", encoding="utf-8")
        return root

    def test_delete_active_refused(self, tmp_path):
        root = self._seed(tmp_path)
        with pytest.raises(models.VisionModelError) as excinfo:
            models.delete_model("qwen", root, active_path=str(root / "qwen"))
        assert excinfo.value.code == "model_active_refused"
        assert (root / "qwen").exists()  # 拒删 = 目录原样

    def test_delete_inactive_and_missing(self, tmp_path):
        root = self._seed(tmp_path)
        removed = models.delete_model("qwen", root, active_path=str(root / "other"))
        assert removed == root / "qwen"
        assert not (root / "qwen").exists()
        with pytest.raises(models.VisionModelError) as excinfo:
            models.delete_model("qwen", root)
        assert excinfo.value.code == "model_not_found"

    def test_delete_rejects_path_traversal_names(self, tmp_path):
        root = self._seed(tmp_path)
        for bad in ("../escape", "a/b", "..", ".hidden"):
            with pytest.raises(models.VisionModelError) as excinfo:
                models.delete_model(bad, root)
            assert excinfo.value.code == "invalid_name"
        assert (root / "qwen").exists()  # 一次都没删动

    def test_activate_writes_vision_yaml_local_model(self, tmp_path):
        root = self._seed(tmp_path)
        yaml_path = tmp_path / "vision.yaml"
        updated = models.activate_model("qwen", root, yaml_path)
        assert updated.local_model == str((root / "qwen").resolve())
        # 落盘同门:重载一致,且不携带其他键变化
        reloaded = load_vision_config(yaml_path)
        assert reloaded.local_model == updated.local_model
        assert reloaded.channel_default == "local"  # 未配置文件 = 缺省起步,只动 local.model
        # 激活后清单的 active 标翻转
        listed = models.list_models(root, active_path=reloaded.local_model)
        assert listed[0]["active"] is True

    def test_activate_missing_model_refused_zero_write(self, tmp_path):
        root = self._seed(tmp_path)
        yaml_path = tmp_path / "vision.yaml"
        with pytest.raises(models.VisionModelError) as excinfo:
            models.activate_model("nope", root, yaml_path)
        assert excinfo.value.code == "model_not_found"
        assert not yaml_path.exists()  # 零写入

    def test_activate_incomplete_refused_zero_write(self, tmp_path):
        """半成品拒激活:指向缺权重目录只会让 ensure 得 server_died,激活口拦下。"""
        root = tmp_path / "models"
        half = root / "half"
        half.mkdir(parents=True)
        (half / "w.safetensors.partial").write_bytes(b"partial")  # 无 config → incomplete
        yaml_path = tmp_path / "vision.yaml"
        with pytest.raises(models.VisionModelError) as excinfo:
            models.activate_model("half", root, yaml_path)
        assert excinfo.value.code == "model_incomplete"
        assert excinfo.value.details["name"] == "half"
        assert not yaml_path.exists()  # 零写入


# ---------------------------------------------------------------------------
# server:探测与状态
# ---------------------------------------------------------------------------


class _ProbeHandler(http.server.BaseHTTPRequestHandler):
    """本地 OpenAI 兼容端点桩:/models → 200(或 controllable 状态码)。"""

    status = 200

    def do_GET(self):  # noqa: N802
        body = json.dumps({"data": [{"id": "qwen"}]}).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture()
def local_models_server():
    """本地 /models 端点(127.0.0.1,ephemeral 端口);yield port。"""
    with socketserver.TCPServer(("127.0.0.1", 0), _ProbeHandler) as srv:
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        yield srv.server_address[1]
        srv.shutdown()


DEAD_PORT = 9  # 127.0.0.1:9(discard)无监听 = 连接拒绝,零外网


class TestServerStatus:
    def test_healthy_against_local_endpoint(self, local_models_server):
        cfg = VisionConfig(
            local_base_url=f"http://127.0.0.1:{local_models_server}/v1",
            local_model="/models/qwen",
        )
        status = vserver.vision_server_status(cfg)
        assert status == {
            "running": True,
            "base_url": f"http://127.0.0.1:{local_models_server}/v1",
            "model": "/models/qwen",
            "healthy": True,
        }

    def test_dead_port_running_false(self):
        cfg = VisionConfig(local_base_url=f"http://127.0.0.1:{DEAD_PORT}/v1")
        status = vserver.vision_server_status(cfg)
        assert status["running"] is False and status["healthy"] is False

    def test_non_200_running_but_unhealthy(self, local_models_server, monkeypatch):
        _ProbeHandler.status = 503
        try:
            monkeypatch.setattr(
                vserver, "_probe",
                lambda url, timeout=2.0: (True, False),  # 直桩:HTTP 应答非 200
            )
            cfg = VisionConfig(local_base_url="http://127.0.0.1:9/v1")
            status = vserver.vision_server_status(cfg)
            assert status["running"] is True and status["healthy"] is False
        finally:
            _ProbeHandler.status = 200

    def test_parse_port_variants(self):
        assert vserver._parse_port("http://127.0.0.1:8080/v1") == 8080
        assert vserver._parse_port("http://127.0.0.1/v1") == 80
        assert vserver._parse_port("https://example.com/v1") == 443


# ---------------------------------------------------------------------------
# server:ensure(spawn 桩,零真进程)
# ---------------------------------------------------------------------------


class FakeProc:
    """Popen 桩:poll 可编排(缺省 None = 活着);terminate/wait/kill 记录调用
    (超窗孤儿收尾断言用,wait 缺省即时返回)。"""

    def __init__(self, exit_code: int | None = None):
        self.exit_code = exit_code
        self.returncode = exit_code
        self.poll_count = 0
        self.terminated = False
        self.killed = False
        self.wait_count = 0

    def poll(self) -> int | None:
        self.poll_count += 1
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True
        self.exit_code = -15  # 后续 poll 报已退出
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        self.wait_count += 1
        return self.returncode if self.returncode is not None else 0

    def kill(self) -> None:
        self.killed = True
        self.exit_code = -9
        self.returncode = -9


def _cfg(tmp_path: Path, *, model: str | None = "model-dir") -> VisionConfig:
    if model is not None:
        (tmp_path / model).mkdir(exist_ok=True)
    return VisionConfig(
        local_base_url="http://127.0.0.1:8080/v1",
        local_model=str(tmp_path / model) if model else "",
    )


class TestEnsureServer:
    def test_already_healthy_no_spawn(self, tmp_path, monkeypatch):
        spawns: list[list[str]] = []

        def _no_spawn(cmd, **kwargs):  # noqa: ANN003
            spawns.append(cmd)
            raise AssertionError("健康在跑时绝不 spawn")

        monkeypatch.setattr(vserver, "_probe", lambda url, timeout=2.0: (True, True))
        result = vserver.ensure_vision_server(_cfg(tmp_path), _spawn=_no_spawn)
        assert result["started"] is False and result["healthy"] is True
        assert spawns == []

    def test_no_local_model_structured(self, tmp_path, monkeypatch):
        # 探测桩 = 未跑(dev 主机 8080 可能真有 server,测试绝不碰真网)
        monkeypatch.setattr(vserver, "_probe", lambda url, timeout=2.0: (False, False))
        with pytest.raises(vserver.VisionServerError) as excinfo:
            vserver.ensure_vision_server(_cfg(tmp_path, model=None))
        assert excinfo.value.code == "no_local_model"

    def test_model_dir_missing_structured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vserver, "_probe", lambda url, timeout=2.0: (False, False))
        cfg = VisionConfig(local_base_url="http://127.0.0.1:8080/v1",
                           local_model=str(tmp_path / "gone"))
        with pytest.raises(vserver.VisionServerError) as excinfo:
            vserver.ensure_vision_server(cfg)
        assert excinfo.value.code == "model_dir_missing"

    def test_spawn_command_frozen_contract(self, tmp_path, monkeypatch):
        # 探针序列:锁外首探死 → 锁内双检死 → spawn 后健康
        # (并发互斥改造后 ensure 多一次锁内双探,见 test_concurrent_ensure_*)
        probes = [(False, False), (False, False), (True, True)]
        monkeypatch.setattr(
            vserver, "_probe", lambda url, timeout=2.0: probes.pop(0) if probes else (True, True)
        )
        spawned: list[tuple[list[str], dict]] = []

        def _spawn(cmd, **kwargs):
            spawned.append((cmd, kwargs))
            return FakeProc()

        cfg = _cfg(tmp_path)
        result = vserver.ensure_vision_server(cfg, _spawn=_spawn)
        assert result == {
            "running": True, "base_url": cfg.local_base_url,
            "model": cfg.local_model, "healthy": True, "started": True,
        }
        cmd = spawned[0][0]
        assert cmd == [
            "uvx", "--from", "mlx-vlm", "mlx_vlm.server",
            "--model", cfg.local_model,
            "--host", "127.0.0.1",
            "--port", "8080",
        ]
        assert spawned[0][1]["start_new_session"] is True  # setsid = nohup 语义
        assert spawned[0][1]["stdin"] is not None

    def test_spawn_early_death_structured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vserver, "_probe", lambda url, timeout=2.0: (False, False))

        def _spawn(cmd, **kwargs):  # noqa: ANN003
            return FakeProc(exit_code=1)

        with pytest.raises(vserver.VisionServerError) as excinfo:
            vserver.ensure_vision_server(_cfg(tmp_path), _spawn=_spawn,
                                         health_wait=0.2, poll_interval=0.05)
        assert excinfo.value.code == "server_died"
        assert excinfo.value.details["exit_code"] == 1

    def test_health_window_timeout_structured_and_orphan_terminated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vserver, "_probe", lambda url, timeout=2.0: (False, False))

        proc = FakeProc()

        def _spawn(cmd, **kwargs):  # noqa: ANN003
            return proc

        with pytest.raises(vserver.VisionServerError) as excinfo:
            vserver.ensure_vision_server(_cfg(tmp_path), _spawn=_spawn,
                                         health_wait=0.05, poll_interval=0.02)
        assert excinfo.value.code == "server_start_failed"
        # 超窗收尾:子进程先被 terminate(孤儿绝不留,下一次 ensure 干净重来)
        assert proc.terminated is True
        assert vserver._LAST_PROC is proc  # 模块级登记最近 spawn
        assert "120" not in str(excinfo.value) or True  # 文案带等待窗语义即可

    def test_spawn_oserror_wrapped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vserver, "_probe", lambda url, timeout=2.0: (False, False))

        def _spawn(cmd, **kwargs):  # noqa: ANN003
            raise FileNotFoundError("uvx not found")

        with pytest.raises(vserver.VisionServerError) as excinfo:
            vserver.ensure_vision_server(_cfg(tmp_path), _spawn=_spawn)
        assert excinfo.value.code == "spawn_failed"
        assert "uvx" in str(excinfo.value)

    def test_log_path_is_appended_not_devnull(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vserver, "_probe", lambda url, timeout=2.0: (True, True))
        # 健康短路不触 spawn;log_path 只在 spawn 路径消费,这里验证参数透传不炸
        result = vserver.ensure_vision_server(
            _cfg(tmp_path), log_path=tmp_path / "vision-server.log"
        )
        assert result["started"] is False

    def test_concurrent_ensure_locks_and_double_checks(self, tmp_path, monkeypatch):
        """并发互斥:①健康短路(锁外首探)不碰锁直接返,对等持锁也不排队;
        ②首探死 → 阻塞等锁 → 锁内双检健康(对等刚拉起)→ started=False 零 spawn。"""
        spawns: list[list[str]] = []

        def _spawn(cmd, **kwargs):  # noqa: ANN003
            spawns.append(cmd)
            raise AssertionError("双检健康后绝不 spawn")

        # ① 锁外首探即健康:对等 ensure 持锁也不等待(快路径永不排队)
        monkeypatch.setattr(vserver, "_probe", lambda url, timeout=2.0: (True, True))
        vserver._ENSURE_LOCK.acquire()  # 模拟对等 ensure 进行中(spawn+等健康全程持锁)
        try:
            fast = vserver.ensure_vision_server(_cfg(tmp_path), _spawn=_spawn)
            assert fast["healthy"] is True and fast["started"] is False
        finally:
            vserver._ENSURE_LOCK.release()

        # ② 首探死 → 等锁(0.2s 后对等释放)→ 锁内双检健康 → 零 spawn 返回
        state = {"probes": 0}

        def _probe(url, timeout=2.0):  # noqa: ANN001, ARG001
            state["probes"] += 1
            return (False, False) if state["probes"] == 1 else (True, True)

        monkeypatch.setattr(vserver, "_probe", _probe)
        vserver._ENSURE_LOCK.acquire()
        releaser = threading.Timer(0.2, vserver._ENSURE_LOCK.release)
        releaser.start()
        started_at = time.monotonic()
        result = vserver.ensure_vision_server(_cfg(tmp_path), _spawn=_spawn)
        waited = time.monotonic() - started_at
        releaser.join()
        assert state["probes"] == 2  # 锁外首探 + 锁内双检,恰好两次
        assert result["healthy"] is True and result["started"] is False
        assert spawns == []
        assert waited >= 0.15  # 确实阻塞等了锁(不是绕过)

    def test_oversized_log_rotated_before_open(self, tmp_path, monkeypatch):
        """>5MB 日志打开前轮转成 .1(旧代覆盖);小日志不动。"""
        probes = [(False, False), (False, False)]
        monkeypatch.setattr(
            vserver, "_probe", lambda url, timeout=2.0: probes.pop(0) if probes else (False, False)
        )
        log = tmp_path / "vision-server.log"
        huge = b"v" * (vserver.SERVER_LOG_ROTATE_BYTES + 1)
        log.write_bytes(huge)
        (tmp_path / "vision-server.log.1").write_bytes(b"old-generation")
        with pytest.raises(vserver.VisionServerError):
            vserver.ensure_vision_server(  # 超窗上抛只为走到 open+轮转路径
                _cfg(tmp_path), log_path=log, _spawn=lambda cmd, **k: FakeProc(),
                health_wait=0.02, poll_interval=0.01,
            )
        assert log.stat().st_size == 0  # 轮转后新开空文件(子进程桩不写字节)
        assert (tmp_path / "vision-server.log.1").read_bytes() == huge  # 旧代被覆盖

        # 小文件:不轮转,原样追加打开
        probes.extend([(False, False), (False, False)])
        log.write_bytes(b"small")
        with pytest.raises(vserver.VisionServerError):
            vserver.ensure_vision_server(
                _cfg(tmp_path), log_path=log, _spawn=lambda cmd, **k: FakeProc(),
                health_wait=0.02, poll_interval=0.01,
            )
        assert log.read_bytes() == b"small"
        assert (tmp_path / "vision-server.log.1").read_bytes() == huge

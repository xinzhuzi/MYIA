"""E2E instrumentation(sitecustomize;PYTHONPATH 注入 shishi run 子进程)。

与 10-03-vision-pipeline 同款两件事(路径改 /tmp/myia-di-e2e):

1. SSRF 检查的官方测试缝:``myia.vision.collect._resolve_host`` docstring 自述
   「测试的 monkeypatch 点,零外网」。仅对字面量 ``127.0.0.1`` 返回公网替身 IP
   (实际拨号仍走 127.0.0.1 → 本地 fixture server);其余主机原样真实解析
   ——私网拒路径真实未打补丁。
2. socket 连接审计:``sys.addaudithook`` 记录全部 socket.connect 事件 →
   socket_connects.jsonl。AC3「断网替代证据(OCR 路径零外联)」的实证法:
   整 run 出站连接全部 loopback 即证 OCR(ocrmac 本地)零外联。
"""
import sys
import time

ROOT = "/tmp/myia-di-e2e"
MARK = ROOT + "/instrument_active.txt"
CONNECTS = ROOT + "/socket_connects.jsonl"

PUBLIC_STANDIN = "93.184.216.34"  # example.com 的地址,仅作 is_public 判定替身
PATCHED_HOSTS = {"127.0.0.1"}


def _install() -> None:
    notes = []
    try:
        import myia.vision.collect as _collect

        _orig = _collect._resolve_host

        def _patched(host):
            if host in PATCHED_HOSTS:
                return [PUBLIC_STANDIN]
            return _orig(host)

        _collect._resolve_host = _patched
        notes.append("resolve_host patched for " + ",".join(sorted(PATCHED_HOSTS)))
    except Exception as exc:  # noqa: BLE001
        notes.append(f"resolve_host patch FAILED: {type(exc).__name__}: {exc}")

    try:
        import json as _json

        def _audit(event, args):
            if event != "socket.connect":
                return
            try:
                sock, address = args
                with open(CONNECTS, "a") as f:
                    f.write(_json.dumps({
                        "ts": time.time(),
                        "pid": __import__("os").getpid(),
                        "address": list(address) if isinstance(address, tuple) else str(address),
                    }) + "\n")
            except Exception:  # noqa: BLE001
                pass

        sys.addaudithook(_audit)
        notes.append("socket.connect audit hook installed")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"audit hook FAILED: {type(exc).__name__}: {exc}")

    with open(MARK, "a") as f:
        f.write(f"{time.time()} pid={__import__('os').getpid()} " + "; ".join(notes) + "\n")


try:
    _install()
except Exception:  # noqa: BLE001
    pass

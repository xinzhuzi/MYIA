"""插件侧密钥库(keystore)——指纹→全文的本地凭证库(猎→存→验通路)。

背景:猎手(ghhunt)产 items 时出于 Q9 掩码红线「全文永不进 item/模板」
(items 的额外字段会全量进 metadata、进而合并进推送模板上下文,引擎路径
没有「只进库不进模板」的安全通道),全文密钥在装配函数里用完掩码/指纹
后即丢弃——「猎→存→验」价值链断在第一环,credcheck 之后无从取原文。
本模块即补上的**本地密钥库**:猎手命中时以指纹为主键把全文落进本机
JSON 文件,credcheck 阶段读库探测后回填 ``check_state``。

边界与红线(与插件 README 授权定位一致):

- **数据域不出本机**:库文件落在 ``MYIA_HOME`` 环境变量(优先)或当前
  工作目录(开发态,仓库根)下的 ``credhunter-keystore.json``,权限
  600(posix);不外发、不入 git;
- **不改变 Q9 对外口径**:items/推送模板/stdout 仍然掩码-only,全文只
  存在于本文件;密钥本体一律经参数注入,模块不读环境变量里的密钥、
  不硬编码任何凭据;
- **原子读写**:读全量→更新→同目录 tmp 文件写 + ``os.replace`` 就位,
  崩溃不留半份 JSON;并发简单化——进程内 ``threading.Lock`` + 文件级
  best-effort(单实例锁已在应用层保证,勿过度工程)。

模块自包含(插件子模块互不 import 的同一架构纪律,stdlib-only);
猎手侧经参数/工厂注入使用(ghhunt ``keystore=`` 形参,duck-typed
``record``),验证侧由 :func:`credcheck.run_from_keystore` 组合。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "KEYSTORE_FILENAME",
    "KEYSTORE_VERSION",
    "SOURCES_LIMIT",
    "Keystore",
    "KeystoreError",
    "default_keystore_path",
]

#: 库文件名(落在 MYIA_HOME 或 cwd 下;数据域,不出本机)。
KEYSTORE_FILENAME = "credhunter-keystore.json"

#: 库结构版本(结构不符即结构化报错,不做静默迁移)。
KEYSTORE_VERSION = 1

#: 每指纹来源 URL 去重列表上限(防长尾刷库)。
SOURCES_LIMIT = 20

#: 进程内互斥(单文件读改写的串行化;跨进程是 best-effort,见模块 docstring)。
_LOCK = threading.Lock()


class KeystoreError(ValueError):
    """结构化密钥库错误:code + message + details(``to_dict()`` 进 JSON/错误面)。

    刻意继承 ValueError:适配器 ``run``/``fetch_hunt`` 的既有错误通道按
    ValueError 收口(getattr(exc, "code", …) 保留结构化码)。
    """

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """结构化形态:{"code", "message", **details}。"""
        return {"code": self.code, "message": self.message, **self.details}


def default_keystore_path() -> Path:
    """库文件缺省位置:``MYIA_HOME`` 环境变量(优先)或当前工作目录。

    调用时读取(非导入期),测试可用 monkeypatch 重定向;开发态无
    MYIA_HOME 时落在 cwd(仓库根),与 MYIA 的桌面/开发双态一致。
    """
    home = os.environ.get("MYIA_HOME")
    base = Path(home).expanduser() if home else Path.cwd()
    return base / KEYSTORE_FILENAME


def _now_iso() -> str:
    """UTC ISO-8601 时间戳(记录字段统一形态)。"""
    return datetime.now(timezone.utc).isoformat()


class Keystore:
    """指纹→全文的本地密钥库(JSON 文件,600 权限,原子读写)。

    结构:``{"version": 1, "records": {fingerprint: {"apikey", "provider",
    "first_seen", "last_seen", "hit_count", "sources", "last_check",
    "check_state"}}}``;构造即确保文件存在(缺则建档并 chmod 600)。
    """

    def __init__(self, path: str | Path, *, sources_limit: int = SOURCES_LIMIT) -> None:
        self.path = Path(path)
        self._sources_limit = sources_limit
        parent = self.path.parent
        if not parent.is_dir():
            parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"version": KEYSTORE_VERSION, "records": {}})

    def load(self) -> dict[str, dict[str, Any]]:
        """读全量 records(指纹→记录映射;调用方不得改动后当真值写回)。"""
        with _LOCK:
            return self._read()["records"]

    def record(self, fingerprint: str, apikey: str, provider: str, source_url: str = "") -> None:
        """一次命中入库(原子读改写):新指纹建档,旧指纹刷新计数/来源。

        Args:
            fingerprint: 密钥指纹(sha256 前 16 hex,猎手侧用
                :func:`findings.key_fingerprint` 产)。
            apikey: 全文密钥(**只落本库**,永不进 items/模板)。
            provider: 供应商归因。
            source_url: 命中位置证据 URL(去重入 ``sources``,上限 20)。
        """

        def _apply(records: dict[str, dict[str, Any]]) -> None:
            now = _now_iso()
            existing = records.get(fingerprint)
            if isinstance(existing, dict):
                existing["apikey"] = apikey
                existing["provider"] = provider
                existing["last_seen"] = now
                existing["hit_count"] = int(existing.get("hit_count", 0) or 0) + 1
                sources = [source for source in existing.get("sources") or [] if isinstance(source, str)]
                if source_url and source_url not in sources and len(sources) < self._sources_limit:
                    sources.append(source_url)
                existing["sources"] = sources
                return
            records[fingerprint] = {
                "apikey": apikey,
                "provider": provider,
                "first_seen": now,
                "last_seen": now,
                "hit_count": 1,
                "sources": [source_url] if source_url else [],
                "last_check": None,
                "check_state": None,
            }

        self._mutate(_apply)

    def update_check(self, fingerprint: str, state: str) -> None:
        """验证结果回填(``last_check`` + ``check_state``)。

        state 取 credcheck 三态词表(final_verified/rejected/transient);
        指纹不存在报结构化错(库被外部改动时不静默吞)。
        """

        def _apply(records: dict[str, dict[str, Any]]) -> None:
            existing = records.get(fingerprint)
            if not isinstance(existing, dict):
                raise KeystoreError(
                    "fingerprint_unknown", f"密钥库无此指纹,无法回填:{fingerprint}", fingerprint=fingerprint
                )
            existing["last_check"] = _now_iso()
            existing["check_state"] = state

        self._mutate(_apply)

    # ---- 内部:读 / 写 / 读改写(均持进程内锁) ----

    def _mutate(self, apply: Callable[[dict[str, dict[str, Any]]], None]) -> None:
        """读全量 → 更新 → 原子写回(apply 抛错则不落盘)。"""
        with _LOCK:
            data = self._read()
            apply(data["records"])
            self._write(data)

    def _read(self) -> dict[str, Any]:
        """读库并校验结构;竞态下文件消失按空库处理(下次写入重建)。"""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"version": KEYSTORE_VERSION, "records": {}}
        except OSError as exc:
            raise KeystoreError("keystore_unreadable", f"密钥库不可读:{self.path}({exc})", path=str(self.path)) from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise KeystoreError("keystore_invalid", f"密钥库不是合法 JSON:{self.path}", path=str(self.path)) from exc
        if (
            not isinstance(data, dict)
            or data.get("version") != KEYSTORE_VERSION
            or not isinstance(data.get("records"), dict)
        ):
            raise KeystoreError(
                "keystore_invalid",
                f"密钥库结构不符(期望 version={KEYSTORE_VERSION} + records 映射):{self.path}",
                path=str(self.path),
            )
        return data

    def _write(self, data: dict[str, Any]) -> None:
        """原子写:同目录 tmp → fsync → chmod 600 → ``os.replace`` 就位。"""
        try:
            handle_fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=self.path.name, suffix=".tmp")
        except OSError as exc:
            raise KeystoreError("keystore_unwritable", f"密钥库目录不可写:{self.path.parent}({exc})", path=str(self.path)) from exc
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)  # posix:就位前先收紧权限(rename 不改模式位)
            os.replace(tmp, self.path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise KeystoreError("keystore_unwritable", f"密钥库不可写:{self.path}({exc})", path=str(self.path)) from exc

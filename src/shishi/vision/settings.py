"""Vision settings — ``MYIA_HOME/vision.yaml`` 的装载与同门校验。

结构配置落 ``<home>/vision.yaml``(MYIA_HOME 第一个全局配置文件,独立先行,
task 10-03-image-input);凭据铁律与 enrich 同门(security-baseline):云端
``api_key`` 只收 ``keychain:`` 引用 —— 明文与 ``env:`` 一律拒载,桌面端经
``secret.set`` 录入钥匙串 ``myia/image/api_key``(与协议 ``image.*`` 同层命名,
不另造前缀)。配置文件出现明文凭据 = 拒载零写入,绝无半载状态。

文件不存在 = 全缺省(看图屏显示「未配置」引导去设置,不崩);存在即过全字
段门:枚举(channel/engine)、http(s) 端点、keychain 引用语法、未知字段拒收
(手滑写错字段名不会静默失效)。装载与保存共用同一道门 —— :meth:`save_vision_config`
只接收已构造的 :class:`VisionConfig`(构造即校验),失败在进入本模块的写路径
之前就已发生。

缺省本地端点带 ``/v1`` 后缀:OpenAI 兼容 SDK 以 ``{base_url}/chat/completions``
发请求,而 mlx-vlm 端点是 ``:8080/v1/chat/completions``(local-ocr 技能实证),
不带后缀会打到 404。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from shishi.schema import (
    CredentialResolveError,
    SchemaValueError,
    parse_secret_value,
    resolve_credential,
)

__all__ = [
    "CHANNELS",
    "DEFAULT_CLOUD_BASE_URL",
    "DEFAULT_CLOUD_MODEL",
    "DEFAULT_LOCAL_BASE_URL",
    "KEYCHAIN_API_KEY",
    "KEYCHAIN_LLM_API_KEY",
    "OCR_ENGINES",
    "VISION_FILE_NAME",
    "VisionConfig",
    "VisionConfigError",
    "load_vision_config",
    "resolve_cloud_api_key",
    "save_vision_config",
]

#: vision.yaml 文件名(置于应用数据根 <home>/ 下;路径解析在 desktop/entry.py)。
VISION_FILE_NAME = "vision.yaml"
#: 云端 api_key 的缺省钥匙链名(桌面端 secret.set 录入;canonical myia/<scope>/<name>)。
KEYCHAIN_API_KEY = "myia/image/api_key"
#: 既有 GLM 凭据链路(设置屏 LLM 表单经 secret.set 录入,enrich 同链)——云端
#: 看图未录专用 key 时的回落解析点;「云默认 glm-4.6v 走既有 GLM 凭据链路」落点。
KEYCHAIN_LLM_API_KEY = "myia/llm/api_key"

#: 二级看图通道(local=本地 OpenAI 兼容端点零出网;cloud=显式切换才出网)。
CHANNELS = ("local", "cloud")
#: 一级 OCR 引擎(macOS Vision 默认;RapidOCR 第二引擎,10-03 修订两个都要)。
OCR_ENGINES = ("vision", "rapidocr")

DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_CLOUD_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_CLOUD_MODEL = "glm-4.6v"  # grill 已拍板;glm-4.5v 错读勿用(local-ocr 横测存档)

#: 协议文档允许的顶层键(from_payload 未知键拒收的对照名单)。
_TOP_KEYS = ("channel_default", "local", "cloud", "ocr")
_LOCAL_KEYS = ("base_url", "model")
_CLOUD_KEYS = ("base_url", "model", "api_key")
_OCR_KEYS = ("enabled", "engine_default")


class VisionConfigError(ValueError):
    """Vision 配置拒载(结构化:code + message + details;镜像 EnrichConfigError)。

    Attributes:
        code: ``invalid_channel`` / ``invalid_engine`` / ``invalid_base_url`` /
            ``credential_plaintext`` / ``invalid_credential_ref`` /
            ``unknown_field`` / ``invalid_field`` / ``vision_unreadable``。
        details: 结构化上下文(至少带 ``field`` 字段路径);协议层(desktop/
            entry.py)翻译为 ``image_config_invalid``。永不携带已解析的凭据值。
    """

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form(协议错误 data / 诊断消费)。"""
        return {"error_type": self.code, "message": str(self), **self.details}


@dataclass(frozen=True)
class VisionConfig:
    """看图配置(``vision.yaml`` 的内存形态;构造即校验,非法值拒构造)。

    云端 ``api_key`` 只承载 ``keychain:`` 引用(明文/env: 拒);``local_model``
    即本地模型路径(mlx-vlm 的 model 字段天然是路径,grill「设置本地模型路径」
    的落点),允许空 = 未配置(analyze 提交时结构化拒绝,不静默)。
    """

    channel_default: str = "local"
    local_base_url: str = DEFAULT_LOCAL_BASE_URL
    local_model: str = ""
    cloud_base_url: str = DEFAULT_CLOUD_BASE_URL
    cloud_model: str = DEFAULT_CLOUD_MODEL
    cloud_api_key_ref: str | None = None
    ocr_enabled: bool = True
    ocr_engine_default: str = "vision"

    def __post_init__(self) -> None:
        if self.channel_default not in CHANNELS:
            raise VisionConfigError(
                "invalid_channel",
                f"channel_default 必须是 {'/'.join(CHANNELS)},当前为 {self.channel_default!r}",
                details={"field": "channel_default"},
            )
        if self.ocr_engine_default not in OCR_ENGINES:
            raise VisionConfigError(
                "invalid_engine",
                f"ocr.engine_default 必须是 {'/'.join(OCR_ENGINES)},当前为 {self.ocr_engine_default!r}",
                details={"field": "ocr.engine_default"},
            )
        for field in ("local_base_url", "cloud_base_url"):
            value = getattr(self, field)
            if not value.startswith(("http://", "https://")):
                raise VisionConfigError(
                    "invalid_base_url",
                    f"{field} 必须是 http(s) 端点(OpenAI 兼容 base_url,本地含 /v1 路径),当前为 {value!r}",
                    details={"field": field},
                )
        if not self.cloud_model:
            raise VisionConfigError(
                "invalid_field",
                "cloud.model 不能为空(缺省 glm-4.6v)",
                details={"field": "cloud.model"},
            )
        if self.cloud_api_key_ref is not None:
            _require_keychain_ref(self.cloud_api_key_ref)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> VisionConfig:
        """从协议/文档字典构造(未知键拒收、类型逐字段校验;缺省键可省)。"""
        if not isinstance(payload, Mapping):
            raise VisionConfigError(
                "vision_unreadable", "vision 配置必须是对象", details={"field": "$"}
            )
        _reject_unknown(payload, _TOP_KEYS, "$")

        local = _section(payload, "local", _LOCAL_KEYS)
        cloud = _section(payload, "cloud", _CLOUD_KEYS)
        ocr = _section(payload, "ocr", _OCR_KEYS)

        channel_default = payload.get("channel_default", "local")
        if channel_default is not None and not isinstance(channel_default, str):
            raise VisionConfigError(
                "invalid_channel",
                f"channel_default 必须是字符串,当前为 {channel_default!r}",
                details={"field": "channel_default"},
            )
        api_key = cloud.get("api_key")
        if api_key is not None and (not isinstance(api_key, str) or not api_key):
            raise VisionConfigError(
                "invalid_field",
                "cloud.api_key 必须是 keychain: 引用字符串或 null",
                details={"field": "cloud.api_key"},
            )
        ocr_enabled = ocr.get("enabled")
        if ocr_enabled is not None and not isinstance(ocr_enabled, bool):
            raise VisionConfigError(
                "invalid_field",
                f"ocr.enabled 必须是布尔,当前为 {ocr_enabled!r}",
                details={"field": "ocr.enabled"},
            )
        return cls(
            channel_default=channel_default or "local",
            local_base_url=_string(local, "local", "base_url") or DEFAULT_LOCAL_BASE_URL,
            local_model=_string(local, "local", "model") or "",
            cloud_base_url=_string(cloud, "cloud", "base_url") or DEFAULT_CLOUD_BASE_URL,
            cloud_model=_string(cloud, "cloud", "model") or DEFAULT_CLOUD_MODEL,
            cloud_api_key_ref=api_key,
            ocr_enabled=True if ocr_enabled is None else ocr_enabled,
            ocr_engine_default=_string(ocr, "ocr", "engine_default") or "vision",
        )

    def to_payload(self) -> dict[str, Any]:
        """协议视图(嵌套原文形状;keychain 引用原样回显,永不携带解析值)。"""
        return {
            "channel_default": self.channel_default,
            "local": {"base_url": self.local_base_url, "model": self.local_model},
            "cloud": {
                "base_url": self.cloud_base_url,
                "model": self.cloud_model,
                "api_key": self.cloud_api_key_ref,
            },
            "ocr": {"enabled": self.ocr_enabled, "engine_default": self.ocr_engine_default},
        }


def _require_keychain_ref(value: str) -> None:
    """cloud.api_key 只收纯 ``keychain:`` 引用(security-baseline:凭据禁明文)。"""
    try:
        ref = parse_secret_value(value, label="cloud.api_key", allow_scheme=False)
    except SchemaValueError as exc:
        code = "credential_plaintext" if exc.code == "credential_plaintext" else "invalid_credential_ref"
        raise VisionConfigError(
            code,
            f"cloud.api_key {exc}(只接受 keychain: 引用;桌面端经 secret.set 录入 {KEYCHAIN_API_KEY})",
            details={"field": "cloud.api_key"},
        ) from exc
    if ref.kind != "keychain":
        raise VisionConfigError(
            "invalid_credential_ref",
            f"cloud.api_key 只接受 keychain: 引用(收到 {value!r});桌面端经 secret.set 录入 {KEYCHAIN_API_KEY}",
            details={"field": "cloud.api_key"},
        )


def _reject_unknown(mapping: Mapping[str, Any], allowed: tuple[str, ...], path: str) -> None:
    unknown = sorted(key for key in mapping if key not in allowed)
    if unknown:
        raise VisionConfigError(
            "unknown_field",
            f"{path} 存在未知字段: {unknown}(允许:{list(allowed)};手滑字段名不会静默失效)",
            details={"field": path, "unknown": unknown},
        )


def _section(payload: Mapping[str, Any], name: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    """取一个子节(None=缺省;非对象/未知键结构化拒收)。"""
    raw = payload.get(name)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise VisionConfigError(
            "invalid_field", f"{name} 必须是对象,当前为 {raw!r}", details={"field": name}
        )
    _reject_unknown(raw, allowed, name)
    return dict(raw)


def _string(section: Mapping[str, Any], section_name: str, key: str) -> str | None:
    """子节内字符串字段(None=缺省;非字符串结构化拒收)。"""
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise VisionConfigError(
            "invalid_field",
            f"{section_name}.{key} 必须是字符串,当前为 {value!r}",
            details={"field": f"{section_name}.{key}"},
        )
    return value


def load_vision_config(path: Path | str) -> VisionConfig:
    """装载 ``vision.yaml``;文件不存在 = 全缺省(合法未配置态,不报错)。

    Raises:
        VisionConfigError: ``vision_unreadable``(文件不可读/YAML 无法解析/非对象)
            或构造校验的任一 code —— fail fast,绝不半载。
    """
    file_path = Path(path)
    if not file_path.exists():
        return VisionConfig()
    try:
        doc = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise VisionConfigError(
            "vision_unreadable",
            f"vision 配置无法解析: {file_path} ({exc})",
            details={"file": str(file_path)},
        ) from exc
    if doc is None:  # 空文件 = 未配置态,与缺文件同权
        return VisionConfig()
    return VisionConfig.from_payload(doc)


def save_vision_config(path: Path | str, config: VisionConfig) -> Path:
    """原子落盘(同目录临时文件 + ``os.replace``);入参已过构造校验,写路径零再判。

    调用方(desktop/entry.py ``image.config.save``)先 :meth:`VisionConfig.from_payload`
    构造,失败即零写入 —— 本函数只在配置合法时被到达。
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(config.to_payload(), allow_unicode=True, sort_keys=False)
    tmp = file_path.with_name(file_path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(tmp, file_path)
    return file_path


def resolve_cloud_api_key(
    config: VisionConfig, *, backend: Any | None = None
) -> str | None:
    """解析云端看图 key:显式 ``cloud.api_key`` 引用优先,缺省回落既有 GLM 链路。

    解析序(2026-10-03 收口拍板:云默认 ``glm-4.6v`` 走既有 GLM 凭据链路):

    1. ``vision.yaml`` 显式 ``cloud.api_key``(``keychain:myia/image/api_key`` 等)
       —— 配了专用 key 就用它;解析失败**原样上抛**由调用方按通道语义翻译
       结构化错误(如 ``credential_resolve_failed``),与显式配置必须 fail fast
       的门风一致;
    2. 未配专用 key → 回落 ``keychain:myia/llm/api_key``(设置屏 LLM 表单录入、
       enrich 同链的既有 GLM 凭据)——已配 GLM 的主机云端看图开箱即用,免二次
       录 key。回落段任何解析失败(未录/无钥匙链后端/操作失败)都只是「无 key」
       返回 ``None``,不报错:回落是便利不是契约。

    Args:
        config: 已过构造校验的看图配置。
        backend: 注入钥匙链后端(测试 ``InMemoryKeychainBackend``);``None`` =
            系统钥匙链惰性发现(与 :func:`shishi.schema.resolve_credential` 同参)。

    Returns:
        已解析的 key;两条链路都无 key 时 ``None``(调用方按 ``无凭据`` 降级)。

    Raises:
        CredentialResolveError: 仅第 1 步(显式引用)解析失败时。
    """
    if config.cloud_api_key_ref:
        return resolve_credential(config.cloud_api_key_ref, backend=backend)
    try:
        return resolve_credential(f"keychain:{KEYCHAIN_LLM_API_KEY}", backend=backend)
    except CredentialResolveError:
        # 回落段失败 = 无 key(未录/无后端都算),不是错误路径。
        return None

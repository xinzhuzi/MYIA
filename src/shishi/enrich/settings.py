"""Enrich endpoint settings: the OpenAI-compatible endpoint comes only from
explicit credential references (grill Q6).

MYIA has **no built-in endpoint and no default key**: ``base_url`` and
``api_key`` must each be an ``env:VAR`` / ``keychain:NAME`` reference.
Plaintext (or a missing reference) is a structured :class:`EnrichConfigError`
at construction time — fail fast, never half-configure the second funnel.

References are validated here (syntax) and resolved by :class:`LLMEnricher`
at construction (concrete values), so a broken enrich config surfaces as a
startup failure instead of a mid-run surprise.

``schema.EnrichConfig`` carries the endpoint itself (``base_url`` / ``api_key``
as pure ``env:``/``keychain:`` references); :class:`EnrichSettings` mirrors it
for the constructor-injection path (``Pipeline(..., enrich_settings=...)`` —
tests/programmatic use). When omitted, the pipeline builds settings from the
schema ``enrich:`` section; missing values surface as
``missing_base_url`` / ``missing_api_key`` at construction either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shishi.enrich.errors import EnrichConfigError
from shishi.schema import parse_secret_value, resolve_credential

__all__ = [
    "DEFAULT_COMPLETION_TIMEOUT_SECONDS",
    "EnrichSettings",
    "resolve_endpoint",
]

#: Per-completion timeout (seconds); one slow LLM call must not eat the whole
#: analyze stage budget (the stage-level cap in the pipeline stays the outer
#: guard).
DEFAULT_COMPLETION_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class EnrichSettings:
    """Endpoint configuration for :class:`LLMEnricher` (constructor-injected).

    Args:
        base_url_ref: credential reference (``env:VAR``) pointing at an
            OpenAI-compatible base URL, e.g. ``env:MYIA_LLM_BASE_URL``.
            Required — ``None`` or plaintext raises :class:`EnrichConfigError`.
        api_key_ref: credential reference for the API key. Required —
            MYIA 无默认 key (grill Q6); a keyless local endpoint still sets a
            placeholder via its env var.
        timeout_seconds: per-completion timeout in seconds.
        prompt_path: override for the bundled scoring prompt data file
            (反馈闭环 v0.3 调优入口); ``None`` uses the packaged default.
        prompt_version: cache-fingerprint input for the scoring prompt;
            overriding it invalidates every cached score without touching the
            data file.
        dedupe_prompt_path: override for the bundled event-dedup prompt data
            file (v0.4 事件聚合, PRD 10-01-v04-event-aggregation;同一反馈调优
            入口); ``None`` uses the packaged default.
        dedupe_prompt_version: cache-fingerprint input for the event-dedup
            prompt; overriding it invalidates cached pair verdicts without
            touching the data file.

    Raises:
        EnrichConfigError: ``missing_base_url`` / ``missing_api_key`` (no
            reference) or ``credential_plaintext`` / ``invalid_credential_ref``
            (the value is not a pure ``env:``/``keychain:`` reference).
    """

    base_url_ref: str | None = None
    api_key_ref: str | None = None
    timeout_seconds: float = DEFAULT_COMPLETION_TIMEOUT_SECONDS
    prompt_path: str | Path | None = None
    prompt_version: int | None = None
    dedupe_prompt_path: str | Path | None = None
    dedupe_prompt_version: int | None = None

    def __post_init__(self) -> None:
        if self.base_url_ref is None:
            raise EnrichConfigError(
                "missing_base_url",
                "enrich.base_url 缺失:MYIA 无内置 LLM 端点,必须显式配置为 "
                "env: 凭据引用(如 env:MYIA_LLM_BASE_URL,grill Q6)",
                details={"field": "enrich.base_url"},
            )
        self._require_ref("base_url", self.base_url_ref)
        if self.api_key_ref is None:
            raise EnrichConfigError(
                "missing_api_key",
                "enrich.api_key 缺失:MYIA 无默认 key,必须显式配置为 "
                "env:/keychain: 凭据引用(如 env:MYIA_LLM_KEY)",
                details={"field": "enrich.api_key"},
            )
        self._require_ref("api_key", self.api_key_ref)
        if self.timeout_seconds <= 0:
            raise EnrichConfigError(
                "invalid_timeout",
                f"enrich 超时必须为正数,得到 {self.timeout_seconds}",
                details={"field": "enrich.timeout_seconds"},
            )

    @staticmethod
    def _require_ref(field_name: str, value: str) -> None:
        """Refuse plaintext/non-reference values (安全性底线:凭据禁明文)."""
        try:
            parse_secret_value(value, label=f"enrich.{field_name}", allow_scheme=False)
        except Exception as exc:  # SchemaValueError → 结构化包装,保留错误链
            raise EnrichConfigError(
                getattr(exc, "code", "invalid_credential_ref"),
                f"enrich.{field_name} 必须是纯 env:/keychain: 凭据引用,"
                f"当前值不含有效凭据引用(grill Q6:MYIA 无内置端点、无默认 key)",
                details={"field": f"enrich.{field_name}"},
            ) from exc


def resolve_endpoint(settings: EnrichSettings) -> tuple[str, str]:
    """Resolve endpoint references to concrete ``(base_url, api_key)`` values.

    Shared construction-time resolution for every LLM consumer of the enrich
    endpoint (:class:`~shishi.enrich.LLMEnricher` scoring and the v0.4
    :class:`~shishi.enrich.aggregate.EventAggregator` dedup — 同一端点、同一份
    凭据解析契约). Structured failure with reference *names* only — resolved
    values never appear in errors or logs (日志不含凭据值).

    Raises:
        EnrichConfigError: ``credential_unresolved`` (env var missing /
            keychain unsupported) or ``invalid_base_url`` (resolved endpoint
            is not http(s)).
    """
    base_url = _resolve_ref("base_url", settings.base_url_ref)
    api_key = _resolve_ref("api_key", settings.api_key_ref)
    if not base_url.startswith(("http://", "https://")):
        raise EnrichConfigError(
            "invalid_base_url",
            f"enrich.base_url 解析结果不是 http(s) 地址(当前以 {base_url[:8]!r} 开头)"
            "(内网地址可用,但必须是合法 http(s) 端点)",
            details={"field": "enrich.base_url"},
        )
    return base_url, api_key


def _resolve_ref(field_name: str, ref: str | None) -> str:
    assert ref is not None  # EnrichSettings.__post_init__ 已保证
    try:
        return resolve_credential(ref)
    except Exception as exc:  # CredentialResolveError → 结构化包装,保留错误链
        raise EnrichConfigError(
            "credential_unresolved",
            f"enrich.{field_name} 凭据引用无法解析: {exc}",
            details={"field": f"enrich.{field_name}", "ref": ref},
        ) from exc

"""验证层供应商规格 loader 与 resolve(credhunter R3)。

数据文件:data/provider_specs.yaml(25 个规格)。本模块装形状校验、
:class:`ProviderResolver.resolve` 归因与官方基址回填 —— **加供应商 =
在数据文件加一节,零代码改动**(任务 10-03-aipocket-fusion R3 扩展纪律)。

行为规格(behavior-specs/fingerprints.md §1/§4/§5/§6 + credcheck.md §2):
- 一个规格 = name/category/domain_suffixes/key_prefixes/protocol/models/
  official_api_url;**无内嵌示例请求** —— 请求形状由 credcheck 阶段按
  protocol + 供应商硬编码装配(本骨架不含出网);
- resolve 归因顺序:**域名优先**(reason="domain",host 等于后缀或以
  ``.`` + 后缀结尾;aws_bedrock 特判:host 以 ``.amazonaws.com`` 结尾且
  以 ``bedrock.``/``bedrock-`` 开头)→ **密钥前缀兜底**(reason=
  "key_prefix")→ **unknown**(reason="unknown",apiurl 回落官方基址,
  官方基址也为空则空串,验证层据此判 "no_api_url");
- 泛前缀纪律:``sk-`` 一类会吞掉所有 sk-* 网关键的泛前缀刻意不进
  key_prefixes(数据文件已注释),域名兜底才是主通道。

校验纪律:未知字段 fail-fast、name 唯一、category/protocol 走封闭词表,
坏数据在加载期报结构化错 :class:`SpecDataError`。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

__all__ = [
    "CATEGORY_TOKENS",
    "PROTOCOL_TOKENS",
    "SPECS_DATA_FILE",
    "ProviderResolver",
    "ProviderProfile",
    "Resolution",
    "SpecDataError",
    "load_specs",
]

#: 数据文件缺省位置(与本文件同目录的 data/ 下)。
SPECS_DATA_FILE = Path(__file__).resolve().parent / "data" / "provider_specs.yaml"

#: 验证层类别封闭词表(规格 §1:international|domestic|gateway|coding_agent|cloud)。
CATEGORY_TOKENS = ("international", "domestic", "gateway", "coding_agent", "cloud")

#: 协议族封闭词表(规格 §1:决定验证请求形状;snake_case 即上游五族的 MYIA 写法)。
PROTOCOL_TOKENS = ("openai_compatible", "anthropic", "gemini", "vertex", "aws_bedrock")

#: 规格名标识符规则(与 packs.id 同一口径)。
_SPEC_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: 域名后缀必须形如裸域(host 等于后缀、或以 "."+后缀 结尾才算命中)。
_DOMAIN_SUFFIX_RE = re.compile(r"^[a-z0-9.-]+$")


class SpecDataError(ValueError):
    """specs 数据文件的结构化加载错误(code + message,加载期 fail-fast)。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProviderProfile:
    """一个验证层供应商规格(字段语义见数据文件头注与模块 docstring)。"""

    name: str
    category: str
    domain_suffixes: tuple[str, ...] = field(default=())
    key_prefixes: tuple[str, ...] = field(default=())
    protocol: str = "openai_compatible"
    models: tuple[str, ...] = field(default=())
    official_api_url: str = ""


@dataclass(frozen=True)
class Resolution:
    """resolve 的产物:归因结果 + 选定的 API 基址。

    apiurl 语义:传入 apiurl 优先(猎取命中项带 URL 时直接用);否则回落
    规格 official_api_url;仍为空(如 azure_openai)则空串 —— 验证层据此
    判 rejected/"no_api_url"(credcheck.md §2)。
    """

    provider: str
    reason: str            # domain | key_prefix | unknown
    spec: ProviderProfile | None
    apiurl: str


def _host_of(apiurl: str) -> str:
    """提取 apiurl 的 host(小写、去尾点;无 scheme 的裸域也按 host 处理)。"""
    candidate = (apiurl or "").strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    return (parsed.hostname or "").lower().rstrip(".")


class ProviderResolver:
    """验证层规格注册表:域名 → 前缀 → unknown 三级归因。"""

    def __init__(self, specs: Sequence[ProviderProfile]) -> None:
        self._specs: dict[str, ProviderProfile] = {spec.name: spec for spec in specs}

    def spec(self, name: str) -> ProviderProfile | None:
        """按名取规格;不存在返回 None。"""
        return self._specs.get(name)

    @property
    def names(self) -> list[str]:
        """全部规格名(保持构造顺序)。"""
        return list(self._specs)

    def _match_domain(self, host: str) -> ProviderProfile | None:
        """域名后缀匹配(host 等于后缀、或以 .后缀 结尾);aws_bedrock 特判在前。"""
        if not host:
            return None
        bedrock = self._specs.get("aws_bedrock")
        if bedrock is not None and host.endswith(".amazonaws.com"):
            stem = host[: -len(".amazonaws.com")]
            if stem == "bedrock" or stem.startswith("bedrock.") or stem.startswith("bedrock-"):
                return bedrock
        for spec in self._specs.values():
            if spec.name == "aws_bedrock":
                # amazonaws.com 上还有 S3/EC2 等无数子域:aws_bedrock 的域名
                # 匹配只认上面的 bedrock./bedrock- 特判,不走泛后缀通道。
                continue
            for suffix in spec.domain_suffixes:
                if host == suffix or host.endswith(f".{suffix}"):
                    return spec
        return None

    def _match_prefix(self, apikey: str) -> ProviderProfile | None:
        """密钥前缀匹配(泛前缀已在数据层排除,首命中即归因)。"""
        if not apikey:
            return None
        for spec in self._specs.values():
            if any(apikey.startswith(prefix) for prefix in spec.key_prefixes):
                return spec
        return None

    def resolve(self, apiurl: str = "", apikey: str = "") -> Resolution:
        """归因一个 (apiurl, apikey) 组合:域名优先、前缀兜底、否则 unknown。

        apiurl 回填顺序:传入值 > spec.official_api_url > 空串。
        """
        spec = self._match_domain(_host_of(apiurl))
        reason = "domain"
        if spec is None:
            spec = self._match_prefix(apikey)
            reason = "key_prefix" if spec is not None else "unknown"
        fallback = spec.official_api_url if spec is not None else ""
        return Resolution(
            provider=spec.name if spec is not None else "unknown",
            reason=reason,
            spec=spec,
            apiurl=(apiurl or "").strip() or fallback,
        )


def _validate_string_list(
    spec_name: str, field_name: str, value: Any, errors: list[str], *, pattern: re.Pattern[str] | None = None
) -> tuple[str, ...]:
    """校验一个字符串数组字段(可带元素正则),返回 tuple 化结果。"""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        errors.append(f"specs[{spec_name!r}].{field_name} 必须是字符串数组,当前为 {type(value).__name__}")
        return ()
    cleaned: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"specs[{spec_name!r}].{field_name}[{index}] 必须是非空字符串")
            continue
        item = item.strip()
        if pattern is not None and not pattern.match(item):
            errors.append(f"specs[{spec_name!r}].{field_name}[{index}] 形态非法: {item!r}")
            continue
        cleaned.append(item)
    return tuple(cleaned)


def load_specs(path: str | Path | None = None) -> list[ProviderProfile]:
    """读入并校验验证层数据文件,返回全部规格(保持文件内顺序)。

    Raises:
        SpecDataError: 文件缺失/非映射/根键错/规格形状坏/name 重复/词表外
            取值 —— 一次性收集全部错误再抛。
    """
    data_file = Path(path) if path is not None else SPECS_DATA_FILE
    if not data_file.is_file():
        raise SpecDataError("specs_file_missing", f"验证层数据文件不存在: {data_file}")
    try:
        raw = yaml.safe_load(data_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecDataError("specs_yaml_invalid", f"验证层数据文件不是合法 YAML: {exc}") from exc
    if not isinstance(raw, Mapping) or "specs" not in raw:
        raise SpecDataError("specs_root_invalid", "验证层数据文件必须是含 specs: 列表的映射")
    unknown_root = set(raw) - {"specs"}
    if unknown_root:
        raise SpecDataError("specs_unknown_root_key", f"验证层数据文件出现未知根键: {sorted(unknown_root)}")
    entries = raw["specs"]
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise SpecDataError("specs_not_list", "specs: 必须是列表")
    errors: list[str] = []
    specs: list[ProviderProfile] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            errors.append(f"specs[{index}] 必须是映射,当前为 {type(entry).__name__}")
            continue
        unknown = set(entry) - {"name", "category", "domain_suffixes", "key_prefixes", "protocol", "models", "official_api_url"}
        if unknown:
            errors.append(f"specs[{index}] 出现未知字段: {sorted(unknown)}")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not _SPEC_NAME_RE.match(name):
            errors.append(f"specs[{index}].name 必须是小写字母/数字/连字符/下划线标识符,当前为 {name!r}")
            continue
        if name in seen_names:
            errors.append(f"specs[{index}].name 重复: {name!r}")
            continue
        seen_names.add(name)
        category = entry.get("category")
        if category not in CATEGORY_TOKENS:
            errors.append(f"specs[{name!r}].category 取值 {category!r} 不在词表 {list(CATEGORY_TOKENS)} 内")
            continue
        protocol = entry.get("protocol")
        if protocol not in PROTOCOL_TOKENS:
            errors.append(f"specs[{name!r}].protocol 取值 {protocol!r} 不在词表 {list(PROTOCOL_TOKENS)} 内")
            continue
        url = entry.get("official_api_url", "")
        if not isinstance(url, str):
            errors.append(f"specs[{name!r}].official_api_url 必须是字符串(允许空串),当前为 {url!r}")
            continue
        specs.append(
            ProviderProfile(
                name=name,
                category=category,
                domain_suffixes=_validate_string_list(name, "domain_suffixes", entry.get("domain_suffixes", []), errors, pattern=_DOMAIN_SUFFIX_RE),
                key_prefixes=_validate_string_list(name, "key_prefixes", entry.get("key_prefixes", []), errors),
                protocol=protocol,
                models=_validate_string_list(name, "models", entry.get("models", []), errors),
                official_api_url=url.strip(),
            )
        )
    if errors:
        raise SpecDataError("specs_invalid", "; ".join(errors))
    return specs

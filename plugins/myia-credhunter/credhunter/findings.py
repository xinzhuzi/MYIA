"""凭证命中物 → MYIA 管线 items(items 形状 + Q9 掩码政策)。

items 契约(对齐 :meth:`shishi.pipeline.Item.from_extracted`):
- ``url`` 必填(命中位置的证据 URL,GitHub 泳道即命中项 html_url);
- ``title``/``source``/``content`` 为管线已知键,其余字段全部落 metadata
  (对 dedup 模板/classify 规则/推送路由可见);
- dedup 键是**结构化字段组合** :data:`DEDUP_KEY_TEMPLATE` = 供应商 +
  密钥指纹(sha256 前 16 hex,同 key 稳定、异 key 区分、不泄露明文),
  ``{title}`` 永不进键(dedup.py 的注册表本身也会拒)。

Q9 掩码政策(任务 grill 决议,硬红线):
- **items 与一切推送模板只允许掩码形态** —— :func:`mask_apikey` 前 8
  后 4(≤12 位只留头段,≤8 位仅留前 2 + 省略号),全文密钥**永不**进
  item 任何角落(含 content 摘录,:func:`redact_secrets` 先行替换);
- store 内部存全文是 credcheck 阶段(读库→探测→回填)的内部字段语义,
  属后续件;本模块产出的对外形状一律掩码。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "DEDUP_KEY_TEMPLATE",
    "MASK_HEAD",
    "MASK_TAIL",
    "build_finding_item",
    "dedup_key",
    "find_full_key_leak",
    "key_fingerprint",
    "mask_apikey",
    "redact_secrets",
]

#: 掩码保留头部长度(Q9:前 8)。
MASK_HEAD = 8

#: 掩码保留尾部长度(Q9:后 4)。
MASK_TAIL = 4

#: 掩码省略号形态(占位符,不是任何真实密钥字符)。
_ELLIPSIS = "…MASKED…"

#: 命中物 item 的 dedup 键模板:供应商 + 密钥指纹(结构化字段组合,
#: {title} 永不进键;品类 YAML 直接引用本常量)。
DEDUP_KEY_TEMPLATE = "{provider}-{key_fingerprint}"

#: item.content 摘录的字节上限(命中上下文只保留有界片段)。
CONTEXT_EXCERPT_CHARS = 512


def mask_apikey(apikey: str) -> str:
    """Q9 掩码:前 8 后 4;过短键退化为更少暴露(防整键复现)。

    - 长度 > MASK_HEAD+MASK_TAIL(12):前 8 + 省略号 + 后 4;
    - 长度 9-12:仅前 8 + 省略号;
    - 长度 ≤ 8:仅前 2 + 省略号(这类键本身已无安全价值,但仍不整串示出)。
    """
    if len(apikey) > MASK_HEAD + MASK_TAIL:
        return f"{apikey[:MASK_HEAD]}{_ELLIPSIS}{apikey[-MASK_TAIL:]}"
    if len(apikey) > MASK_HEAD:
        return f"{apikey[:MASK_HEAD]}{_ELLIPSIS}"
    return f"{apikey[:2]}{_ELLIPSIS}"


def key_fingerprint(apikey: str) -> str:
    """密钥指纹:sha256 前 16 hex(同 key 稳定、异 key 区分、单向)。"""
    return f"sha256-{hashlib.sha256(apikey.encode('utf-8')).hexdigest()[:16]}"


def dedup_key(provider: str, apikey: str) -> str:
    """按 :data:`DEDUP_KEY_TEMPLATE` 语义渲染 dedup 键(供 CLI/引擎直接用)。"""
    return f"{provider}-{key_fingerprint(apikey)}"


def redact_secrets(text: str, secrets: Sequence[str]) -> str:
    """把文本中出现的每个全文密钥替换为其掩码形态(content 摘录红线)。"""
    result = text
    for secret in secrets:
        if secret:
            result = result.replace(secret, mask_apikey(secret))
    return result


def _walk_strings(value: Any) -> list[str]:
    """递归收集结构里的全部字符串(items 泄漏自检用)。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        collected: list[str] = []
        for item in value.values():
            collected.extend(_walk_strings(item))
        return collected
    if isinstance(value, (list, tuple, set, frozenset)):
        collected = []
        for item in value:
            collected.extend(_walk_strings(item))
        return collected
    return []


def find_full_key_leak(item: Mapping[str, Any], apikey: str) -> list[str]:
    """自检:item(含嵌套)里是否出现全文密钥;返回出现位置的键路径列表。

    红线兜底:build_finding_item 已保证不写入全文;本函数供测试与调用方
    防御性复核(Q9:全文永不进 items/推送模板上下文)。
    """
    if not apikey:
        return []
    leaked: list[str] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                _walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                _walk(value, f"{path}[{index}]")
        elif isinstance(node, str) and apikey in node:
            leaked.append(path)

    _walk(item, "")
    return leaked


def build_finding_item(
    *,
    apikey: str,
    provider: str,
    source_url: str,
    apiurl: str = "",
    source_type: str = "manual",
    matched_by: str = "joint",
    variable: str | None = None,
    file_path: str | None = None,
    context_excerpt: str | None = None,
    source: str = "credhunter",
) -> dict[str, Any]:
    """把一次密钥命中装配成管线 item(url 必填、全文永不入 item)。

    Args:
        apikey: 全文密钥(仅在本函数作用域内用于掩码与指纹,不落 item)。
        provider: 供应商归因(未归因传 "unknown")。
        source_url: 命中位置证据 URL(item.url;GitHub 泳道即命中项 html_url)。
        apiurl: 归因出的官方 API 地址(可空)。
        source_type: 命中泳道(code_snapshot/commit_message/manual/…)。
        matched_by: 指纹层命中方式(joint/fine/joint+fine)。
        variable: 命中处赋值变量名(若有)。
        file_path: 命中文件路径(若有)。
        context_excerpt: 命中上下文摘录(函数内先 :func:`redact_secrets`
            再截断到 :data:`CONTEXT_EXCERPT_CHARS`)。
        source: item.source 标识(缺省 "credhunter")。

    Returns:
        管线 item dict:url/title/source/content + metadata 字段(provider/
        apikey_masked/key_fingerprint/apiurl/matched_by/source_type/
        file_path/variable/dedup 建议键字段齐备)。

    Raises:
        ValueError: source_url 为空(管线 Item 层同样会拒,提前到装配期)。
    """
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError(f"命中物缺少有效 source_url(item.url 必填),当前为 {source_url!r}")
    masked = mask_apikey(apikey)
    content: str | None = None
    if context_excerpt:
        redacted = redact_secrets(context_excerpt, [apikey])
        content = redacted[:CONTEXT_EXCERPT_CHARS]
    item: dict[str, Any] = {
        "url": source_url,
        "title": f"{provider} 疑似泄露凭证 {masked}({source_type})",
        "source": source,
        "provider": provider,
        "apikey_masked": masked,
        "key_fingerprint": key_fingerprint(apikey),
        "apiurl": apiurl,
        "matched_by": matched_by,
        "source_type": source_type,
    }
    if variable:
        item["variable"] = variable
    if file_path:
        item["file_path"] = file_path
    if content:
        item["content"] = content
    return item

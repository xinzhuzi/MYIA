"""发现层供应商查询包 loader(credhunter R3)。

数据文件:data/provider_packs.yaml(20 个发现包)。本模块只装形状校验
与查询池拼装 —— **加供应商 = 在数据文件加一节,零代码改动**(任务
10-03-aipocket-fusion R3 扩展纪律)。

行为规格(behavior-specs/fingerprints.md §2 + ghhunt.md §1):
- 一个包 = id + 三路查询数组(fofa/shodan/github),全静态字符串,不含
  regex/探测端点/auth 风格 —— 那些在验证层(同目录 specs.py);
- GitHub 泳道的实际查询集 = 全部包的 ``github_terms`` 拼接去重(保持
  首现顺序),每轮 run 受查询预算约束(Q8:12 条/run,游标跨轮转);
- 逐包计数(id + 三路条数)即 `queries` 诊断口径(exposure.md §6)。

校验纪律:未知字段 fail-fast、id 唯一、查询串非空且不含换行 —— 与
manifest/品类 YAML 同一纪律,坏数据在加载期报结构化错
:class:`PackDataError`(code + 明细),绝不静默吞。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "PACKS_DATA_FILE",
    "PackDataError",
    "QueryPack",
    "discovery_query_counts",
    "github_query_pool",
    "load_packs",
]

#: 数据文件缺省位置(与本文件同目录的 data/ 下)。
PACKS_DATA_FILE = Path(__file__).resolve().parent / "data" / "provider_packs.yaml"

#: 包 id 标识符规则(与 manifest 的 provides 同一口径:小写字母/数字/连字符/下划线)。
_PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: 三路查询字段名(超出即 fail-fast,防数据文件拼错键名静默漏一路)。
_QUERY_FIELDS = ("fofa_queries", "shodan_queries", "github_terms")


class PackDataError(ValueError):
    """packs 数据文件的结构化加载错误(code + message,加载期 fail-fast)。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class QueryPack:
    """一个发现层查询包:三路静态查询串(id 与验证层规格 name 对齐)。"""

    id: str
    fofa_queries: tuple[str, ...] = field(default=())
    shodan_queries: tuple[str, ...] = field(default=())
    github_terms: tuple[str, ...] = field(default=())

    def query_counts(self) -> dict[str, Any]:
        """逐路条数(`queries` 诊断口径:计数而非查询文本)。"""
        return {
            "id": self.id,
            "fofa": len(self.fofa_queries),
            "shodan": len(self.shodan_queries),
            "github": len(self.github_terms),
        }


def _validate_queries(pack_id: str, field_name: str, value: Any, errors: list[str]) -> tuple[str, ...]:
    """校验一路查询数组:必须全是非空单行字符串;返回 tuple 化结果。"""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        errors.append(f"packs[{pack_id!r}].{field_name} 必须是字符串数组,当前为 {type(value).__name__}")
        return ()
    cleaned: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"packs[{pack_id!r}].{field_name}[{index}] 必须是非空字符串")
            continue
        if "\n" in item or "\r" in item:
            errors.append(f"packs[{pack_id!r}].{field_name}[{index}] 查询串不允许换行: {item!r}")
            continue
        cleaned.append(item.strip())
    return tuple(cleaned)


def load_packs(path: str | Path | None = None) -> list[QueryPack]:
    """读入并校验发现层数据文件,返回全部包(保持文件内顺序)。

    Raises:
        PackDataError: 文件缺失/非映射/根键错/包形状坏/id 重复 —— 一次性
            收集全部错误再抛(与 manifest 加载同一纪律)。
    """
    data_file = Path(path) if path is not None else PACKS_DATA_FILE
    if not data_file.is_file():
        raise PackDataError("packs_file_missing", f"发现层数据文件不存在: {data_file}")
    try:
        raw = yaml.safe_load(data_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PackDataError("packs_yaml_invalid", f"发现层数据文件不是合法 YAML: {exc}") from exc
    if not isinstance(raw, Mapping) or "packs" not in raw:
        raise PackDataError("packs_root_invalid", "发现层数据文件必须是含 packs: 列表的映射")
    unknown_root = set(raw) - {"packs"}
    if unknown_root:
        raise PackDataError("packs_unknown_root_key", f"发现层数据文件出现未知根键: {sorted(unknown_root)}")
    entries = raw["packs"]
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise PackDataError("packs_not_list", "packs: 必须是列表")
    errors: list[str] = []
    packs: list[QueryPack] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            errors.append(f"packs[{index}] 必须是映射,当前为 {type(entry).__name__}")
            continue
        unknown = set(entry) - {"id", *_QUERY_FIELDS}
        if unknown:
            errors.append(f"packs[{index}] 出现未知字段: {sorted(unknown)}")
            continue
        pack_id = entry.get("id")
        if not isinstance(pack_id, str) or not _PACK_ID_RE.match(pack_id):
            errors.append(f"packs[{index}].id 必须是小写字母/数字/连字符/下划线标识符,当前为 {pack_id!r}")
            continue
        if pack_id in seen_ids:
            errors.append(f"packs[{index}].id 重复: {pack_id!r}")
            continue
        seen_ids.add(pack_id)
        packs.append(
            QueryPack(
                id=pack_id,
                fofa_queries=_validate_queries(pack_id, "fofa_queries", entry.get("fofa_queries", []), errors),
                shodan_queries=_validate_queries(pack_id, "shodan_queries", entry.get("shodan_queries", []), errors),
                github_terms=_validate_queries(pack_id, "github_terms", entry.get("github_terms", []), errors),
            )
        )
    if errors:
        raise PackDataError("packs_invalid", "; ".join(errors))
    return packs


def github_query_pool(packs: Sequence[QueryPack]) -> list[str]:
    """全部包 github_terms 拼接去重(保持首现顺序)—— GitHub 泳道查询集。"""
    seen: set[str] = set()
    pool: list[str] = []
    for pack in packs:
        for term in pack.github_terms:
            if term not in seen:
                seen.add(term)
                pool.append(term)
    return pool


def discovery_query_counts(packs: Sequence[QueryPack]) -> list[dict[str, Any]]:
    """逐包三路查询条数(`queries` 诊断口径:计数,不回显查询文本)。"""
    return [pack.query_counts() for pack in packs]

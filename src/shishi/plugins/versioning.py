"""插件版本矩阵:兼容 myia 版本范围的极简 spec 解析(核心零新依赖).

manifest 的 ``compatible`` 字段声明插件兼容的 myia 核心版本范围,语法刻意
保持 pip 风格的最小子集:逗号分隔的 AND 约束,每个约束是 ``>=`` / ``>`` /
``<=`` / ``<`` / ``==`` + 版本号(如 ``>=0.3,<0.5``)。不引入 ``packaging``
库 —— 新核心依赖必须进 PRD 论程(spec python/index 依赖红线)。

版本号解析为整数元组后按位比较,缺位补 0(``0.3`` 等价 ``0.3.0``),与
``shishi.__version__`` 的三段式语义兼容。解析 fail-fast 且一次性报告全部坏
约束(AI 生成的 spec 要能一次改完)。
"""

from __future__ import annotations

import re

__all__ = ["VersionRange", "VersionSpecError", "compare_versions", "parse_version"]

_VERSION_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\s*$")
# 两字符操作符先匹配,避免 ">=" 被拆成 ">" + "=0.3"。
_OPERATORS = (">=", "<=", "==", ">", "<")


class VersionSpecError(ValueError):
    """版本号或版本范围 spec 非法。

    Attributes:
        code: ``invalid_version`` | ``invalid_version_range``。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_version(value: str) -> tuple[int, ...]:
    """Parse a version string into an int tuple (``"0.3.2"`` -> ``(0, 3, 2)``).

    Raises:
        VersionSpecError: 不是 1-4 段点分数字的形式。
    """
    if not isinstance(value, str):
        raise VersionSpecError("invalid_version", f"版本号应为字符串,当前为 {value!r}")
    match = _VERSION_RE.match(value)
    if match is None:
        raise VersionSpecError(
            "invalid_version", f"版本号应为 1-4 段点分数字(如 0.3.2),当前为 {value!r}"
        )
    return tuple(int(part) for part in match.group(1).split("."))


def compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    """按位比较两个版本元组(缺位补 0);返回 -1/0/1 对应 left 小于/等于/大于。"""
    width = max(len(left), len(right))
    padded_left = left + (0,) * (width - len(left))
    padded_right = right + (0,) * (width - len(right))
    if padded_left < padded_right:
        return -1
    if padded_left > padded_right:
        return 1
    return 0


def _satisfied(version: tuple[int, ...], op: str, bound: tuple[int, ...]) -> bool:
    order = compare_versions(version, bound)
    return {">=": order >= 0, ">": order > 0, "<=": order <= 0, "<": order < 0, "==": order == 0}[op]


class VersionRange:
    """逗号分隔的 AND 版本约束集合(``compatible`` 字段的运行形态)。

    Attributes:
        spec: 原始范围字符串(原样保留,诊断信息直接可见)。

    Raises:
        VersionSpecError: spec 为空,或任一约束缺操作符/版本号非法 ——
            全部坏约束一次性报出。
    """

    def __init__(self, spec: str) -> None:
        if not isinstance(spec, str) or not spec.strip():
            raise VersionSpecError(
                "invalid_version_range", f"版本范围不能为空,应如 '>=0.3,<0.5',当前为 {spec!r}"
            )
        self._spec = spec.strip()
        self._constraints: list[tuple[str, tuple[int, ...]]] = []
        problems: list[str] = []
        for raw in self._spec.split(","):
            constraint = raw.strip()
            op = next((candidate for candidate in _OPERATORS if constraint.startswith(candidate)), None)
            if op is None:
                problems.append(f"{constraint!r}(缺少比较操作符,支持 {'/'.join(_OPERATORS)})")
                continue
            try:
                bound = parse_version(constraint[len(op):])
            except VersionSpecError as exc:
                problems.append(f"{constraint!r}({exc})")
                continue
            self._constraints.append((op, bound))
        if problems:
            raise VersionSpecError(
                "invalid_version_range",
                f"版本范围 {self._spec!r} 有 {len(problems)} 处非法: " + "; ".join(problems),
            )

    @property
    def spec(self) -> str:
        return self._spec

    def contains(self, version: str | tuple[int, ...]) -> bool:
        """当前版本是否落在范围内(全部约束同时满足)。"""
        parsed = parse_version(version) if isinstance(version, str) else tuple(version)
        return all(_satisfied(parsed, op, bound) for op, bound in self._constraints)

    def __repr__(self) -> str:
        return f"VersionRange({self._spec!r})"

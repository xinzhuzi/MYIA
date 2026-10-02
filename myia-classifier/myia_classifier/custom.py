"""User-defined classification rules with safe ``when`` expression evaluation.

A rule pairs a ``when`` expression over item fields (e.g. ``abs(change_pct)
>= 3``) with a ``tag``. Expressions are parsed into an AST at load time and
evaluated against a whitelist -- raw ``eval()`` is never used, and unknown
constructs are rejected at construction (fail fast on config).

Allowed grammar (whitelist AST):

- literals: str / int / float / bool / None
- names: item fields (dict keys or attributes); a missing field reads as
  ``None``
- containers: list / tuple literals
- operators: ``+ - * / // % **``, unary ``- + not``, comparisons
  (``== != < <= > >= in not in is is not``, chainable), ``and`` / ``or``
- calls: positional args only, function name in the whitelist
  (abs / min / max / round / len / int / float / str)

Everything else (attribute access, subscripts, lambdas, f-strings,
comprehensions, keyword/star args, any other call target) raises
:class:`RuleSyntaxError` at construction time.

Operand-size rails: ``**`` and sequence repetition (``'a' * n``) are capped
(``_MAX_POW_RESULT_BITS`` / ``_MAX_SEQUENCE_REPEAT``) so a tiny config typo
(``9**9**9``) fails fast as :class:`RuleEvalError` instead of hanging the
event loop or exhausting memory (资源类异常同样包装,不逃出 per-item 隔离).

Error model (fail fast on config, isolate at runtime):

- config problems (syntax error, disallowed construct, unknown function)
  -> :class:`RuleSyntaxError`, raised when the Rule is built
- runtime problems (e.g. missing field feeding arithmetic, division by
  zero) -> :class:`RuleEvalError`; :meth:`Rule.evaluate` traps it, logs a
  WARNING (per logging spec: 自动恢复的异常) and treats the rule as not
  fired, so one bad rule never breaks the batch
"""

from __future__ import annotations

import ast
import logging
import operator as op
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

__all__ = [
    "ALLOWED_FUNCTIONS",
    "Rule",
    "RuleConfigError",
    "RuleEvalError",
    "RuleSyntaxError",
    "evaluate_expression",
    "load_rules",
    "rules_from_config",
]

logger = logging.getLogger(__name__)

#: Functions callable inside ``when`` expressions (all side-effect free).
ALLOWED_FUNCTIONS: dict[str, Any] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "len": len,
    "int": int,
    "float": float,
    "str": str,
}

_MAX_EXPR_LENGTH = 1000
_MAX_DEPTH = 48

_ALLOWED_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.List,
    ast.Tuple,
    ast.BoolOp,
    ast.UnaryOp,
    ast.BinOp,
    ast.Compare,
    ast.Call,
    ast.Load,
    # operator nodes appear as child nodes of the wrappers above; which
    # operator is allowed where is enforced by the per-parent checks below
    ast.And,
    ast.Or,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
)
_ALLOWED_BIN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_ALLOWED_UNARY_OPS = (ast.Not, ast.USub, ast.UAdd)
_ALLOWED_CMP_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot)
_ALLOWED_CONST_TYPES = (str, int, float, bool, type(None))

# Operand-size rails (9**9**9 这类 7 字符表达式曾把进程挂死在分钟级+GB 内存;
# 'a' * 10**14 曾以 MemoryError 打穿 per-item 隔离):远超行情/文本规则所需,
# 只拦资源级滥用,不拦任何正常规则。
_MAX_POW_RESULT_BITS = 400_000  # 幂结果位长上限(≈12 万位十进制整数)
_MAX_SEQUENCE_REPEAT = 10_000_000  # str/bytes/list 重复结果元素上限

_BIN_OPS: dict[type, Any] = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
}
_UNARY_OPS: dict[type, Any] = {ast.Not: op.not_, ast.USub: op.neg, ast.UAdd: op.pos}
_CMP_OPS: dict[type, Any] = {
    ast.Eq: op.eq,
    ast.NotEq: op.ne,
    ast.Lt: op.lt,
    ast.LtE: op.le,
    ast.Gt: op.gt,
    ast.GtE: op.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: op.is_,
    ast.IsNot: op.is_not,
}


class RuleSyntaxError(ValueError):
    """Raised when a ``when`` expression or rule config is invalid (load time).

    The message is structured: ``规则路径: 原因``, consumed by ``myia doctor``
    and AI self-repair.
    """


class RuleConfigError(ValueError):
    """Raised when the ``classify.rules`` config structure itself is invalid."""


class RuleEvalError(RuntimeError):
    """Raised when a valid expression fails at runtime on a concrete item."""


def _validate_expression(when: str, context: str) -> tuple[ast.Expression, tuple[str, ...]]:
    """Parse and whitelist-check a ``when`` expression.

    Returns:
        (AST root, referenced field names in first-seen order)

    Raises:
        RuleSyntaxError: syntax error, disallowed construct, unknown
            function, keyword/star args, or oversized expression.
    """
    if not isinstance(when, str) or not when.strip():
        raise RuleSyntaxError(f"{context}: when 表达式必须是非空字符串")
    if len(when) > _MAX_EXPR_LENGTH:
        raise RuleSyntaxError(f"{context}: when 表达式超长(>{_MAX_EXPR_LENGTH} 字符)")
    try:
        tree = ast.parse(when, mode="eval")
    except SyntaxError as exc:
        raise RuleSyntaxError(f"{context}: when 表达式语法错误: {exc.msg} (行 {exc.lineno} 列 {exc.offset})") from exc

    names: list[str] = []

    def visit(node: ast.AST, depth: int, collect: bool = True) -> None:
        if depth > _MAX_DEPTH:
            raise RuleSyntaxError(f"{context}: when 表达式嵌套过深(>{_MAX_DEPTH} 层)")
        if not isinstance(node, _ALLOWED_NODES):
            raise RuleSyntaxError(f"{context}: 不允许的表达式构造: {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, _ALLOWED_CONST_TYPES):
            raise RuleSyntaxError(f"{context}: 不允许的常量类型: {type(node.value).__name__}")
        if isinstance(node, ast.BoolOp) and not isinstance(node.op, (ast.And, ast.Or)):
            raise RuleSyntaxError(f"{context}: 不允许的布尔运算符: {type(node.op).__name__}")
        if isinstance(node, ast.UnaryOp) and not isinstance(node.op, _ALLOWED_UNARY_OPS):
            raise RuleSyntaxError(f"{context}: 不允许的一元运算符: {type(node.op).__name__}")
        if isinstance(node, ast.BinOp) and not isinstance(node.op, _ALLOWED_BIN_OPS):
            raise RuleSyntaxError(f"{context}: 不允许的算术运算符: {type(node.op).__name__}")
        if isinstance(node, ast.Compare):
            for cmp_op in node.ops:
                if not isinstance(cmp_op, _ALLOWED_CMP_OPS):
                    raise RuleSyntaxError(f"{context}: 不允许的比较运算符: {type(cmp_op).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
                raise RuleSyntaxError(f"{context}: 只允许调用白名单函数 {sorted(ALLOWED_FUNCTIONS)}")
            if node.keywords:
                raise RuleSyntaxError(f"{context}: 函数调用不允许关键字参数")
            # the function name is not an item field
            visit(node.func, depth + 1, collect=False)
            for arg in node.args:
                visit(arg, depth + 1, collect=True)
            return
        if isinstance(node, ast.Name) and collect:
            names.append(node.id)
        for child in ast.iter_child_nodes(node):
            visit(child, depth + 1, collect)

    visit(tree, 0)
    ordered_names = list(dict.fromkeys(names))
    return tree, tuple(ordered_names)


def _guarded_bin_op(op_node: ast.AST, left: Any, right: Any) -> Any:
    """Apply a whitelisted binary operator with operand-size rails.

    Raises:
        RuleEvalError: ``**`` whose integer result would exceed
            ``_MAX_POW_RESULT_BITS``, or ``*`` whose sequence repetition would
            exceed ``_MAX_SEQUENCE_REPEAT`` elements (资源滥用,不是合法规则).
    """
    if isinstance(op_node, ast.Pow):
        if (
            isinstance(left, int)
            and isinstance(right, int)
            and abs(left) > 1
            and abs(right) > 1
            and abs(left).bit_length() * abs(right) > _MAX_POW_RESULT_BITS
        ):
            raise RuleEvalError(
                f"幂运算规模超限({abs(left)}**{abs(right)} 的结果位长超 {_MAX_POW_RESULT_BITS}),"
                f"已拒绝求值以防进程挂死"
            )
    elif isinstance(op_node, ast.Mult):
        for sequence, count in ((left, right), (right, left)):
            if (
                isinstance(sequence, (str, bytes, list, tuple))
                and isinstance(count, int)
                and abs(count) * len(sequence) > _MAX_SEQUENCE_REPEAT
            ):
                raise RuleEvalError(
                    f"序列重复规模超限(len={len(sequence)} × {abs(count)} > "
                    f"{_MAX_SEQUENCE_REPEAT}),已拒绝求值以防内存耗尽"
                )
    return _BIN_OPS[type(op_node)](left, right)


def _eval_node(node: ast.AST, fields: Mapping[str, Any], depth: int) -> Any:
    """Evaluate a validated AST node against item fields.

    Raises:
        RuleEvalError: runtime failure (bad operand types, zero division,
            unexpected node) -- message carries node kind + 原因.
    """
    if depth > _MAX_DEPTH:
        raise RuleEvalError("when 表达式求值嵌套过深")
    try:
        if isinstance(node, ast.Expression):
            return _eval_node(node.body, fields, depth + 1)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in fields:
                raise RuleEvalError(f"字段缺失: {node.id}")
            return fields[node.id]
        if isinstance(node, ast.List):
            return [_eval_node(element, fields, depth + 1) for element in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(_eval_node(element, fields, depth + 1) for element in node.elts)
        if isinstance(node, ast.BoolOp):
            result: Any = isinstance(node.op, ast.And)
            for value_node in node.values:
                result = _eval_node(value_node, fields, depth + 1)
                if isinstance(node.op, ast.And) and not result:
                    return result
                if isinstance(node.op, ast.Or) and result:
                    return result
            return result
        if isinstance(node, ast.UnaryOp):
            return _UNARY_OPS[type(node.op)](_eval_node(node.operand, fields, depth + 1))
        if isinstance(node, ast.BinOp):
            left = _eval_node(node.left, fields, depth + 1)
            right = _eval_node(node.right, fields, depth + 1)
            return _guarded_bin_op(node.op, left, right)
        if isinstance(node, ast.Compare):
            left = _eval_node(node.left, fields, depth + 1)
            for cmp_op, comparator in zip(node.ops, node.comparators):
                right = _eval_node(comparator, fields, depth + 1)
                if not _CMP_OPS[type(cmp_op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            args = [_eval_node(arg, fields, depth + 1) for arg in node.args]
            return ALLOWED_FUNCTIONS[node.func.id](*args)
        raise RuleEvalError(f"不允许的表达式节点: {type(node).__name__}")
    except RuleEvalError:
        raise
    except (ArithmeticError, TypeError, ValueError, LookupError, MemoryError) as exc:
        # MemoryError 等资源类异常同样包装,绝不让它打穿 per-item 隔离契约。
        raise RuleEvalError(f"节点 {type(node).__name__} 求值失败: {exc}") from exc


@dataclass
class Rule:
    """One custom rule: ``name``, a ``when`` expression over item fields, and a ``tag``.

    The expression is validated at construction (fail fast). ``fields`` lists
    the item fields the expression references -- handy for docs and debug.

    Raises:
        RuleSyntaxError: invalid or disallowed ``when`` expression.
    """

    name: str
    when: str
    tag: str = ""
    _tree: ast.Expression | None = field(default=None, init=False, repr=False, compare=False)
    _field_names: tuple[str, ...] = field(default=(), init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._tree, self._field_names = _validate_expression(
            self.when, context=f"规则 {self.name!r} 的 when"
        )

    @property
    def fields(self) -> tuple[str, ...]:
        """Item fields referenced by the ``when`` expression, in order."""
        return self._field_names

    def evaluate(self, item: Mapping[str, Any] | object) -> bool:
        """Evaluate the ``when`` expression against one item.

        Fields are read from dict keys or attributes; a missing field reads
        as ``None``. A runtime failure logs a WARNING and returns False (the
        rule is skipped for this item) -- per-item isolation, never raises.

        Args:
            item: dict-like item or arbitrary object with attributes.
        """
        if self._tree is None:  # pragma: no cover - __post_init__ guarantees
            raise RuleEvalError(f"规则 {self.name!r} 未初始化")
        fields = {
            name: item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)
            for name in self._field_names
        }
        try:
            return bool(_eval_node(self._tree, fields, 0))
        except RuleEvalError as exc:
            logger.warning(
                "自定义规则求值失败, 本条按不命中处理: rule=%s title=%r error=%s",
                self.name,
                _item_title(item),
                exc,
            )
            return False


def _item_title(item: Mapping[str, Any] | object) -> str:
    if isinstance(item, Mapping):
        return str(item.get("title", ""))
    return str(getattr(item, "title", ""))


def evaluate_expression(when: str, fields: Mapping[str, Any]) -> Any:
    """Parse and evaluate a standalone ``when`` expression against ``fields``.

    Raises:
        RuleSyntaxError: invalid expression (config-level, fail fast).
        RuleEvalError: runtime failure on the given fields.
    """
    tree, names = _validate_expression(when, context="when 表达式")
    context = {name: fields.get(name) for name in names}
    return _eval_node(tree, context, 0)


def rules_from_config(entries: Iterable[Mapping[str, Any]] | None, source: str = "config") -> list[Rule]:
    """Build rules from raw ``classify.rules`` entries (list of mappings).

    Each entry allows exactly the fields ``name`` / ``when`` / ``tag``;
    unknown fields fail fast (schema 铁律: 未知字段不许静默忽略).

    Raises:
        RuleConfigError: entries not a list / entry not an object /
            unknown field / missing ``when``.
        RuleSyntaxError: invalid ``when`` expression (from Rule).
    """
    if entries is None:
        return []
    entry_list = list(entries)
    rules: list[Rule] = []
    for index, entry in enumerate(entry_list):
        path = f"{source}: classify.rules[{index}]"
        if not isinstance(entry, Mapping):
            raise RuleConfigError(f"{path}: 必须是对象, got {type(entry).__name__}")
        unknown = set(entry) - {"name", "when", "tag"}
        if unknown:
            raise RuleConfigError(f"{path}: 未知字段 {sorted(unknown)}")
        when = entry.get("when")
        if not isinstance(when, str) or not when.strip():
            raise RuleConfigError(f"{path}: 缺少非空 when 表达式")
        name = entry.get("name")
        if name is not None and not isinstance(name, str):
            raise RuleConfigError(f"{path}: name 必须是字符串")
        tag = entry.get("tag")
        if tag is not None and not isinstance(tag, str):
            raise RuleConfigError(f"{path}: tag 必须是字符串")
        rules.append(Rule(name=name or f"rule-{index}", when=when, tag=tag or ""))
    return rules


def load_rules(yaml_path: str) -> list[Rule]:
    """Load and validate ``classify.rules`` from a plugin YAML file.

    Raises:
        RuleConfigError: malformed config structure.
        RuleSyntaxError: invalid ``when`` expression.
    """
    import yaml  # deferred: keeps this module importable without PyYAML

    with open(yaml_path, encoding="utf-8") as file_handler:
        data = yaml.safe_load(file_handler) or {}
    classify_section = data.get("classify") if isinstance(data, Mapping) else None
    raw_rules = (classify_section or {}).get("rules") if isinstance(classify_section, Mapping) else None
    return rules_from_config(raw_rules, source=str(yaml_path))

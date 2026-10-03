"""System-keychain credential store: the ``keychain:`` leg of the三态凭据.

Credential references come in exactly three states (security-baseline spec):

- ``env:VAR`` — resolved by :func:`shishi.schema.resolve_credential`;
- ``keychain:NAME`` — resolved **here**: the system keychain via the
  ``keyring`` library (macOS Keychain, Windows DPAPI 凭据管理器, Linux
  Secret Service when a desktop keychain is present);
- plaintext — refused at YAML load time (schema layer), never stored.

Namespace: every MYIA-owned secret is ``myia/<scope>/<name>`` (scope ≈ 插件/品类
id, e.g. ``myia/stocks/linuxsb_cookie``). The namespace maps to a single keyring
service (``SECRET_SERVICE = "myia"``) with the full name as the item account, so
``security find-generic-password -s myia -a myia/stocks/linuxsb_cookie`` finds it
and Keychain Access / 凭据管理器 groups MYIA items together.

Listing: the ``keyring`` library has no enumeration API, so MYIA keeps a JSON
**name index** inside the keychain itself (account ``INDEX_ACCOUNT``). The index
stores names only — 引用名可落日志/仓库红线不涉及,值永不. ``list_secrets``
reconciles the index against the backend and self-heals stale entries (items
deleted out-of-band via Keychain Access / 凭据管理器).

Fallback (Linux 服务器/无钥匙链环境): ``env:`` 引用为主. When no keychain backend
exists, discovery raises :class:`SecretError` with code
``keychain_backend_unavailable`` and the message guides to ``env:`` — 结构化报错引导.

CLI: ``myia secret set/get/delete/list`` (separate task) is a thin wrapper over
:func:`set_secret` / :func:`get_secret` / :func:`delete_secret` /
:func:`list_secrets` — the interfaces here are the stable contract.

Testing: :class:`InMemoryKeychainBackend` is the injectable mock (secret 类测试
零真实钥匙串触碰); production paths lazily discover the ``keyring`` default.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "INDEX_ACCOUNT",
    "SECRET_SERVICE",
    "InMemoryKeychainBackend",
    "KeychainBackend",
    "SecretError",
    "delete_secret",
    "get_backend",
    "get_secret",
    "list_secrets",
    "reset_backend",
    "resolve_keychain_ref",
    "set_backend",
    "set_secret",
    "validate_secret_name",
]

#: keyring ``service`` for every MYIA item (one service, namespaced accounts).
SECRET_SERVICE = "myia"
#: reserved item account holding the JSON name index (never a user secret;
#: canonical names all start with ``myia/`` so this cannot collide).
INDEX_ACCOUNT = "__index__"

# ``myia/<scope>/<name>``: scope follows category-id rules (小写字母/数字/连字符/
# 下划线,字母数字开头), the name segment additionally allows dots (长度各 64)。
_SECRET_NAME_RE = re.compile(r"^myia/([a-z0-9][a-z0-9_-]{0,63})/([A-Za-z0-9][A-Za-z0-9_.\-]{0,63})$")


# ---------------------------------------------------------------------------
# Errors (error-handling spec: 错误类 code + 中文原因,结构化上报)
# ---------------------------------------------------------------------------


class SecretError(RuntimeError):
    """Structured keychain-store failure.

    Attributes:
        code: ``invalid_secret_name`` | ``secret_not_found`` |
            ``keychain_backend_unavailable`` | ``keychain_operation_failed``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Backends (构造注入优先;默认惰性发现 keyring 系统后端)
# ---------------------------------------------------------------------------


class KeychainBackend(Protocol):
    """Structural subset of the ``keyring`` library API this module relies on.

    Any object with these three methods is a valid backend — the real macOS /
    Windows / Linux keyring backends satisfy it, and so does
    :class:`InMemoryKeychainBackend` for tests.
    """

    def get_password(self, service: str, username: str) -> str | None:
        """Return the stored password, or ``None`` when the item is absent."""
        ...

    def set_password(self, service: str, username: str, password: str) -> None:
        """Create or overwrite the item."""
        ...

    def delete_password(self, service: str, username: str) -> None:
        """Remove the item (backends raise when it is absent)."""
        ...


class InMemoryKeychainBackend:
    """Dict-backed mock for tests (secret 类测试走 mock 钥匙串,零真实触碰).

    Mirrors real-backend observable behavior: ``get_password`` of a missing
    item returns ``None``; ``delete_password`` of a missing item raises.
    """

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self._items[(service, username)]
        except KeyError as exc:
            raise RuntimeError(f"item not found: service={service!r} username={username!r}") from exc


_default_backend: KeychainBackend | None = None


def set_backend(backend: KeychainBackend) -> None:
    """Inject a backend override (tests: mock;桌面/CLI 可显式选择钥匙链).

    The override stays until :func:`reset_backend`.
    """
    global _default_backend
    _default_backend = backend


def reset_backend() -> None:
    """Drop the injected backend; the next use re-discovers the keyring default."""
    global _default_backend
    _default_backend = None


def get_backend() -> KeychainBackend:
    """Return the injected backend, else lazily discover the keyring default.

    Raises:
        SecretError: no usable backend — ``keyring`` missing, or the host has
            no keychain (Linux 服务器常态); code
            ``keychain_backend_unavailable``, message guides to ``env:``.
    """
    global _default_backend
    if _default_backend is None:
        _default_backend = _discover_system_backend()
    return _default_backend


def _discover_system_backend() -> KeychainBackend:
    """Probe ``keyring.get_keyring()`` with a read; fail fast when unusable.

    The probe reads a never-existing item: real backends answer ``None``
    without prompting, while headless hosts (fail backend) raise — that
    distinction is exactly "可用钥匙链 vs 没有".
    """
    try:
        import keyring  # 惰性导入:保持本模块在无 keyring 环境可导入,缺失走结构化报错
    except ImportError as exc:
        raise SecretError(
            "keychain_backend_unavailable",
            "keyring 库未安装,无法访问系统钥匙链;"
            "服务端/无钥匙链环境请优先使用 env: 凭据引用",
        ) from exc
    backend = keyring.get_keyring()
    try:
        backend.get_password(SECRET_SERVICE, "__backend_probe__")
    except Exception as exc:
        raise SecretError(
            "keychain_backend_unavailable",
            f"当前环境无可用系统钥匙链(backend={type(backend).__name__}): {exc};"
            "服务端/无钥匙链环境请优先使用 env: 凭据引用",
        ) from exc
    return backend


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------


def validate_secret_name(name: str) -> str:
    """Validate the canonical ``myia/<scope>/<name>`` namespace; return the name.

    Raises:
        SecretError: code ``invalid_secret_name`` — the flat legacy form
            (``linuxsb_cookie``) and any other non-canonical name is refused
            with migration guidance (改 YAML 引用 + ``myia secret set`` 写入).
    """
    if not isinstance(name, str) or not _SECRET_NAME_RE.match(name):
        raise SecretError(
            "invalid_secret_name",
            f"钥匙链凭据名必须为 myia/<scope>/<name> 形式(按用途分组),当前为 {name!r};"
            "请把 YAML 凭据引用改成 keychain:myia/<scope>/<name>,"
            "再用 myia secret set myia/<scope>/<name> 写入钥匙链",
        )
    return name


# ---------------------------------------------------------------------------
# CRUD (``myia secret set/get/delete/list`` 的函数契约,CLI 任务直接包装)
# ---------------------------------------------------------------------------


def set_secret(name: str, value: str, *, backend: KeychainBackend | None = None) -> None:
    """Write one secret into the system keychain (首跑录入的底座).

    macOS 对既有项的跨进程原地更新可能被底层拒绝(-25244,需 GUI 授权)而
    新建正常:写入前先探测既有项并保留旧值(读失败按无既有项处理并记 warning
    留痕),更新被拒且确有既有项时回落「删旧建新」重建;重建失败尽力回写旧值,
    报错文案告知项当前状态。无既有项的失败不做回落,真实错误原样抛出。

    Args:
        name: canonical ``myia/<scope>/<name>``.
        value: the secret value — 调用方负责不落日志/仓库.
        backend: injected backend; ``None`` = system keyring discovery.

    Raises:
        SecretError: invalid name, backend unavailable, or the write failed
            (code ``keychain_operation_failed``, 底层异常以 ``from`` 链保留).
    """
    validate_secret_name(name)
    chosen = backend if backend is not None else get_backend()
    # 先探测既有项并保留旧值(回落失败时的回写底牌):读取本身失败按无既有项
    # 处理,但必须记 warning 留痕——读被拒(如 ACL)与项不存在混同会让回落被
    # 静默跳过,事后无从诊断。
    try:
        existing_value = chosen.get_password(SECRET_SERVICE, name)
    except Exception as probe_exc:
        existing_value = None
        logger.warning(
            "既有凭据探测读取失败,按无既有项处理(不做删旧建新回落) name=%s: %s",
            name,
            probe_exc,
        )
    try:
        chosen.set_password(SECRET_SERVICE, name, value)
    except Exception as exc:
        if existing_value is None:
            # 新建路径失败:不做回落,直接结构化抛出。
            raise SecretError(
                "keychain_operation_failed", f"写入系统钥匙链失败 name={name}: {exc}"
            ) from exc
        # 既有项更新被拒(典型 macOS -25244 需 GUI 授权):删旧建新重建。
        try:
            chosen.delete_password(SECRET_SERVICE, name)
            chosen.set_password(SECRET_SERVICE, name, value)
        except Exception as retry_exc:
            # 重建失败:旧值此刻可能已被删,尽力回写旧值避免凭据凭空消失,
            # 并在文案中告知项当前状态。
            try:
                chosen.set_password(SECRET_SERVICE, name, existing_value)
                restored = "项已恢复为旧值"
            except Exception as restore_exc:
                restored = f"旧值回写亦失败,该项可能已丢失: {restore_exc}"
            raise SecretError(
                "keychain_operation_failed",
                f"更新系统钥匙链既有项失败 name={name}"
                f"(删旧建新回落亦失败: {retry_exc};{restored}): {exc}",
            ) from exc
    _update_index(chosen, add=name)
    logger.info("凭据已写入系统钥匙链 name=%s service=%s(值不落日志)", name, SECRET_SERVICE)


def get_secret(name: str, *, backend: KeychainBackend | None = None) -> str:
    """Read one secret back (``keychain:NAME`` resolve-time 落点).

    Args:
        name: canonical ``myia/<scope>/<name>``.
        backend: injected backend; ``None`` = system keyring discovery.

    Returns:
        The stored secret value.

    Raises:
        SecretError: ``invalid_secret_name`` / ``secret_not_found`` /
            ``keychain_backend_unavailable`` / ``keychain_operation_failed``.
    """
    validate_secret_name(name)
    chosen = backend if backend is not None else get_backend()
    try:
        value = chosen.get_password(SECRET_SERVICE, name)
    except Exception as exc:
        raise SecretError(
            "keychain_operation_failed", f"读取系统钥匙链失败 name={name}: {exc}"
        ) from exc
    if value is None:
        raise SecretError(
            "secret_not_found",
            f"系统钥匙链 service={SECRET_SERVICE!r} 下未找到凭据 {name!r};"
            f"请先执行 myia secret set {name} 写入,或改用 env: 引用",
        )
    return value


def delete_secret(name: str, *, backend: KeychainBackend | None = None) -> None:
    """Remove one secret from the system keychain (and the name index).

    Raises:
        SecretError: ``secret_not_found`` when the item is absent (删除幂等性
            交给调用方:重复 delete 第二次报 not_found), invalid name, or the
            backend operation failed.
    """
    validate_secret_name(name)
    chosen = backend if backend is not None else get_backend()
    try:
        existing = chosen.get_password(SECRET_SERVICE, name)
    except Exception as exc:
        raise SecretError(
            "keychain_operation_failed", f"读取系统钥匙链失败 name={name}: {exc}"
        ) from exc
    if existing is None:
        raise SecretError("secret_not_found", f"系统钥匙链中未找到凭据 {name!r},无法删除")
    try:
        chosen.delete_password(SECRET_SERVICE, name)
    except Exception as exc:
        raise SecretError(
            "keychain_operation_failed", f"从系统钥匙链删除失败 name={name}: {exc}"
        ) from exc
    _update_index(chosen, remove=name)
    logger.info("凭据已从系统钥匙链删除 name=%s service=%s", name, SECRET_SERVICE)


def list_secrets(*, backend: KeychainBackend | None = None) -> list[str]:
    """List known MYIA secret names, sorted.

    Reads the JSON name index (names only, 索引项里没有值) and reconciles it
    against the backend: entries whose keychain item vanished out-of-band
    (Keychain Access / 凭据管理器手删) are dropped and the index self-heals.

    Raises:
        SecretError: backend unavailable or a backend read failed.
    """
    chosen = backend if backend is not None else get_backend()
    names = _read_index(chosen)
    alive: list[str] = []
    for name in names:
        try:
            present = chosen.get_password(SECRET_SERVICE, name) is not None
        except Exception as exc:
            raise SecretError(
                "keychain_operation_failed", f"读取系统钥匙链失败 name={name}: {exc}"
            ) from exc
        if present:
            alive.append(name)
        else:
            logger.debug("索引项失效(钥匙链中不存在),剔除 name=%s", name)
    if len(alive) != len(names):
        _write_index(chosen, alive)
    return sorted(alive)


def resolve_keychain_ref(name: str, *, backend: KeychainBackend | None = None) -> str:
    """Resolve a ``keychain:NAME`` reference to its value (schema 层入口).

    Thin named entry point over :func:`get_secret` so the schema layer has one
    call site and future policy (auditing, caching) lands in one place.
    """
    value = get_secret(name, backend=backend)
    logger.debug("钥匙链凭据引用已解析 name=%s(值不落日志)", name)
    return value


# ---------------------------------------------------------------------------
# Name index (keyring 库无枚举 API;名字存钥匙链内 JSON 项,值永不入索引)
# ---------------------------------------------------------------------------


def _read_index(backend: KeychainBackend) -> list[str]:
    """Read the name index; corrupt/absent index degrades to empty (自愈重建).

    A corrupt index must not brick ``set`` (which read-modify-writes it), so
    corruption logs a warning and starts over instead of raising.
    """
    try:
        raw = backend.get_password(SECRET_SERVICE, INDEX_ACCOUNT)
    except Exception as exc:
        raise SecretError(
            "keychain_operation_failed", f"读取钥匙链名字索引失败: {exc}"
        ) from exc
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("钥匙链名字索引损坏(JSON 解析失败),按空索引重建: %s", exc)
        return []
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        logger.warning("钥匙链名字索引格式异常(应为字符串列表),按空索引重建")
        return []
    return list(data)


def _write_index(backend: KeychainBackend, names: list[str]) -> None:
    """Overwrite the name index; failures degrade to a warning (值已写成功时
    不能因索引失败而谎报 set 失败,list 只是可能暂时缺名字)."""
    try:
        backend.set_password(SECRET_SERVICE, INDEX_ACCOUNT, json.dumps(names))
    except Exception as exc:
        logger.warning("钥匙链名字索引更新失败(list 可能缺最新名字): %s", exc)


def _update_index(
    backend: KeychainBackend,
    *,
    add: str | None = None,
    remove: str | None = None,
) -> None:
    """Read-modify-write the index for one add/remove."""
    names = _read_index(backend)
    if add is not None and add not in names:
        names.append(add)
    if remove is not None and remove in names:
        names.remove(remove)
    _write_index(backend, names)

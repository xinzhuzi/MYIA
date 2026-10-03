"""Command-line entry point (stdlib argparse, no new deps).

Exit-code contract (spec python/error-handling — agent 与 CI 依赖):

=== ====== ==============================================================
码   含义   触发
=== ====== ==============================================================
0   成功   run 正常结束(含 dry-run);``--loop`` 干净退出;init/list/doctor
            完成;secret 操作成功;``--version`` / ``--help``
1   配置错误   品类 YAML 被 schema 拒载 / 明文凭据 / route 或规则配置非法 /
            用法错误(argparse)/ 插件目录不存在 / secret 结构化失败;
            错误结构化、含字段路径
2   采集全部失败   run:所有 source 采集失败;test:被试抓的源全部失败
3   部分失败   run:部分 source/条目或通道发送失败;test:部分源失败
=== ====== ==============================================================

argparse 的默认用法错误退出码是 2,与「采集全部失败」冲突 ——
``_Parser.error`` 改为抛 :class:`_UsageError`,统一归入退出码 1。
``--help`` / ``--version`` 仍按惯例以 0 退出(经 SystemExit 传播)。

输出双形态:人类可读摘要(stdout)与 ``--json`` 机器可读单份 JSON(可被
jq 解析)。日志走 logging 框架(stderr),``--json`` 模式下日志收敛到
WARNING;stdout 推送通道的卡片行在 ``--json`` 下也改写 stderr,保证
stdout 只有一份纯 JSON(AI 消费路径)。

子命令(AI-NATIVE:每条命令的 JSON 输出即 agent 的行动依据):

- ``run``       跑一次品类流水线或常驻调度(v0.1)。
- ``init``      输出生成品类 YAML 所需的**结构化信息清单**(JSON,非人机
                问答):agent 据此向用户收集信息并生成 12 节 YAML。
- ``test``      单源试抓:打印提取字段、变更指纹与去重键预览;不推送、
                不入库(存储走 ``:memory:``,退出码沿用 0/2/3 子集语义)。
- ``list``      插件清单 + 各源健康度(状态机 ok/degraded/dead/unknown、
                引擎提示、指纹跳过率;判据看源级条目数与 skip 原因,不看
                run 级成败 —— run success 不得掩盖单源静默 0 条)。
- ``doctor``    结构化诊断:源状态机、engine_hints、凭据配置检查(env:
                存在性 + keychain 引用存在性)、代理连通性(--config 提供
                pools 声明时)、调度下次触发时间、enrich 预算/缓存;
                ``--json`` 输出,agent 据 findings 自修。诊断完成即退出
                0,发现的问题全部落在 ``findings``(healthy=false)。
- ``secret``    钥匙链凭据 set/list/delete(薄包装 myia.secrets;值永不
                回显、不落日志)。
- ``plugin``    市场插件装卸:list/install/remove(v0.3,薄包装
                myia.plugins;装卸 fail-fast 结构化拒绝,扫描零异常 ——
                任何插件装不上/配置坏都不拦核心流水线,铁律)。
- ``channels``  通道目录 refresh/list(v1.2,10-03-messaging-feishu:
                定向推送的寻址地图;refresh 调平台列表 API,失败平台保留
                旧桶并结构化上报,绝不阻塞推送)。
- ``feedback``  反馈闭环 list/stats/mark(v0.3,薄包装 myia.feedback;
                mark 是桌面形态的手动标记接收路,TG/飞书回调经 pipeline/
                回调端点入库,负反馈随维护阶段自动调参)。
- ``skill``     Agent Skill 安装通路 install/path(纯文件操作,退出码
                0/1;目标已存在默认结构化拒绝,--force 才覆盖;--link 符号
                链接;--path 自定义目录;真实安装目录按平台惯例探测)。
- ``osint``     一次性 OSINT 侦察(v1.1 源码型插件样板 myia-osint:动态
                加载插件目录 adapter.py,子进程调用上游 Photon——uv 临时
                环境隔离依赖;输出结构化 JSON;vendor 缺失/适配器不可用 →
                结构化错误,绝不拦核心流水线,铁律)。
- ``proxy``     轻量代理抓取+测活(v1.1 desktop 分级 myia-proxy:动态加载
                插件目录 adapter.py,进程内完成公开免费代理列表抓取与逐个
                测活,零 Redis 零 docker;全源失败/零可用 → 结构化错误,
                绝不拦核心流水线,铁律)。
- ``add-source`` / ``dashboard`` 为后续版本留位(结构化 not_implemented)。

源健康度判据(PRD 10-01-v02-cli-full,list/doctor 共用):

- ``ok``      本轮有产出,或 skip 原因=指纹未变(0 条合理);
- ``degraded`` 指纹未跳过却产出 0 条;或条目数 < 近 5 次基线的 50%
              (样本不足 5 次时只看 0 条判据);最新一轮失败但未满死线;
- ``dead``    连续 3 轮采集失败;
- ``unknown`` store 无该源的任何运行记录。

数据源:``runs.stats.sources``(pipeline 每轮写入的源级条目数与 skip
原因),store 已持久化,无需迁移。

Raises:
    SystemExit: ``--help`` / ``--version``(exit 0,argparse 惯例)。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import shutil
import sys
import types
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

import myia
from myia.dedup import DedupRegistry
from myia.engines.fetch_base import (
    FetchContext,
    check_proxy_connectivity,
    classify_exception,
    load_proxy_pools_file,
    mask_proxy_url,
)
from myia.engines.registry import fetch_source
from myia.feedback import (
    DEFAULT_WINDOW_DAYS as DEFAULT_TUNING_WINDOW_DAYS,
)
from myia.feedback import (
    FeedbackTuner,
    TuningPolicy,
    load_active_tuning,
    record_feedback,
    resolve_item_ref,
)
from myia.pipeline import Pipeline, build_cron_trigger
from myia.push import PLATFORMS
from myia.push.directory import ChannelDirectory
from myia.plugins import (
    InstalledPluginStore,
    PluginFinding,
    PluginStoreError,
    check_category_plugin,
    check_remote_modes,
    default_install_root,
)
from myia.schema import (  # _SECRET_REF_RE 复用:与 schema 同一引用语法,避免两处漂移
    _SECRET_REF_RE,
    CategoryConfig,
    LoadError,
    load_category_file,
)
from myia.secrets import (
    SECRET_SERVICE,
    KeychainBackend,
    SecretError,
    delete_secret,
    get_backend,
    list_secrets,
    set_secret,
    validate_secret_name,
)
from myia.store import FEEDBACK_CHANNEL_CLI, FeedbackRecord, SQLiteStore, StoreSchemaError

__all__ = ["build_parser", "main"]

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_FETCH_ALL_FAILED = 2
EXIT_PARTIAL = 3

_EXIT_BY_STATUS = {
    "success": EXIT_OK,
    "failed": EXIT_FETCH_ALL_FAILED,
    "partial": EXIT_PARTIAL,
}

DEFAULT_PLUGINS_DIR = "plugins"
DEFAULT_DB_PATH = "myia.db"
#: osint 样板插件与默认目标(PRD 10-02-v11-plugins-source-arch:合法演示域)。
OSINT_PLUGIN_ID = "myia-osint"
DEFAULT_OSINT_TARGET = "https://example.com"
#: osint 子进程 wall-clock 预算缺省(整个侦察过程,非单请求超时)。
DEFAULT_OSINT_TIMEOUT_SECONDS = 600.0
#: 采集类失败码 → 退出码 2(其余 osint 失败码归配置/环境错误,退 1)。
#: 退出码契约归 CLI 所有(spec python/error-handling),不依赖插件侧导出。
OSINT_FETCH_FAILURE_CODES = frozenset(
    {"photon_failed", "photon_timeout", "photon_export_missing", "photon_export_invalid"}
)
#: 代理池插件(myia-proxy,进程内轻量 fetcher/测活;v1.1 desktop 分级)。
PROXY_PLUGIN_ID = "myia-proxy"
#: 代理测活期望拿到的可用代理缺省数。
DEFAULT_PROXY_COUNT = 5
#: 单代理测活超时缺省秒数。
DEFAULT_PROXY_CHECK_TIMEOUT_SECONDS = 10.0
#: 采集类失败码 → 退出码 2(全部源抓取失败 / 零可用代理);其余退 1。
PROXY_FETCH_FAILURE_CODES = frozenset({"fetch_failed", "no_alive_proxy"})
#: 单源试抓的每源超时(与 pipeline fetch 阶段默认一致)。
DEFAULT_TEST_TIMEOUT_SECONDS = 120.0
#: doctor/list 回看的最大 run 数(足够「连续 3 次失败 + 近 5 次基线」判据)。
HEALTH_HISTORY_RUNS = 10
#: 基线判据的样本窗口(PRD:近 5 次基线,样本不足 5 次时只看 0 条判据)。
HEALTH_BASELINE_SAMPLES = 5
#: 基线判据的降幅阈值(PRD:条目数 < 基线的 50%)。
HEALTH_BASELINE_DROP_RATIO = 0.5

#: 变更指纹「未变」的 skip 原因词表(engines/fetch_base ChangeVerdict.reason
#: 的未变子集 + 304 协商短路)。命中即「0 条合理」。
FINGERPRINT_UNCHANGED_REASONS = frozenset({"not_modified", "validators_match", "hash_match"})

SOURCE_HEALTH_OK = "ok"
SOURCE_HEALTH_DEGRADED = "degraded"
SOURCE_HEALTH_DEAD = "dead"
SOURCE_HEALTH_UNKNOWN = "unknown"

#: 试抓结果预览的条目上限与字段值截断(提取字段预览是给 agent 看的样本)。
TEST_PREVIEW_ITEMS = 5
TEST_PREVIEW_VALUE_CHARS = 200

#: 子命令留位表(实现排期在后续版本)。
STUB_COMMANDS: dict[str, str] = {
    "add-source": "向既有插件 YAML 追加源",
    "dashboard": "品类与源健康度终端面板",
}

#: Agent Skill 安装目标(PRD 10-02-v11:至少 claude/cursor/zcode 三类;
#: agents 兜底 Codex 等通用 ~/.agents/skills 惯例)。值是技能根目录
#: (home 下),实际安装位置 = <技能根>/myia/SKILL.md。
SKILL_AGENT_DIRS: dict[str, str] = {
    "claude": "~/.claude/skills",
    "cursor": "~/.cursor/skills",
    "zcode": "~/.zcode/skills",
    "agents": "~/.agents/skills",
}
#: 技能目录名(平台惯例:技能 = 技能根下与技能同名的目录 + SKILL.md)。
SKILL_DIR_NAME = "myia"
#: SKILL.md 源位置的显式覆盖(装进 wheel 后仓库布局探测失效时的逃生口)。
SKILL_SOURCE_ENV = "MYIA_SKILL_SOURCE"


class _UsageError(Exception):
    """argparse usage error (mapped to exit code 1, structured output)."""


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose errors raise instead of ``sys.exit(2)``.

    argparse 的用法错误默认退出码 2 会与「采集全部失败」撞码;改为抛
    :class:`_UsageError`,由 :func:`main` 归入退出码 1。
    """

    def error(self, message: str) -> None:
        raise _UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``myia`` argument parser (help 文本也是 AI 的输入)."""
    parser = _Parser(
        prog="shishi",
        description="世事 — AI-native intelligence hub. 一个品类一份 YAML,fetch→classify→dedup→analyze→push。",
        epilog=(
            "退出码:0 成功 / 1 配置或用法错误 / 2 采集全部失败 / 3 部分失败。\n"
            "示例:myia run plugins/stocks.yaml --dry-run --json | jq .status\n"
            "      myia doctor --json | jq '.findings'"
        ),
    )
    parser.add_argument("--version", action="version", version=f"shishi {myia.__version__}")
    sub = parser.add_subparsers(dest="command", title="子命令")
    _add_run_parser(sub)
    _add_init_parser(sub)
    _add_test_parser(sub)
    _add_list_parser(sub)
    _add_doctor_parser(sub)
    _add_secret_parser(sub)
    _add_plugin_parser(sub)
    _add_channels_parser(sub)
    _add_feedback_parser(sub)
    _add_skill_parser(sub)
    _add_osint_parser(sub)
    _add_proxy_parser(sub)
    for name, blurb in STUB_COMMANDS.items():
        sub.add_parser(name, help=f"{blurb}(后续版本实现)")
    return parser


def _add_run_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi run``:品类流水线单次/常驻(v0.1 契约)。"""
    run = sub.add_parser(
        "run",
        help="跑一次品类流水线(--once 默认)或常驻调度(--loop)",
        description="加载插件 YAML → 按品类跑 fetch→classify→dedup→analyze→push。",
    )
    run.add_argument("yaml", help="品类 YAML 文件路径(12 节 schema)")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="跑一次后退出(默认)")
    mode.add_argument("--loop", action="store_true", help="常驻:按 schedule+timezone cron 到点自动触发")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="全链执行但不推送,不产生任何持久化副作用;输出将要推的条目 + route 判定 + skip 原因",
    )
    run.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout;stdout 推送通道的卡片行此时改写 stderr)")
    run.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite 存储路径(默认 ./{DEFAULT_DB_PATH})")
    run.add_argument(
        "--config",
        default=None,
        help="全局配置 YAML(pools 代理声明);源用 pool: 代理时必带(与 test/doctor 同一加载器)",
    )


def _add_init_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi init``:生成品类 YAML 的结构化信息清单。"""
    init = sub.add_parser(
        "init",
        help="输出生成品类 YAML 所需的结构化信息清单(JSON,供 agent 消费)",
        description=(
            "非人机问答:stdout 恒为单份 JSON(含必填/可选项、缺省值与硬规则),"
            "agent 收集信息后生成 12 节 YAML,再用 myia test 验证。"
        ),
    )
    init.add_argument("--json", dest="as_json", action="store_true", help=argparse.SUPPRESS)


def _add_test_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi test``:单源试抓(不推送不入库)。"""
    test = sub.add_parser(
        "test",
        help="单源试抓:打印提取字段与指纹结果,不推送不入库",
        description="对插件的一个或全部源走真实降级链试抓;存储走 :memory:,零持久化副作用。",
    )
    test.add_argument("yaml", help="品类 YAML 文件路径")
    test.add_argument("--source", default=None, help="只试抓指定名称的源(缺省全部源)")
    test.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TEST_TIMEOUT_SECONDS,
        help=f"每源试抓超时秒数(默认 {DEFAULT_TEST_TIMEOUT_SECONDS:.0f})",
    )
    test.add_argument("--config", default=None, help="全局配置 YAML(pools 代理声明);源用 pool: 代理时必带")
    test.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")


def _add_list_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi list``:插件清单 + 源健康度。"""
    listing = sub.add_parser(
        "list",
        help="插件清单 + 各源健康度(ok/degraded/dead/unknown)",
        description="健康度判据看源级条目数与 skip 原因,不看 run 级成败(静默 0 条不得被 success 掩盖)。",
    )
    listing.add_argument("--plugins-dir", default=DEFAULT_PLUGINS_DIR, help=f"插件目录(默认 ./{DEFAULT_PLUGINS_DIR})")
    listing.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite 存储路径(默认 ./{DEFAULT_DB_PATH})")
    listing.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")


def _add_doctor_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi doctor``:结构化诊断。"""
    doctor = sub.add_parser(
        "doctor",
        help="结构化诊断:源状态机/凭据/代理/调度,--json 供 agent 自修",
        description=(
            "诊断完成即退出 0;发现的问题全部落在 findings(healthy=false)。"
            "覆盖:插件加载(含明文凭据拒载)、源健康度、engine_hints、凭据引用"
            "(env: 存在性 + keychain: 引用存在性)、代理连通性(--config)、下次触发时间、enrich 预算/缓存。"
        ),
    )
    doctor.add_argument("yaml", nargs="*", help="要体检的品类 YAML;缺省扫描 --plugins-dir 下全部插件")
    doctor.add_argument("--plugins-dir", default=DEFAULT_PLUGINS_DIR, help=f"插件目录(默认 ./{DEFAULT_PLUGINS_DIR})")
    doctor.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite 存储路径(默认 ./{DEFAULT_DB_PATH})")
    doctor.add_argument("--config", default=None, help="全局配置 YAML(pools 代理声明);提供则逐池做连通性探测")
    doctor.add_argument("--probe-timeout", type=float, default=10.0, help="代理探测超时秒数(默认 10)")
    doctor.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")


def _add_secret_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi secret``:钥匙链凭据 set/list/delete。"""
    secret = sub.add_parser(
        "secret",
        help="钥匙链凭据管理:set / list / delete(值永不回显)",
        description="薄包装 myia.secrets;凭据名必须为 myia/<scope>/<name> 规范形式。",
    )
    secret_sub = secret.add_subparsers(dest="secret_command", required=True, title="凭据操作")
    secret_set = secret_sub.add_parser("set", help="写入/覆盖一个凭据(值不落日志)")
    secret_set.add_argument("name", help="规范凭据名 myia/<scope>/<name>")
    secret_set.add_argument(
        "--value",
        default=None,
        help=(
            "凭据值;缺省时非 tty 从 stdin 读取、tty 下安全输入(不回显)。"
            "注意:经命令行参数传值会落入 shell history 与进程列表(ps),"
            "自动化请改用 stdin 管道(如 myia secret set … < value.txt)"
        ),
    )
    secret_set.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出")
    secret_list = secret_sub.add_parser("list", help="列出凭据名(只有名字,没有值)")
    secret_list.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出")
    secret_delete = secret_sub.add_parser("delete", help="删除一个凭据")
    secret_delete.add_argument("name", help="规范凭据名 myia/<scope>/<name>")
    secret_delete.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出")


def _add_plugin_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi plugin``:市场插件装卸 list / install / remove(v0.3)。"""
    plugin = sub.add_parser(
        "plugin",
        help="市场插件装卸:list / install / remove(装卸失败绝不影响核心流水线)",
        description=(
            "插件 = 安装根下的一个目录(目录名 == manifest id,根下 plugin.yaml)。"
            "默认安装根 ~/.myia/plugins(环境变量 MYIA_PLUGIN_DIR 可覆盖)。"
            "装卸 fail-fast(manifest 坏/版本矩阵不兼容/已装未 force 都结构化拒绝,"
            "退出码 1);扫描零异常 —— 任何插件装不上/配置坏只产结构化 findings,"
            "核心流水线照常跑通(铁律)。"
        ),
    )
    plugin_sub = plugin.add_subparsers(dest="plugin_command", required=True, title="插件操作")
    listing = plugin_sub.add_parser(
        "list",
        help="列出已安装插件与结构化 findings(--probe 附带 remote 端点探测)",
    )
    listing.add_argument("--dir", default=str(default_install_root()), help="插件安装根(默认 ~/.myia/plugins)")
    listing.add_argument(
        "--probe",
        action="store_true",
        help="对声明 remote 模式的插件做端点可达性探测(网络 I/O,显式 opt-in;4xx 算可达)",
    )
    listing.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")
    install = plugin_sub.add_parser(
        "install",
        help="安装一个插件目录(fail-fast:manifest 坏/版本不兼容/已装未 force 都拒)",
    )
    install.add_argument("source", help="插件目录或其 plugin.yaml 路径")
    install.add_argument("--dir", default=str(default_install_root()), help="插件安装根(默认 ~/.myia/plugins)")
    install.add_argument(
        "--force",
        action="store_true",
        help="已安装时覆盖;版本矩阵不兼容时强制安装(结果里 compatible_current=false 可见)",
    )
    install.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")
    remove = plugin_sub.add_parser("remove", help="按 id 移除已安装插件")
    remove.add_argument("id", help="要移除的插件 id(manifest 的 id)")
    remove.add_argument("--dir", default=str(default_install_root()), help="插件安装根(默认 ~/.myia/plugins)")
    remove.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")


def _add_channels_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi channels``:通道目录 refresh / list(10-03-messaging-feishu D5)。"""
    channels = sub.add_parser(
        "channels",
        help="通道目录:refresh 刷新平台可达对象 / list 查看(定向推送的寻址地图)",
        description=(
            "目录 = 各平台可达推送对象(群/私聊/话题)的缓存地图,"
            "落在 <数据根>/channel_directory.json(数据根 = --db 父目录)。"
            "refresh 调平台列表 API 发现(飞书 im/v1/chats;单平台失败保留旧桶);"
            "list 纯读不发现。私聊(飞书列表 API 不返回)经"
            " <数据根>/channel_aliases.json 手工登记:{\"feishu\": {\"oc_xxx\": \"别名\"}}。"
        ),
    )
    channels_sub = channels.add_subparsers(dest="channels_command", required=True, title="目录操作")
    refresh = channels_sub.add_parser(
        "refresh",
        help="刷新平台通道目录(缺省全部已注册平台;失败平台保留旧桶并结构化上报)",
    )
    refresh.add_argument(
        "platforms", nargs="*", help="要刷新的平台名(缺省全部已注册,当前:feishu)"
    )
    refresh.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"存储路径(目录 JSON 落数据根 = db 父目录;默认 ./{DEFAULT_DB_PATH})",
    )
    refresh.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")
    listing = channels_sub.add_parser("list", help="列出通道目录(纯读;空目录提示先 refresh)")
    listing.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"存储路径(目录 JSON 落数据根 = db 父目录;默认 ./{DEFAULT_DB_PATH})",
    )
    listing.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")


def _add_feedback_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi feedback``:反馈闭环 list / stats / mark(v0.3,grill Q7)。"""
    feedback = sub.add_parser(
        "feedback",
        help="反馈闭环:list / stats / mark(推送卡片的有价值/没价值回写)",
        description=(
            "mark 手动标记一条反馈入库;list 查看反馈记录;"
            "stats 输出窗口统计(负反馈 Top 类目/词)与当前生效的调参"
            "(mute 词权重/类目降权/评分要点,随调度周期自动应用)。"
        ),
    )
    feedback_sub = feedback.add_subparsers(dest="feedback_command", required=True, title="反馈操作")
    mark = feedback_sub.add_parser(
        "mark",
        help="手动标记:myia feedback mark <条目> <good|bad>(桌面形态第三接收路)",
    )
    mark.add_argument("item", help="条目引用:items.id 或 dedup_key(默认模板下即条目 URL)")
    mark.add_argument("verdict", choices=("good", "bad"), help="判定:good=有价值,bad=没价值")
    mark.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite 存储路径(默认 ./{DEFAULT_DB_PATH})")
    mark.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出")
    listing = feedback_sub.add_parser("list", help="反馈记录(默认最近 50 条,新→旧)")
    listing.add_argument("--verdict", choices=("good", "bad"), default=None, help="按判定过滤")
    listing.add_argument("--channel", default=None, help="按接收渠道过滤(cli/telegram/feishu)")
    listing.add_argument("--limit", type=int, default=50, help="返回条数上限(默认 50)")
    listing.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite 存储路径(默认 ./{DEFAULT_DB_PATH})")
    listing.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出")
    stats = feedback_sub.add_parser(
        "stats", help="窗口统计:好/坏计数、负反馈 Top 类目/词、当前生效调参与历史"
    )
    stats.add_argument(
        "--window-days", type=int, default=DEFAULT_TUNING_WINDOW_DAYS,
        help=f"统计窗口天数(默认 {DEFAULT_TUNING_WINDOW_DAYS},与周期调参一致)",
    )
    stats.add_argument("--top", type=int, default=5, help="Top 类目/词条数(默认 5)")
    stats.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite 存储路径(默认 ./{DEFAULT_DB_PATH})")
    stats.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出")


def _add_skill_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi skill``:Agent Skill 安装通路 install / path(PRD 10-02-v11)。"""
    skill = sub.add_parser(
        "skill",
        help="Agent Skill 安装通路:install / path(把 skill/SKILL.md 装进 agent 的技能目录)",
        description=(
            "打通 Agent Skill 最后一公里:把仓库 skill/SKILL.md 复制(或 --link "
            "符号链接)进 agent 的技能目录。纯文件操作,装卸结果不影响核心流水线;"
            "已存在默认结构化拒绝,--force 才覆盖。"
        ),
    )
    skill_sub = skill.add_subparsers(dest="skill_command", required=True, title="技能操作")
    install = skill_sub.add_parser(
        "install",
        help="安装 SKILL.md 到指定 agent 的技能目录(默认探测,--path 自定义)",
        description=(
            "复制(或 --link 符号链接)SKILL.md 到 <技能根>/myia/SKILL.md。"
            "缺省 --agent 时逐个探测已存在的技能根并全部安装;目标文件已存在时"
            "默认结构化拒绝(退出码 1),--force 才覆盖。"
        ),
    )
    install.add_argument(
        "--agent",
        choices=sorted(SKILL_AGENT_DIRS),
        default=None,
        help=f"目标 agent(缺省探测全部:{', '.join(sorted(SKILL_AGENT_DIRS))})",
    )
    install.add_argument(
        "--path",
        dest="target_dir",
        default=None,
        help="自定义安装目录(直接作为技能目录,优先于 --agent 探测;目录名即技能名)",
    )
    install.add_argument(
        "--link",
        action="store_true",
        help="符号链接代替复制(源仓库更新后装好的技能即跟随更新)",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="目标 SKILL.md 已存在时覆盖(缺省结构化拒绝,退出码 1)",
    )
    install.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")
    path_cmd = skill_sub.add_parser(
        "path",
        help="打印 SKILL.md 源位置与各 agent 的推荐安装路径及安装状态",
        description="信息性命令:完成即退出 0;源缺失也如实落在 found=false,不猜路径。",
    )
    path_cmd.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")


def _add_osint_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi osint``:一次性 OSINT 侦察(v1.1 源码型插件样板 myia-osint)."""
    osint = sub.add_parser(
        "osint",
        help="一次性 OSINT 侦察(myia-osint 插件,子进程调用上游 Photon;失败绝不拦核心)",
        description=(
            "源码型插件样板:定位 <plugins-dir>/myia-osint(适配器 adapter.py + "
            "vendor/Photon submodule),以 uv 临时环境(--no-project --with 按需装依赖,"
            "不进根依赖)隔离子进程运行上游 CLI,读取其 JSON 导出并输出结构化结果。"
            "默认目标 example.com(合法演示域)。vendor 缺失/适配器不可用/目标非法 "
            "→ 结构化错误退 1;采集失败(非零退出/超时/导出坏)退 2;任何失败都"
            "不影响核心品类流水线(铁律)。"
        ),
    )
    osint.add_argument(
        "target",
        nargs="?",
        default=DEFAULT_OSINT_TARGET,
        help=f"侦察目标 http(s) URL(默认 {DEFAULT_OSINT_TARGET},合法演示域)",
    )
    osint.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_OSINT_TIMEOUT_SECONDS,
        help=f"子进程 wall-clock 预算秒数(默认 {DEFAULT_OSINT_TIMEOUT_SECONDS:.0f})",
    )
    osint.add_argument(
        "--plugins-dir",
        default=DEFAULT_PLUGINS_DIR,
        help=f"插件目录(默认 ./{DEFAULT_PLUGINS_DIR},样板位于 myia-osint/ 子目录)",
    )
    osint.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")


def _configure_logging(as_json: bool) -> None:
    """stderr 上的结构化日志;--json 模式收敛到 WARNING,保 stdout 纯净。"""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING if as_json else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _emit_config_error(errors: list[dict[str, Any]], *, source: str | None, as_json: bool) -> None:
    """结构化配置错误:--json 走 stdout,人类可读走 stderr。"""
    if as_json:
        print(json.dumps({"error": "config", "source": source, "errors": errors}, ensure_ascii=False))
        return
    stream = sys.stderr
    head = "品类配置错误"
    if source:
        head += f"({source})"
    print(head, file=stream)
    for index, detail in enumerate(errors, start=1):
        print(f"  {index}. [{detail.get('error_type', 'error')}] {detail.get('path', '$')} — {detail.get('message', '')}", file=stream)


def _emit_generic_error(error: str, message: str, *, as_json: bool, **extra: Any) -> None:
    """非 YAML 类错误(用法/secret/存储)的结构化输出,契约同配置错误。"""
    payload = {"error": error, "message": message, **extra}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"{message}", file=sys.stderr)


def _load_error_details(exc: LoadError) -> list[dict[str, Any]]:
    return exc.to_dict()["errors"]


def _value_error_details(exc: Exception) -> list[dict[str, Any]]:
    return [{"path": "$", "error_type": "config_error", "message": str(exc)}]


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


# ---------------------------------------------------------------------------
# myia run(v0.1 契约,保持不变)
# ---------------------------------------------------------------------------


def _print_human(result: Any) -> None:
    """人类可读摘要(与 --json 同一信息,另一种皮)。"""
    print(f"世事 run:{result.category}({result.category_name}) run_id={result.run_id}" + (" [dry-run]" if result.dry_run else ""))
    for stage in result.stages:
        parts = [f"{stage.name}:{stage.status}"]
        if stage.items_in or stage.items_out:
            parts.append(f"{stage.items_in}→{stage.items_out} 条")
        if stage.skips:
            parts.append("跳过 " + ",".join(f"{k}×{v}" for k, v in sorted(stage.skips.items())))
        if stage.failures:
            parts.append(f"失败 {len(stage.failures)} 条")
        if stage.error:
            parts.append(f"错误 {stage.error}")
        print("  - " + " | ".join(parts))
    for source in result.sources:
        state = "跳过" if source.skipped else ("失败" if source.failed else "成功")
        line = f"  源 {source.name}({state},engine={source.engine},条目 {source.item_count}"
        if source.skip_reason:
            line += f",skip={source.skip_reason}"
        line += ")"
        print(line)
    for push in result.pushes:
        line = f"  推送 {push.channel}:immediate={push.immediate} digest={push.digest} archive={push.archive}"
        line += ",发送成功" if push.ok else ",存在发送失败"
        if push.dry_run:
            line += "(dry-run,未实际发送)"
        print(line)
    print(f"状态:{result.status}")


def _selfcheck_category_plugin(config: CategoryConfig) -> None:
    """品类 ``plugin:`` 节的启动自检:只发 WARNING 日志,任何结果都不改变 run 流程.

    铁律(security-baseline):任何 plugin 装不上/配置坏/remote 不可达,核心
    流水线照常跑通 —— 本函数吞掉一切异常,findings 一律 warning 级落日志
    (--json 模式 stderr 可见,stdout 的单份 JSON 不受影响,退出码不变)。
    自检默认零网络(端点探测是 ``shishi plugin list --probe`` 的显式 opt-in)。
    """
    section = config.plugin
    if section is None:
        return
    try:
        backend = _safe_keychain_backend()
        findings = check_category_plugin(
            section, InstalledPluginStore(default_install_root()), backend=backend
        )
    except Exception as exc:  # noqa: BLE001 - 自检绝不拦核心
        logger.warning("插件自检失败(已降级跳过,核心流水线不受影响): %s", exc)
        return
    for finding in findings:
        _log_plugin_finding(finding, prefix="插件自检")


def _safe_keychain_backend() -> KeychainBackend | None:
    """Acquire the keychain backend for存在性核验;不可用返回 None(核验跳过)."""
    try:
        return get_backend()
    except SecretError as exc:
        logger.debug("钥匙链后端不可用(token 存在性核验跳过): %s", exc)
        return None


def _log_plugin_finding(finding: PluginFinding, *, prefix: str) -> None:
    logger.warning(
        "%s [%s] %s %s: %s", prefix, finding.severity, finding.scope, finding.code, finding.message
    )


def _cmd_run(args: argparse.Namespace) -> int:
    """``shishi run <yaml>``:加载 → 跑一次或常驻;退出码 0/1/2/3。"""
    _configure_logging(as_json=args.as_json)
    try:
        config = load_category_file(args.yaml)
    except LoadError as exc:
        _emit_config_error(_load_error_details(exc), source=exc.source, as_json=args.as_json)
        return EXIT_CONFIG_ERROR
    _selfcheck_category_plugin(config)

    pools, pools_exit = _resolve_pools(getattr(args, "config", None), args.as_json)
    if pools is None and pools_exit != EXIT_OK:
        return pools_exit
    try:
        pipeline = Pipeline(
            config,
            db_path=args.db,
            proxy_pools=pools,
            # --json 契约(SKILL.md §0 / cli docstring):stdout 恰好一份 JSON。
            # stdout 推送通道的卡片行改写 stderr,不能与 run 报告同流。
            stdout_stream=sys.stderr if args.as_json else None,
        )
    except ValueError as exc:  # Route/规则/关键词表配置非法(fail fast)
        _emit_config_error(_value_error_details(exc), source=str(args.yaml), as_json=args.as_json)
        return EXIT_CONFIG_ERROR

    try:
        if args.loop:
            asyncio.run(pipeline.run_forever(dry_run=args.dry_run))
            logger.info("常驻模式已退出")
            return EXIT_OK
        result = asyncio.run(pipeline.run(dry_run=args.dry_run))
    except KeyboardInterrupt:
        logger.info("收到中断信号,已停止")
        return EXIT_OK
    except StoreSchemaError as exc:
        # 惰性开库发生在 run 内(cli.py:386 的 except ValueError 管不到):
        # 库损坏 / schema 版本过新必须结构化上报,不能让裸 traceback 打穿
        # --json 主消费面(agent/CI 拿不到 code/details)。
        _emit_generic_error(
            "store", str(exc), as_json=args.as_json, error_type=exc.code, **exc.details
        )
        return EXIT_CONFIG_ERROR
    finally:
        pipeline.close()

    if args.as_json:
        _print_json(result.to_dict())
    else:
        _print_human(result)
    return _EXIT_BY_STATUS.get(result.status, EXIT_PARTIAL)


# ---------------------------------------------------------------------------
# shishi init:生成品类 YAML 的结构化信息清单(AI 消费,非人机问答)
# ---------------------------------------------------------------------------

_INIT_REQUIRED_INPUTS: list[dict[str, Any]] = [
    {
        "id": "identity",
        "section": "id / name",
        "description": "品类 id(小写字母/数字/连字符/下划线,字母或数字开头,1-64 字符)与中文显示名",
        "example": {"id": "gpu-prices", "name": "显卡价格"},
    },
    {
        "id": "schedule",
        "section": "schedule / timezone",
        "description": "5 段 cron 触发频率;timezone 为 IANA 名称,缺省跟随系统时区",
        "example": {"schedule": "0 9,15 * * 1-5", "timezone": "Asia/Shanghai"},
    },
    {
        "id": "sources",
        "section": "sources[]",
        "description": (
            "每个源:name、url(模板翻页含 {page})、engine(auto=L1 API→L2 静态页→L3 crawl4ai→firecrawl "
            "自动降级)、method+post_body(POST 必带 post_body)、headers(凭据位只许 env:/keychain: 引用)、"
            "extract(type: list/item/json_path + fields,至少要能取到 url)、rate_limit(qps/backoff/"
            "respect_robots)、proxy(direct / pool:<名称>)、retry(0-10);"
            "源级扩展参数(如 symbols: [...])按 URL 占位符 {symbol} 逐值展开"
        ),
        "example": {
            "name": "yahoo-chart",
            "engine": "direct_api",
            "url": "https://query1.example.com/v8/chart/{symbol}",
            "extract": {"type": "json_path", "fields": {"title": "$.meta.longName", "url": "$.meta.symbol"}},
            "symbols": ["NVDA", "AAPL"],
        },
    },
]

_INIT_OPTIONAL_INPUTS: list[dict[str, Any]] = [
    {
        "id": "watchlist",
        "section": "watchlist",
        "default": {"keywords": [], "mute": []},
        "description": "关键词加权与静默词(供 v0.2 LLM 精评做相关性基线与降权)",
    },
    {
        "id": "classify",
        "section": "classify",
        "default": {"builtin": True, "rules": []},
        "description": "七大类内置关键词扫描默认开;自定义规则为 name/when/tag(when 为白名单表达式,永不 eval)",
    },
    {
        "id": "dedup",
        "section": "dedup.key",
        "default": "{url}",
        "description": "去重键模板:只允许 URL 或字段组合键;{title} 永久禁止;保留字段 {date}/{slot} 可用",
    },
    {
        "id": "enrich",
        "section": "enrich",
        "default": {"enabled": False, "model": "glm-4-flash", "scores": ["value", "relevance", "credibility"],
                    "batch": 20, "cache": True, "budget_per_run": 50000},
        "description": "LLM 精评(第二层漏斗):启用时 enrich.base_url / enrich.api_key 是 schema 字段,"
                       "值必须是 env:/keychain: 引用(无内置端点、无默认 key);漏配在启动期报 "
                       "missing_base_url / missing_api_key。构造注入(Pipeline enrich_settings)仅是测试/编程路径",
    },
    {
        "id": "push",
        "section": "push[]",
        "default": [],
        "description": "每通道:channel(feishu_card/telegram/webhook/stdout)、target(env:/keychain: 引用,"
                       "stdout 不需要)、route(when→immediate/digest/archive;空 = 七大类缺省映射)、"
                       "template(Jinja2,语法在加载期校验)",
    },
    {
        "id": "storage",
        "section": "storage",
        "default": {"retention": "90d", "vacuum": "monthly"},
        "description": "保留期 <n>d/<n>w 与 VACUUM 周期(daily/weekly/monthly/never)",
    },
]

_INIT_RULES: list[str] = [
    "凭据禁明文:Cookie/Authorization/Token 等凭据键的值只允许 env:VAR 或 keychain:myia/<scope>/<name> 引用;明文 = 拒载(退出码 1)",
    "永不标题指纹:dedup.key 禁用 {title},只用 URL 或组合键",
    "未知字段 fail-fast:sources[] 开放扩展参数,其余节拼错字段名即拒载(含字段路径)",
    "engine: auto 降级链固定 L1 direct_api→L2 static_html→L3 crawl4ai→firecrawl;选择结果回写 SQLite engine_hints,不改用户 YAML",
    "推送语义分层:route 管「推不推」(score 分级),AM/PM 槽位管「发没发过」(防重发),两层正交",
]

_INIT_NEXT_STEPS: list[str] = [
    "1. 向用户收集 required_inputs(与需要的 optional_inputs)",
    "2. 未收集的可选节按 default 补全,生成 12 节品类 YAML",
    "3. myia test <yaml> --json 逐源试抓,核对提取字段与指纹",
    "4. myia run <yaml> --dry-run --json 全链演练(不推送)",
    "5. myia doctor --json 体检,把 findings 清零后再常驻调度",
]


def _init_payload() -> dict[str, Any]:
    """Assemble the structured wizard document (静态内容,确定性输出)."""
    return {
        "command": "init",
        "purpose": "生成品类 YAML(12 节 schema)所需的信息清单;agent 据此向用户收集信息并生成插件,"
                   "而非人机问答。schema 权威定义:src/myia/schema.py",
        "required_inputs": _INIT_REQUIRED_INPUTS,
        "optional_inputs": _INIT_OPTIONAL_INPUTS,
        "rules": _INIT_RULES,
        "next_steps": _INIT_NEXT_STEPS,
    }


def _cmd_init(args: argparse.Namespace) -> int:
    """``shishi init``:stdout 恒为单份 JSON(两种模式同一产物,人读加皮走 stderr)。"""
    payload = _init_payload()
    _print_json(payload)
    if not args.as_json:
        print(
            "世事 init:以上 JSON 是生成品类 YAML 的信息清单(供 agent 消费);"
            "生成后用 myia test 验证。",
            file=sys.stderr,
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# 源健康度状态机(list / doctor 共用判据,PRD 10-01-v02-cli-full)
# ---------------------------------------------------------------------------


def _collect_run_history(store: SQLiteStore, category: str, *, max_runs: int = HEALTH_HISTORY_RUNS) -> list[Any]:
    """Newest-first run records for one category (latest_run + previous_run 链)."""
    runs: list[Any] = []
    record = store.latest_run(category)
    while record is not None and len(runs) < max_runs:
        runs.append(record)
        if record.id is None:
            break
        record = store.previous_run(category, before_run_id=record.id)
    return runs


def _source_entries(runs: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """Per-source observations newest-first, read from ``runs.stats.sources``.

    判据只看源级条目数与 skip 原因 —— run 级 status 仅随行展示,不得参与
    判定(run success 掩盖单源静默 0 条的事故不得复发)。
    """
    per_source: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        stats = run.stats if isinstance(run.stats, Mapping) else None
        raw_sources = stats.get("sources") if stats is not None else None
        if not isinstance(raw_sources, list):
            continue  # running/中断行没有 stats,或旧版本行:按无观测处理
        for entry in raw_sources:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get("source")
            if not isinstance(name, str) or not name:
                continue
            item_count = entry.get("item_count")
            per_source.setdefault(name, []).append(
                {
                    "run_id": run.id,
                    "run_status": run.status,
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "item_count": item_count if isinstance(item_count, int) and item_count >= 0 else 0,
                    "skip_reason": entry.get("skip_reason") if isinstance(entry.get("skip_reason"), str) else None,
                    "failed": bool(entry.get("failed")),
                    "engine": entry.get("engine") if isinstance(entry.get("engine"), str) else None,
                }
            )
    return per_source


def evaluate_source_health(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """One source's health state from newest-first observations.

    Args:
        entries: per-run observations for ONE source, newest first (见
            :func:`_source_entries` 的条目形状)。

    Returns:
        ``{"state", "reason", "observed", "latest", "baseline"}`` — state ∈
        ok/degraded/dead/unknown;baseline 为被评判轮**之前**近 5 次有产出的
        均值(样本不足 5 次时为 None,基线判据不参与;把本轮计入自身基线
        会把 50% 阈值稀释到 ~44%,边界漏报)。
    """
    base: dict[str, Any] = {"state": SOURCE_HEALTH_UNKNOWN, "reason": "暂无运行记录", "observed": len(entries),
                            "latest": None, "baseline": None}
    if not entries:
        return base
    latest = entries[0]
    base["latest"] = {
        "run_id": latest["run_id"],
        "run_status": latest["run_status"],
        "item_count": latest["item_count"],
        "skip_reason": latest["skip_reason"],
        "failed": latest["failed"],
    }
    last_three = entries[:3]
    if len(last_three) == 3 and all(entry["failed"] for entry in last_three):
        base.update(state=SOURCE_HEALTH_DEAD, reason="连续 3 轮采集失败(引擎链耗尽/超时)")
        return base
    if latest["failed"]:
        base.update(state=SOURCE_HEALTH_DEGRADED, reason="最新一轮采集失败(未满连续 3 次的死线)")
        return base
    if latest["item_count"] == 0:
        if latest["skip_reason"] in FINGERPRINT_UNCHANGED_REASONS:
            base.update(state=SOURCE_HEALTH_OK, reason=f"指纹未变({latest['skip_reason']}),0 条属正常跳过")
            return base
        base.update(state=SOURCE_HEALTH_DEGRADED,
                    reason="指纹未跳过却产出 0 条(页面结构变化/反爬升级疑似,run 级 success 不得掩盖)")
        return base
    samples = [entry["item_count"] for entry in entries[1:] if entry["item_count"] > 0][
        :HEALTH_BASELINE_SAMPLES
    ]
    if len(samples) >= HEALTH_BASELINE_SAMPLES:
        baseline = sum(samples) / len(samples)
        base["baseline"] = round(baseline, 2)
        if latest["item_count"] < HEALTH_BASELINE_DROP_RATIO * baseline:
            base.update(state=SOURCE_HEALTH_DEGRADED,
                        reason=f"本轮 {latest['item_count']} 条低于近 {len(samples)} 次基线 {baseline:.1f} 的 50%")
            return base
    base.update(state=SOURCE_HEALTH_OK, reason=f"本轮产出 {latest['item_count']} 条")
    return base


def _fingerprint_skip_stats(entries: list[dict[str, Any]]) -> dict[str, int]:
    """指纹跳过率:近 5 次观测里 skip 原因=指纹未变的轮数(window 同基线)。"""
    window = entries[:HEALTH_BASELINE_SAMPLES]
    skipped = sum(1 for entry in window if entry["skip_reason"] in FINGERPRINT_UNCHANGED_REASONS)
    return {"observed": len(window), "skipped": skipped}


# ---------------------------------------------------------------------------
# 插件装载与凭据引用检查(doctor)
# ---------------------------------------------------------------------------


def _plugin_files(directory: str | Path) -> list[Path]:
    """Sorted ``*.yaml``/``*.yml`` category files directly inside ``directory``.

    Raises:
        NotADirectoryError: the directory does not exist (usage error → exit 1).
    """
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(f"插件目录不存在: {root}")
    return sorted({*root.glob("*.yaml"), *root.glob("*.yml")})


def _load_plugin(path: str | Path) -> tuple[CategoryConfig | None, list[dict[str, Any]]]:
    """Load one category YAML; load errors come back structured, never raised."""
    try:
        return load_category_file(path), []
    except LoadError as exc:
        return None, _load_error_details(exc)


def _iter_string_leaves(node: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    """Depth-first (path, string-value) leaves of a model_dump tree."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield from _iter_string_leaves(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_string_leaves(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


def _collect_credential_refs(config: CategoryConfig) -> list[dict[str, str]]:
    """env:/keychain: references declared by one loaded plugin (含 Bearer 前缀).

    ``plugin:`` 节是 sidecar(不进 ``model_dump``),其 remote token 引用在此
    合并进体检范围 —— doctor 的凭据存在性检查对它一视同仁。同一引用的多个
    使用位(如 sources 头与 plugin 节)都如实列出,不在这里做首次命中截断
    (跨插件去重由 :func:`_merge_credential_entries` 负责)。
    """
    payload = config.model_dump()
    if config.plugin is not None:
        payload["plugin"] = config.plugin.model_dump()
    refs: list[dict[str, str]] = []
    for path, value in _iter_string_leaves(payload):
        match = _SECRET_REF_RE.match(value.strip())
        if match is None:
            continue
        if match.group("env_var") is not None:
            kind, name = "env", match.group("env_var")
        else:
            kind, name = "keychain", match.group("kc")
        refs.append({"path": path, "kind": kind, "name": name, "ref": value.strip()})
    return refs


def _ref_exists(kind: str, name: str, backend: KeychainBackend | None) -> bool | None:
    """Existence probe for one reference; ``None`` = 无法判定(无钥匙链后端)."""
    if kind == "env":
        return name in os.environ
    if backend is None:
        return None
    return backend.get_password(SECRET_SERVICE, name) is not None


def _merge_credential_entries(
    loaded: Sequence[tuple[str, CategoryConfig]], backend: KeychainBackend | None
) -> list[dict[str, Any]]:
    """Dedup references across plugins and probe each one's existence.

    凭据值永不入结果:条目只含引用名与存在性。keychain 引用名非规范形式
    (``myia/<scope>/<name>``)时附迁移提示(存在性仍按原名探测,兼容旧引用)。

    Args:
        loaded: ``(plugin_id, config)`` pairs for every plugin that loaded.
        backend: probed keychain backend; ``None`` = 不可用(exists 记 None).
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for plugin_id, config in loaded:
        for ref in _collect_credential_refs(config):
            key = (ref["kind"], ref["name"])
            entry = merged.setdefault(
                key,
                {"kind": ref["kind"], "name": ref["name"], "ref": ref["ref"], "paths": [], "plugins": []},
            )
            entry["paths"].append({"plugin": plugin_id, "path": ref["path"]})
            if plugin_id and plugin_id not in entry["plugins"]:
                entry["plugins"].append(plugin_id)
    results: list[dict[str, Any]] = []
    for entry in merged.values():
        exists = _ref_exists(entry["kind"], entry["name"], backend)
        result: dict[str, Any] = {
            "kind": entry["kind"],
            "name": entry["name"],
            "ref": entry["ref"],
            "paths": entry["paths"],
            "plugins": entry["plugins"],
            "exists": exists,
        }
        if entry["kind"] == "keychain":
            try:
                validate_secret_name(entry["name"])
            except SecretError as exc:
                result["note"] = str(exc)
        results.append(result)
    return sorted(results, key=lambda item: (item["kind"], item["name"]))


# ---------------------------------------------------------------------------
# 代理连通性(doctor --config)与 HTTP client 工厂
# ---------------------------------------------------------------------------


def _build_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """HTTP client factory for trial fetches / proxy probes(测试注入点).

    测试 monkeypatch 本函数返回 ``MockTransport`` 客户端,实现零真实网络;
    生产路径即 ``httpx.AsyncClient(**kwargs)``。
    """
    return httpx.AsyncClient(**kwargs)


async def _probe_proxy_pools(
    pools: Any,
    *,
    timeout: float,
    backend: KeychainBackend | None,
) -> list[dict[str, Any]]:
    """Probe every declared pool; credentials are expanded then never shown.

    逐池**并行**探测(asyncio.gather,保序):每池各吃自己的 probe-timeout,
    总耗时≈最慢单池而非逐池累加 —— doctor 必须在桌面 sidecar 的固定壳超时
    (120s)内回话,v1.1 评审实测串行下 13 个不可达池即顶穿(130.6s)。
    单池凭据解析失败不拖垮其余池(部分失败语义不变)。
    """

    async def _probe_one(name: str) -> dict[str, Any]:
        try:
            resolved = pools.resolve(name, backend=backend)
        except Exception as exc:  # noqa: BLE001 - 单池失败不拖垮其余池的诊断(部分失败语义)
            logger.warning("代理池凭据解析失败 pool=%s: %s", name, exc)
            raw = mask_proxy_url(pools.raw_url(name))
            return {
                "pool": name, "ok": False, "message": f"代理池凭据引用无法解析: {exc}",
                "proxy_url": raw, "error_type": classify_exception(exc),
            }
        client = _build_async_client(proxy=resolved, timeout=timeout)
        try:
            check = await check_proxy_connectivity(resolved, client=client, timeout=timeout)
        finally:
            await client.aclose()
        return {"pool": name, **check.to_dict()}

    return list(await asyncio.gather(*(_probe_one(name) for name in pools.names())))


# ---------------------------------------------------------------------------
# myia test:单源试抓(不推送不入库)
# ---------------------------------------------------------------------------


def _preview_value(value: Any) -> Any:
    """Truncate one extracted field for preview(标量直出,其余转字符串截断)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        text = value
    else:
        text = str(value)
    if isinstance(text, str) and len(text) > TEST_PREVIEW_VALUE_CHARS:
        return text[:TEST_PREVIEW_VALUE_CHARS] + "…"
    return text


def _dedup_key_preview(
    raw: Mapping[str, Any], registry: DedupRegistry, template: str
) -> dict[str, Any]:
    """Compute one item's dedup-key preview(组合键/URL 键,即条目指纹)."""
    view = {**registry.key_context(), **dict(raw)}
    try:
        return {"dedup_key": DedupRegistry.make_key(template, view)}
    except ValueError as exc:
        return {"dedup_key": None, "dedup_key_error": str(exc)}


async def _test_one_source(
    source: Any,
    context: FetchContext,
    registry: DedupRegistry,
    config: CategoryConfig,
    timeout: float,
) -> dict[str, Any]:
    """Trial-fetch one source through its real degrade chain (结构化结果)."""
    report: dict[str, Any] = {
        "source": source.name,
        "url": source.url,
        "engine_configured": source.engine,
        "ok": False,
        "item_count": 0,
        "fingerprint": None,
        "items": [],
        "failures": [],
        "error": None,
    }
    try:
        outcome = await asyncio.wait_for(fetch_source(source, context), timeout=timeout)
    except TimeoutError:
        report["error"] = {"error_type": "timeout", "message": f"试抓超时(>{timeout:.0f}s)"}
        return report
    report["engine"] = outcome.engine or source.engine
    report["ok"] = outcome.engine is not None
    report["item_count"] = len(outcome.items)
    report["failures"] = [failure.to_dict() for failure in outcome.failures]
    report["fingerprint"] = _fingerprint_view(outcome)
    for raw in outcome.items[:TEST_PREVIEW_ITEMS]:
        item: dict[str, Any] = {
            "fields": {key: _preview_value(value) for key, value in raw.items()},
            **_dedup_key_preview(raw, registry, config.dedup.key),
        }
        report["items"].append(item)
    if len(outcome.items) > TEST_PREVIEW_ITEMS:
        report["items_truncated"] = True
    return report


def _fingerprint_view(outcome: Any) -> dict[str, Any]:
    """Trial fetch's fingerprint view(变更指纹判定 + 对调度行为的含义)."""
    skip_reason = outcome.skip_reason
    if outcome.engine is None:
        verdict = "unknown"
    elif skip_reason in FINGERPRINT_UNCHANGED_REASONS:
        verdict = "unchanged_skip"
    else:
        verdict = "changed_or_first_fetch"
    return {
        "skip_reason": skip_reason,
        "verdict": verdict,
        "meaning": {
            "unchanged_skip": "变更指纹未变,线上调度会跳过本轮(正常)",
            "changed_or_first_fetch": "内容有变化或首次抓取,线上调度会正常提取",
            "unknown": "引擎链耗尽,未取得指纹判定",
        }[verdict],
    }


def _print_human_test(payload: dict[str, Any]) -> None:
    """人类可读的试抓摘要(与 --json 同一信息)。"""
    print(f"世事 test:{payload['yaml']}(试抓不入库不推送)")
    for source in payload["sources"]:
        state = "成功" if source["ok"] else "失败"
        print(f"  源 {source['source']}({state},engine={source.get('engine')},条目 {source['item_count']})")
        fingerprint = source.get("fingerprint") or {}
        if fingerprint.get("skip_reason"):
            print(f"    指纹:skip={fingerprint['skip_reason']}({fingerprint['meaning']})")
        elif fingerprint:
            print(f"    指纹:{fingerprint['meaning']}")
        for item in source["items"]:
            print(f"    条目 dedup_key={item.get('dedup_key')} fields={json.dumps(item['fields'], ensure_ascii=False)}")
        for failure in source["failures"]:
            print(f"    引擎失败 {failure['engine']} [{failure['error_type']}] {failure['message']}")
        if source.get("error"):
            print(f"    错误 [{source['error']['error_type']}] {source['error']['message']}")
    print(f"状态:{payload['status']}")


def _resolve_pools(config_path: str | None, as_json: bool) -> tuple[Any | None, int]:
    """Load the global pools declaration for ``--config``(test/run/doctor 共用;可选)."""
    if config_path is None:
        return None, EXIT_OK
    try:
        return load_proxy_pools_file(config_path), EXIT_OK
    except LoadError as exc:
        _emit_config_error(_load_error_details(exc), source=str(config_path), as_json=as_json)
        return None, EXIT_CONFIG_ERROR


def _cmd_test(args: argparse.Namespace) -> int:
    """``shishi test <yaml> [--source name]``:试抓 → 字段与指纹;0/1/2/3。"""
    _configure_logging(as_json=args.as_json)
    try:
        config = load_category_file(args.yaml)
    except LoadError as exc:
        _emit_config_error(_load_error_details(exc), source=exc.source, as_json=args.as_json)
        return EXIT_CONFIG_ERROR
    sources = list(config.sources)
    if args.source is not None:
        sources = [source for source in sources if source.name == args.source]
        if not sources:
            known = [source.name for source in config.sources]
            _emit_config_error(
                [{"path": "$.sources", "error_type": "source_not_found",
                  "message": f"源 {args.source!r} 不存在,可用源: {known}"}],
                source=str(args.yaml),
                as_json=args.as_json,
            )
            return EXIT_CONFIG_ERROR
    pools, exit_code = _resolve_pools(args.config, args.as_json)
    if pools is None and exit_code != EXIT_OK:
        return exit_code
    # 试抓零持久化:基线/引擎提示全部写进内存库,命令结束即弃。
    store = SQLiteStore(":memory:")
    registry = DedupRegistry(store, tz=_category_tz(config))
    client = _build_async_client(timeout=args.timeout)
    context = FetchContext(client=client, store=store, timeout=args.timeout, proxy_pools=pools)
    try:
        reports = asyncio.run(_test_sources(sources, context, registry, config, args.timeout))
    finally:
        store.close()

    failed = [report for report in reports if not report["ok"]]
    status = "failed" if len(failed) == len(reports) else ("partial" if failed else "success")
    payload = {
        "command": "test",
        "yaml": str(args.yaml),
        "note": "试抓不入库不推送;dedup_key 为去重键预览",
        "sources": reports,
        "status": status,
    }
    if args.as_json:
        _print_json(payload)
    else:
        _print_human_test(payload)
    if status == "failed":
        return EXIT_FETCH_ALL_FAILED
    return EXIT_PARTIAL if failed else EXIT_OK


async def _test_sources(
    sources: list[Any],
    context: FetchContext,
    registry: DedupRegistry,
    config: CategoryConfig,
    timeout: float,
) -> list[dict[str, Any]]:
    """Run trial fetches sequentially (同插件共享限速与指纹基线内存库)."""
    reports: list[dict[str, Any]] = []
    try:
        for source in sources:
            reports.append(await _test_one_source(source, context, registry, config, timeout))
    finally:
        await context.aclose_pool_clients()
    return reports


# ---------------------------------------------------------------------------
# myia list / myia doctor:插件清单、健康度与结构化诊断
# ---------------------------------------------------------------------------


def _category_tz(config: CategoryConfig) -> ZoneInfo | None:
    """The category timezone as ZoneInfo(None = 系统本地时区,加载期已校验)."""
    if not config.timezone:
        return None
    try:
        return ZoneInfo(config.timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError):  # 防御:加载期已校验
        return None


def _next_fire_at(config: CategoryConfig) -> str | None:
    """Next cron fire time in the category timezone(调度下次触发时间)."""
    try:
        trigger = build_cron_trigger(config.schedule, config.timezone)
        now = datetime.now(_category_tz(config) or ZoneInfo("UTC")).astimezone()
        next_fire = trigger.get_next_fire_time(None, now)
    except (ValueError, ZoneInfoNotFoundError, OSError) as exc:  # 防御:加载期已校验
        logger.warning("下次触发时间计算失败 id=%s: %s", config.id, exc)
        return None
    return next_fire.astimezone().isoformat() if next_fire is not None else None


def _enrich_section(config: CategoryConfig, store: SQLiteStore | None) -> dict[str, Any]:
    """enrich 预算/缓存状态(只展示;端点设置走构造注入,不在 YAML)。"""
    section: dict[str, Any] = {
        "enabled": config.enrich.enabled,
        "model": config.enrich.model,
        "scores": list(config.enrich.scores),
        "batch": config.enrich.batch,
        "cache": config.enrich.cache,
        "budget_per_run": config.enrich.budget_per_run,
    }
    if store is not None:
        section["cache_rows"] = _enrich_cache_rows(store)
    return section


def _enrich_cache_rows(store: SQLiteStore) -> int | None:
    """enrich_cache 行数(默认 SQLite 后端直查;异常按 None 降级,不误报)."""
    conn = getattr(store, "conn", None)
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT COUNT(*) FROM enrich_cache").fetchone()
    except Exception as exc:  # noqa: BLE001 - 诊断展示,失败降级为未知
        logger.debug("enrich_cache 计数失败(按未知处理): %s", exc)
        return None
    return int(row[0]) if row is not None else None


def _source_report(
    config: CategoryConfig | None,
    store: SQLiteStore | None,
    store_error: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Per-source health rows for one plugin (list/doctor 共用;未加载 → 空表).

    库打不开(store_error)时健康度如实标「存储无法打开」,不伪装成
    「暂无运行记录」——把「库坏了」报成「没跑过」属静默掩盖。
    """
    entries_by_source: dict[str, list[dict[str, Any]]] = {}
    if config is None:
        return [], entries_by_source
    if store is not None:
        runs = _collect_run_history(store, config.id)
        entries_by_source = _source_entries(runs)
    store_down_health: dict[str, Any] | None = None
    if store is None and store_error is not None:
        store_down_health = {
            "state": SOURCE_HEALTH_UNKNOWN,
            "reason": f"存储无法打开({store_error.get('error_type', 'store_error')}),健康度未知",
            "observed": 0,
            "latest": None,
            "baseline": None,
        }
    rows: list[dict[str, Any]] = []
    for source in config.sources:
        entries = entries_by_source.get(source.name, [])
        hint = store.get_engine_hint(source.url) if store is not None else None
        rows.append(
            {
                "name": source.name,
                "url": source.url,
                "engine": source.engine,
                "engine_hint": hint,
                "health": store_down_health or evaluate_source_health(entries),
                "fingerprint_skips": _fingerprint_skip_stats(entries),
            }
        )
    return rows, entries_by_source


def _plugin_identity(path: Path, config: CategoryConfig | None, errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Common identity block of a plugin report (list/doctor 共用)."""
    return {
        "file": str(path),
        "id": config.id if config else None,
        "name": config.name if config else None,
        "schedule": config.schedule if config else None,
        "timezone": config.timezone if config else None,
        "push_channels": [push.channel for push in config.push] if config else [],
        "loaded": config is not None,
        "load_errors": errors or None,
    }


def _plugin_report(
    path: Path, store: SQLiteStore | None, store_error: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One plugin's list view: identity + per-source health (errors structured)."""
    config, errors = _load_plugin(path)
    report = _plugin_identity(path, config, errors)
    report["sources"] = _source_report(config, store, store_error)[0] if config is not None else []
    return report


def _print_human_list(payload: dict[str, Any]) -> None:
    """人类可读的插件清单(与 --json 同一信息)。"""
    print(f"世事 list:目录 {payload['plugins_dir']}(db={payload['db']})共 {len(payload['plugins'])} 个插件")
    for plugin in payload["plugins"]:
        if not plugin["loaded"]:
            print(f"  {plugin['file']} — 加载失败")
            for detail in plugin["load_errors"] or []:
                print(f"    [{detail['error_type']}] {detail['path']} — {detail['message']}")
            continue
        print(f"  {plugin['file']} — {plugin['id']}({plugin['name']})schedule={plugin['schedule']}")
        for source in plugin["sources"]:
            health = source["health"]
            skips = source["fingerprint_skips"]
            hint = f",hint={source['engine_hint']}" if source["engine_hint"] else ""
            print(
                f"    源 {source['name']}[{health['state']}] {health['reason']}"
                f"(指纹跳过 {skips['skipped']}/{skips['observed']}{hint})"
            )


def _cmd_list(args: argparse.Namespace) -> int:
    """``shishi list``:插件清单 + 源健康度;信息性命令,完成即 0(目录错=1)。"""
    as_json = args.as_json
    _configure_logging(as_json=as_json)
    try:
        files = _plugin_files(args.plugins_dir)
    except NotADirectoryError as exc:
        _emit_generic_error("plugins_dir", str(exc), as_json=as_json)
        return EXIT_CONFIG_ERROR
    store, store_error = _open_store(args.db, as_json=as_json)
    payload = {
        "command": "list",
        "plugins_dir": str(args.plugins_dir),
        "db": str(args.db),
        "store_error": store_error,
        "plugins": [_plugin_report(path, store, store_error) for path in files],
    }
    if store is not None:
        store.close()
    if as_json:
        _print_json(payload)
    else:
        _print_human_list(payload)
    return EXIT_OK


def _open_store(
    db_path: str, *, as_json: bool
) -> tuple[SQLiteStore | None, dict[str, Any] | None]:
    """Open the default SQLite store for diagnostics; corrupt/newer → (None, structured error).

    doctor/list 是诊断入口:库损坏或版本过新必须结构化可见,而不是让命令
    崩掉。错误**不在这里打印**——--json 契约是 stdout 恰好一份 JSON 文档,
    由调用方把错误并入唯一一份报告(list 顶层 ``store_error`` 字段 /
    doctor ``findings``);人类模式才直接打到 stderr。
    """
    try:
        return SQLiteStore(db_path), None
    except StoreSchemaError as exc:
        detail: dict[str, Any] = {"error_type": exc.code, "message": str(exc), **exc.details}
        if not as_json:
            print(f"存储无法打开 db={db_path}: {exc}", file=sys.stderr)
        return None, detail


def _finding(findings: list[dict[str, Any]], *, severity: str, scope: str, code: str, message: str) -> None:
    """Append one structured finding(0=成功 1=配置 2/3 同 run 语义,doctor 只产 findings)."""
    findings.append({"severity": severity, "scope": scope, "code": code, "message": message})


def _plugin_findings(report: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    """Derive findings from one plugin's load errors + source health."""
    scope = f"plugin:{report['file']}"
    for detail in report["load_errors"] or []:
        _finding(
            findings,
            severity="error",
            scope=scope,
            code=str(detail.get("error_type", "config_error")),
            message=f"{detail.get('path', '$')}: {detail.get('message', '')}",
        )
    for source in report["sources"]:
        health = source["health"]
        source_scope = f"{scope}/source:{source['name']}"
        if health["state"] == SOURCE_HEALTH_DEAD:
            _finding(findings, severity="error", scope=source_scope, code="source_dead", message=health["reason"])
        elif health["state"] == SOURCE_HEALTH_DEGRADED:
            _finding(findings, severity="warning", scope=source_scope, code="source_degraded", message=health["reason"])


def _format_ref_paths(entry: Mapping[str, Any]) -> str:
    """Render a credential entry's usage sites as ``plugin:path``(诊断定位)."""
    return "; ".join(f"{usage['plugin']}:{usage['path']}" for usage in entry["paths"])


def _credential_findings(
    entries: list[dict[str, Any]],
    *,
    backend_error: SecretError | None,
    findings: list[dict[str, Any]],
) -> None:
    """Derive findings from credential-reference probes(引用存在性可检可修)."""
    keychain_refs = [entry for entry in entries if entry["kind"] == "keychain"]
    if backend_error is not None and keychain_refs:
        _finding(
            findings,
            severity="error",
            scope="credentials",
            code=getattr(backend_error, "code", "keychain_backend_unavailable"),
            message=f"{backend_error};以下 keychain 引用无法核验: "
                    f"{[entry['ref'] for entry in keychain_refs]}",
        )
    for entry in entries:
        scope = "credentials"
        if entry["kind"] == "env" and entry["exists"] is False:
            _finding(
                findings,
                severity="warning",
                scope=scope,
                code="env_ref_missing",
                message=f"环境变量 {entry['name']} 未设置(引用于 {_format_ref_paths(entry)})",
            )
        if entry["kind"] == "keychain":
            if entry["exists"] is False:
                _finding(
                    findings,
                    severity="error",
                    scope=scope,
                    code="keychain_ref_missing",
                    message=f"钥匙链中不存在凭据 {entry['name']}(引用于 {_format_ref_paths(entry)});"
                            f"请执行 myia secret set {entry['name']} 写入",
                )
            if entry.get("note"):
                _finding(
                    findings,
                    severity="warning",
                    scope=scope,
                    code="keychain_name_noncanonical",
                    message=entry["note"],
                )


def _proxy_findings(pools_result: list[dict[str, Any]], findings: list[dict[str, Any]]) -> None:
    """Derive findings from proxy probes(代理挂 ≠ 源死,warning 语义)."""
    for entry in pools_result:
        if entry.get("ok"):
            continue
        code = entry.get("error_type") or "proxy_unreachable"
        severity = "error" if code in ("credential_unresolved", "invalid_proxy_url") else "warning"
        _finding(
            findings,
            severity=severity,
            scope=f"proxy:{entry.get('pool')}",
            code=code,
            message=str(entry.get("message", "代理探测失败")),
        )


def _telegram_poll_conflict_findings(
    loaded: Sequence[tuple[str, CategoryConfig]], findings: list[dict[str, Any]]
) -> None:
    """常驻模式 TG getUpdates 同 token 竞争提示(素材 12,warning 语义).

    telegram 通道的 bot token 固定解析自 ``env:TELEGRAM_BOT_TOKEN``(schema
    不设 per-channel token 位),所以只要 ≥2 个已加载品类都配了 telegram 通道,
    它们的常驻进程(``--loop``)就会各起一个 ``getUpdates`` 轮询 → Telegram
    以 409 Conflict 互踢(一个 token 同时只允许一个 long-poll 消费方)。
    单次 run 不轮询、不受影响,故只警示不判错;无法在此判定谁真的在常驻,
    提示由主人按部署形态裁决。
    """
    telegram_plugins = [
        plugin_id
        for plugin_id, config in loaded
        if any(push.channel == "telegram" for push in config.push)
    ]
    if len(telegram_plugins) < 2:
        return
    _finding(
        findings,
        severity="warning",
        scope="feedback",
        code="telegram_token_poll_conflict",
        message=(
            f"{len(telegram_plugins)} 个品类共享同一 bot token(env:TELEGRAM_BOT_TOKEN)"
            f"且都配置了 telegram 通道:{'、'.join(telegram_plugins)};每个常驻进程"
            "(myia run --loop)都会轮询 getUpdates,同 token 多轮询方会被 Telegram "
            "以 409 Conflict 互踢。只保留一个常驻品类配置 telegram 通道(其余品类"
            "改用其他推送渠道),或仅对其中一个品类使用 --loop。"
        ),
    )


def _print_human_doctor(payload: dict[str, Any]) -> None:
    """人类可读的诊断报告(与 --json 同一信息)。"""
    print(f"世事 doctor(db={payload['db']})healthy={'是' if payload['healthy'] else '否'}")
    for plugin in payload["plugins"]:
        if not plugin["loaded"]:
            print(f"  插件 {plugin['file']} — 加载失败")
            continue
        next_fire = plugin.get("next_fire_at") or "未知"
        print(f"  插件 {plugin['id']}({plugin['name']})下次触发 {next_fire}")
        for source in plugin["sources"]:
            health = source["health"]
            hint = f",hint={source['engine_hint']}" if source["engine_hint"] else ""
            print(f"    源 {source['name']}[{health['state']}] {health['reason']}{hint}")
        enrich = plugin.get("enrich") or {}
        if enrich.get("enabled"):
            print(f"    enrich model={enrich['model']} budget/run={enrich['budget_per_run']} "
                  f"cache={enrich.get('cache_rows')} 行")
    credentials = payload["credentials"]
    for entry in credentials["entries"]:
        state = {True: "存在", False: "不存在", None: "无法核验"}[entry["exists"]]
        print(f"  凭据 {entry['ref']} — {state}")
    proxy = payload["proxy"]
    for entry in proxy.get("pools", []):
        state = "连通" if entry.get("ok") else "失败"
        latency = f" {entry['latency_seconds']}s" if entry.get("latency_seconds") is not None else ""
        print(f"  代理池 {entry.get('pool')} — {state}{latency} {entry.get('message', '')}")
    for item in payload["findings"]:
        print(f"  [{item['severity']}] {item['scope']} {item['code']}: {item['message']}")
    print(f"findings: {payload['summary']['errors']} 错误 / {payload['summary']['warnings']} 警告")


def _cmd_doctor(args: argparse.Namespace) -> int:
    """``shishi doctor``:结构化诊断;诊断完成即 0,问题全在 findings。"""
    as_json = args.as_json
    _configure_logging(as_json=as_json)
    try:
        files = _doctor_targets(args, as_json=as_json)
    except NotADirectoryError as exc:
        _emit_generic_error("plugins_dir", str(exc), as_json=as_json)
        return EXIT_CONFIG_ERROR
    if files is None:
        return EXIT_CONFIG_ERROR

    store, store_error = _open_store(args.db, as_json=as_json)
    findings: list[dict[str, Any]] = []
    if store_error is not None:
        _finding(findings, severity="error", scope="store", code="store_error",
                 message=f"存储无法打开 db={args.db}:{store_error['message']}")

    plugins, loaded = _doctor_plugins(files, store, findings, store_error)
    backend, backend_error = _acquire_keychain_backend()
    credential_entries = _merge_credential_entries(loaded, backend)
    _credential_findings(credential_entries, backend_error=backend_error, findings=findings)
    _telegram_poll_conflict_findings(loaded, findings)
    proxy_section = _doctor_proxy(args, backend, findings)
    payload = _doctor_payload(args, plugins=plugins, backend=backend, backend_error=backend_error,
                              credential_entries=credential_entries, proxy_section=proxy_section,
                              findings=findings)
    if as_json:
        _print_json(payload)
    else:
        _print_human_doctor(payload)
    if store is not None:
        store.close()
    return EXIT_OK


def _doctor_payload(
    args: argparse.Namespace,
    *,
    plugins: list[dict[str, Any]],
    backend: KeychainBackend | None,
    backend_error: SecretError | None,
    credential_entries: list[dict[str, Any]],
    proxy_section: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the doctor report (healthy = 无 error 级 finding)."""
    errors = sum(1 for item in findings if item["severity"] == "error")
    warnings = sum(1 for item in findings if item["severity"] == "warning")
    return {
        "command": "doctor",
        "generated_at": datetime.now().astimezone().isoformat(),
        "db": str(args.db),
        "healthy": errors == 0,
        "plugins": plugins,
        "credentials": {
            "backend_available": backend is not None,
            "backend_error": str(backend_error) if backend_error else None,
            "entries": credential_entries,
        },
        "proxy": proxy_section,
        "findings": findings,
        "summary": {
            "plugins": len(plugins),
            "sources": sum(len(report["sources"]) for report in plugins),
            "errors": errors,
            "warnings": warnings,
        },
    }


def _doctor_targets(args: argparse.Namespace, *, as_json: bool) -> list[Path] | None:
    """Resolve diagnosis targets: explicit YAMLs or the plugins-dir scan.

    Returns:
        The file list; ``None`` when an explicitly named YAML is missing
        (already reported as a structured usage error, exit code 1).
    """
    if not args.yaml:
        return _plugin_files(args.plugins_dir)
    files = [Path(path) for path in args.yaml]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        _emit_generic_error("file_not_found", f"品类 YAML 不存在: {missing}", as_json=as_json)
        return None
    return files


def _doctor_plugins(
    files: list[Path],
    store: SQLiteStore | None,
    findings: list[dict[str, Any]],
    store_error: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[str, CategoryConfig]]]:
    """Plugin section: identity + health + schedule + enrich(加载失败即 finding)."""
    plugins: list[dict[str, Any]] = []
    loaded: list[tuple[str, CategoryConfig]] = []
    for path in files:
        config, errors = _load_plugin(path)
        report = _plugin_identity(path, config, errors)
        if config is not None:
            loaded.append((config.id, config))
            report["sources"] = _source_report(config, store, store_error)[0]
            report["next_fire_at"] = _next_fire_at(config)
            report["enrich"] = _enrich_section(config, store)
        else:
            report["sources"] = []
            report["next_fire_at"] = None
            report["enrich"] = None
        _plugin_findings(report, findings)
        plugins.append(report)
    return plugins, loaded


def _acquire_keychain_backend() -> tuple[KeychainBackend | None, SecretError | None]:
    """Acquire the keychain backend for existence probes(不可用不炸诊断)."""
    try:
        return get_backend(), None
    except SecretError as exc:
        logger.warning("钥匙链后端不可用(凭据存在性核验降级): %s", exc)
        return None, exc


def _doctor_proxy(
    args: argparse.Namespace, backend: KeychainBackend | None, findings: list[dict[str, Any]]
) -> dict[str, Any]:
    """Proxy connectivity section: probe every declared pool when --config given."""
    section: dict[str, Any] = {"config": args.config, "pools": []}
    if not args.config:
        return section
    try:
        pools = load_proxy_pools_file(args.config)
    except LoadError as exc:
        section["error"] = _load_error_details(exc)
        for detail in _load_error_details(exc):
            _finding(findings, severity="error", scope=f"config:{args.config}",
                     code=str(detail.get("error_type", "config_error")),
                     message=f"{detail.get('path', '$')}: {detail.get('message', '')}")
        return section
    section["pools"] = asyncio.run(_probe_proxy_pools(pools, timeout=args.probe_timeout, backend=backend))
    _proxy_findings(section["pools"], findings)
    return section


# ---------------------------------------------------------------------------
# myia secret:钥匙链凭据管理(薄包装 myia.secrets)
# ---------------------------------------------------------------------------


def _read_secret_value(args: argparse.Namespace) -> str:
    """Resolve the secret value: --value > stdin(非 tty)> 安全输入(tty)."""
    if args.value is not None:
        # 安全基线(--value 暴露面):argv 值必然落入 shell history 与
        # 进程列表(ps)。stderr 警告不污染 --json 的 stdout 单文档契约。
        print(
            "警告:凭据值经 --value 命令行参数传入,会落入 shell history 与进程列表(ps);"
            "自动化场景建议改用 stdin 管道:myia secret set <name> < value.txt",
            file=sys.stderr,
        )
        return args.value
    if not sys.stdin.isatty():
        return sys.stdin.read().rstrip("\r\n")
    return getpass.getpass(f"请输入 {args.name} 的值(输入不回显):")


def _emit_secret_error(exc: SecretError, *, as_json: bool) -> None:
    """结构化 secret 错误(code + 中文原因;值永不入消息)。"""
    _emit_generic_error("secret", str(exc), as_json=as_json, code=exc.code)


def _cmd_secret(args: argparse.Namespace) -> int:
    """``shishi secret set|list|delete``:0 成功 / 1 结构化失败。"""
    as_json = getattr(args, "as_json", False)
    try:
        if args.secret_command == "set":
            value = _read_secret_value(args)
            if not value:
                _emit_generic_error("secret", "凭据值为空(--value、stdin 或安全输入均未取得内容)",
                                    as_json=as_json)
                return EXIT_CONFIG_ERROR
            set_secret(args.name, value)
            payload = {"command": "secret", "action": "set", "name": args.name, "stored": True}
        elif args.secret_command == "list":
            names = list_secrets()
            payload = {"command": "secret", "action": "list", "names": names}
        else:
            delete_secret(args.name)
            payload = {"command": "secret", "action": "delete", "name": args.name, "deleted": True}
    except SecretError as exc:
        _emit_secret_error(exc, as_json=as_json)
        return EXIT_CONFIG_ERROR
    if as_json:
        _print_json(payload)
        return EXIT_OK
    if args.secret_command == "list":
        print(f"钥匙链凭据 {len(payload['names'])} 个(只有名字,值不可读出):")
        for name in payload["names"]:
            print(f"  {name}")
    elif args.secret_command == "set":
        print(f"已写入系统钥匙链 name={payload['name']}(值不落日志)")
    else:
        print(f"已从系统钥匙链删除 name={payload['name']}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# myia channels:通道目录 refresh / list(v1.2,10-03-messaging-feishu D5)
# ---------------------------------------------------------------------------


class _PrecapturedAdapter:
    """CLI refresh 的合并壳:把已发现条目原样喂给 :meth:`ChannelDirectory.refresh`。

    CLI 需要逐平台把发现错误(如凭据缺失)结构化上报给调用方,而 refresh
    内部只记日志;所以 CLI 先自己调 ``discover_directory`` 捕获错误,成功
    的结果经本壳走 core 的 canonical 合并路径(桶替换 + updated_at 戳 +
    原子持久化),不二次发起网络请求。
    """

    def __init__(self, entries: Sequence[Any]) -> None:
        self._entries = entries

    async def discover_directory(self) -> Sequence[Any]:
        return self._entries


def _channels_data_root(args: argparse.Namespace) -> Path:
    """目录 JSON 数据根:``--db`` 父目录(与 pipeline 目录/死信账本同一收口)。"""
    return Path(args.db).expanduser().resolve().parent


def _print_channels_table(directory: ChannelDirectory) -> None:
    """目录表(人类可读;对 AI 用 --json)。"""
    platforms = directory.platforms()
    if not platforms:
        print("通道目录为空:先 myia channels refresh <platform> 发现可达对象")
        return
    print(f"通道目录(最近刷新 {directory.updated_at or '未知'}):")
    for platform in platforms:
        entries = directory.entries(platform)
        print(f"  [{platform}] {len(entries)} 个可达对象:")
        for entry in entries:
            thread = f" thread={entry.thread_id}" if entry.thread_id else ""
            print(f"    {entry.name} ({entry.chat_id}) type={entry.type}{thread}")


def _channels_refresh(args: argparse.Namespace) -> int:
    """``shishi channels refresh [platforms...]``:发现 → 合并 → 打印目录。

    退出码:全部成功 0;未知平台/凭据缺失等任一结构化失败 1(fail-fast
    家族语义;失败平台保留旧目录桶,已成功平台照常合并)。
    """
    as_json = args.as_json
    requested = [str(name) for name in args.platforms] or sorted(PLATFORMS)
    unknown = [name for name in requested if name not in PLATFORMS]
    if unknown:
        _emit_generic_error(
            "channels",
            f"未知平台: {unknown}(已注册: {sorted(PLATFORMS)})",
            as_json=as_json,
        )
        return EXIT_CONFIG_ERROR
    if not requested:
        _emit_generic_error(
            "channels",
            "尚无已注册平台(等平台适配子任务接入 PLATFORMS)",
            as_json=as_json,
        )
        return EXIT_CONFIG_ERROR
    data_root = _channels_data_root(args)
    directory = ChannelDirectory(data_root)
    refreshed: dict[str, int] = {}
    failures: list[dict[str, str]] = []
    passive: list[str] = []
    shims: dict[str, _PrecapturedAdapter] = {}
    for platform in requested:
        adapter = PLATFORMS[platform]()
        discover = getattr(adapter, "discover_directory", None)
        if not callable(discover):
            # 被动目录平台(10-03-messaging-telegram:Bot API 无「列出会话」
            # 能力):非失败——条目由 feedback 轮询入站观测积累 + 别名手工补录。
            passive.append(platform)
            continue
        try:
            entries = asyncio.run(discover())
        except Exception as exc:  # noqa: BLE001 - 单平台失败隔离:告警 + 保留旧桶
            failures.append({"platform": platform, "error": f"{type(exc).__name__}: {exc}"})
            continue
        shims[platform] = _PrecapturedAdapter(entries)
        refreshed[platform] = len(entries)
    if shims:
        asyncio.run(directory.refresh(shims))
    payload = {
        "command": "channels",
        "action": "refresh",
        "data_root": str(data_root),
        "refreshed": refreshed,
        "passive": passive,
        "failed": failures,
        "updated_at": directory.updated_at,
        "platforms": {
            platform: [entry.to_dict() for entry in directory.entries(platform)]
            for platform in directory.platforms()
        },
    }
    if failures:
        # 失败也是单份文档的一部分(顶层 error/message 供 jq 快查,failed 留明细)
        payload["error"] = "channels_refresh_failed"
        payload["message"] = "; ".join(
            f"{failure['platform']}: {failure['error']}" for failure in failures
        )
    if as_json:
        _print_json(payload)
    else:
        for platform, count in sorted(refreshed.items()):
            print(f"已刷新 {platform} 目录:发现 {count} 个可达对象(数据根 {data_root})")
        for platform in passive:
            print(
                f"{platform} 为被动目录平台(无主动发现 API):目录由入站消息自动积累,"
                "别名文件 channel_aliases.json 可手工补录"
            )
        for failure in failures:
            print(
                f"刷新失败(保留旧目录): {failure['platform']}: {failure['error']}",
                file=sys.stderr,
            )
        _print_channels_table(directory)
    return EXIT_OK if not failures else EXIT_CONFIG_ERROR


def _channels_list(args: argparse.Namespace) -> int:
    """``shishi channels list``:纯读目录(空目录是合法态,提示先 refresh)。"""
    as_json = args.as_json
    data_root = _channels_data_root(args)
    directory = ChannelDirectory(data_root)
    payload = {
        "command": "channels",
        "action": "list",
        "data_root": str(data_root),
        "updated_at": directory.updated_at,
        "platforms": {
            platform: [entry.to_dict() for entry in directory.entries(platform)]
            for platform in directory.platforms()
        },
    }
    if as_json:
        _print_json(payload)
        return EXIT_OK
    _print_channels_table(directory)
    return EXIT_OK


def _cmd_channels(args: argparse.Namespace) -> int:
    """``shishi channels refresh|list`` 的分发入口(退出码 0/1)。"""
    _configure_logging(as_json=args.as_json)
    if args.channels_command == "refresh":
        return _channels_refresh(args)
    return _channels_list(args)


# ---------------------------------------------------------------------------
# myia plugin:市场插件装卸(v0.3;薄包装 myia.plugins,装卸 fail-fast,
# 扫描零异常 —— 插件坏绝不拦核心,铁律)
# ---------------------------------------------------------------------------


def _plugin_list(args: argparse.Namespace, store: InstalledPluginStore, *, as_json: bool) -> int:
    """``shishi plugin list``:已装清单 + findings;信息性命令,完成即 0。

    缺失安装根 = 空清单(未装插件是正常态,不是错误);--probe 显式 opt-in
    remote 端点探测(网络 I/O),探测失败只产 findings,不改退出码。
    """
    entries = store.entries()
    if getattr(args, "probe", False):
        backend = _safe_keychain_backend()
        for entry in entries:
            if entry.manifest is None:
                continue
            entry.findings.extend(
                check_remote_modes(
                    entry.manifest,
                    backend=backend,
                    probe_remote=True,
                    client_factory=_build_async_client,
                )
            )
    tiers: dict[str, int] = {}
    for entry in entries:
        if entry.manifest is not None:
            tiers[entry.manifest.tier] = tiers.get(entry.manifest.tier, 0) + 1
    payload = {
        "command": "plugin",
        "action": "list",
        "dir": str(store.root),
        "myia_version": myia.__version__,
        "plugins": [entry.to_dict() for entry in entries],
        "summary": {
            "installed": sum(1 for entry in entries if entry.manifest is not None),
            "usable": sum(
                1
                for entry in entries
                if entry.manifest is not None
                and entry.compatible_current
                and not any(finding.severity == "error" for finding in entry.findings)
            ),
            # v1.1 分级计数(desktop/remote/server-only,见 plugins.manifest)。
            "tiers": dict(sorted(tiers.items())),
            "errors": sum(
                1 for entry in entries for finding in entry.findings if finding.severity == "error"
            ),
            "warnings": sum(
                1 for entry in entries for finding in entry.findings if finding.severity == "warning"
            ),
        },
    }
    if as_json:
        _print_json(payload)
    else:
        _print_human_plugin_list(payload)
    return EXIT_OK


def _print_human_plugin_list(payload: dict[str, Any]) -> None:
    """人类可读的已装插件清单(与 --json 同一信息)。"""
    summary = payload["summary"]
    print(
        f"世事 plugin list:{payload['dir']}(shishi {payload['myia_version']})"
        f"共 {len(payload['plugins'])} 个,可用 {summary['usable']} 个"
    )
    tiers = summary.get("tiers") or {}
    if tiers:
        tier_text = ", ".join(f"{tier}={count}" for tier, count in tiers.items())
        print(f"  分级:{tier_text}")
    for plugin in payload["plugins"]:
        if not plugin["loaded"]:
            print(f"  {plugin['path']} — manifest 缺失或损坏")
        else:
            compatibility = "兼容" if plugin["compatible_current"] else f"不兼容(要求 myia {plugin['compatible']})"
            print(
                f"  {plugin['id']}@{plugin['version']}[{plugin['tier']}]({plugin['name']}){compatibility}"
                f" requires={plugin['requires']} provides={plugin['provides']}"
            )
        for finding in plugin["findings"]:
            print(f"    [{finding['severity']}] {finding['code']}: {finding['message']}")
    print(f"findings: {summary['errors']} 错误 / {summary['warnings']} 警告")


def _plugin_install(args: argparse.Namespace, store: InstalledPluginStore, *, as_json: bool) -> int:
    """``shishi plugin install <source>``:fail-fast 校验后整目录拷贝;0/1。"""
    try:
        result = store.install(Path(args.source), force=args.force)
    except PluginStoreError as exc:
        extra: dict[str, Any] = {"code": exc.code}
        if exc.errors:
            extra["errors"] = exc.errors
        _emit_generic_error("plugin", str(exc), as_json=as_json, **extra)
        return EXIT_CONFIG_ERROR
    if as_json:
        _print_json({"command": "plugin", "action": "install", **result})
        return EXIT_OK
    notes = []
    if result["forced"]:
        notes.append("--force 覆盖安装")
    if not result["compatible_current"]:
        notes.append(f"警告:版本不兼容(要求 myia {result['compatible']})")
    suffix = f"({';'.join(notes)})" if notes else ""
    print(f"已安装 {result['id']}@{result['version']} → {result['path']}{suffix}")
    return EXIT_OK


def _plugin_remove(args: argparse.Namespace, store: InstalledPluginStore, *, as_json: bool) -> int:
    """``shishi plugin remove <id>``:按 id 移除;0 / 1(未装/id 非法)。"""
    try:
        result = store.remove(args.id)
    except PluginStoreError as exc:
        _emit_generic_error("plugin", str(exc), as_json=as_json, code=exc.code)
        return EXIT_CONFIG_ERROR
    if as_json:
        _print_json({"command": "plugin", "action": "remove", **result})
    else:
        print(f"已移除 {result['id']}({result['path']})")
    return EXIT_OK


def _cmd_plugin(args: argparse.Namespace) -> int:
    """``shishi plugin list|install|remove`` 的分发入口(退出码 0/1)。"""
    as_json = args.as_json
    _configure_logging(as_json=as_json)
    store = InstalledPluginStore(args.dir)
    if args.plugin_command == "list":
        return _plugin_list(args, store, as_json=as_json)
    if args.plugin_command == "install":
        return _plugin_install(args, store, as_json=as_json)
    return _plugin_remove(args, store, as_json=as_json)


# ---------------------------------------------------------------------------
# myia feedback:反馈闭环(v0.3;CLI 是桌面形态的手动标记接收路)
# ---------------------------------------------------------------------------


def _feedback_row_dict(record: FeedbackRecord) -> dict[str, Any]:
    """Machine-readable feedback row (--json / AI 消费形态)."""
    return {
        "id": record.id,
        "item_id": record.item_id,
        "dedup_key": record.dedup_key,
        "verdict": record.verdict,
        "channel": record.channel,
        "title": record.title,
        "category": record.category,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def _feedback_mark(args: argparse.Namespace, *, as_json: bool) -> int:
    """``shishi feedback mark <条目> <good|bad>``:入库并回执;0/1。"""
    try:
        store = SQLiteStore(args.db)
    except StoreSchemaError as exc:
        _emit_generic_error("store", str(exc), as_json=as_json, error_type=exc.code, **exc.details)
        return EXIT_CONFIG_ERROR
    try:
        item = resolve_item_ref(store, args.item)
        if item is None:
            _emit_generic_error(
                "item_not_found",
                f"条目不存在: {args.item!r}(可传 items.id 或 dedup_key/URL);"
                "先用 myia list 或查询 items 表确认",
                as_json=as_json,
            )
            return EXIT_CONFIG_ERROR
        try:
            record = record_feedback(store, verdict=args.verdict, channel=FEEDBACK_CHANNEL_CLI, item=item)
        except ValueError as exc:
            _emit_generic_error("feedback", str(exc), as_json=as_json)
            return EXIT_CONFIG_ERROR
    finally:
        store.close()
    payload = {
        "command": "feedback",
        "action": "mark",
        "feedback_id": record.id,
        "item_id": record.item_id,
        "dedup_key": record.dedup_key,
        "verdict": record.verdict,
        "channel": record.channel,
    }
    if as_json:
        _print_json(payload)
    else:
        print(
            f"已记录反馈 verdict={record.verdict} item_id={record.item_id} "
            f"dedup_key={record.dedup_key}(feedback_id={record.id});"
            "负反馈将在下轮维护阶段参与调参(负反馈 Top 类目/词降权)"
        )
    return EXIT_OK


def _feedback_list(args: argparse.Namespace, *, as_json: bool) -> int:
    """``shishi feedback list``:反馈记录(新→旧);0/1。"""
    try:
        store = SQLiteStore(args.db)
    except StoreSchemaError as exc:
        _emit_generic_error("store", str(exc), as_json=as_json, error_type=exc.code, **exc.details)
        return EXIT_CONFIG_ERROR
    try:
        rows = store.list_feedback(
            verdict=args.verdict, channel=args.channel, limit=args.limit
        )
    except ValueError as exc:  # --limit 负数等参数校验失败
        _emit_generic_error("feedback", str(exc), as_json=as_json)
        return EXIT_CONFIG_ERROR
    finally:
        store.close()
    payload = {
        "command": "feedback",
        "action": "list",
        "count": len(rows),
        "items": [_feedback_row_dict(row) for row in rows],
    }
    if as_json:
        _print_json(payload)
        return EXIT_OK
    print(f"世事 feedback list:共 {len(rows)} 条(新→旧)")
    for row in rows:
        title = f" {row.title}" if row.title else ""
        print(
            f"  #{row.id} [{row.verdict}] {row.channel} item={row.item_id or '-'} "
            f"dedup_key={row.dedup_key}{title}"
        )
    return EXIT_OK


def _feedback_stats(args: argparse.Namespace, *, as_json: bool) -> int:
    """``shishi feedback stats``:窗口统计 + 生效调参 + 可追溯历史;0/1。"""
    try:
        policy = TuningPolicy(window_days=args.window_days, top_n=args.top)
    except ValueError as exc:
        _emit_generic_error("feedback", str(exc), as_json=as_json)
        return EXIT_CONFIG_ERROR
    try:
        store = SQLiteStore(args.db)
    except StoreSchemaError as exc:
        _emit_generic_error("store", str(exc), as_json=as_json, error_type=exc.code, **exc.details)
        return EXIT_CONFIG_ERROR
    try:
        tuner = FeedbackTuner(policy)
        stats = tuner.stats(store)
        active = load_active_tuning(store)
        history = [
            {
                "id": row.id,
                "kind": row.kind,
                "payload": dict(row.payload),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in store.list_tuning(limit=10)
        ]
    except ValueError as exc:
        _emit_generic_error("feedback", str(exc), as_json=as_json)
        return EXIT_CONFIG_ERROR
    finally:
        store.close()
    payload = {
        "command": "feedback",
        "action": "stats",
        "window_days": policy.window_days,
        "stats": stats.to_dict(),
        "active_tuning": active.to_dict(),
        "tuning_history": history,
    }
    if as_json:
        _print_json(payload)
        return EXIT_OK
    print(
        f"世事 feedback stats(窗口 {policy.window_days} 天):"
        f"总 {stats.total} / 好 {stats.good} / 坏 {stats.bad}"
    )
    print(
        "  渠道分布: "
        + (", ".join(f"{name}={count}" for name, count in sorted(stats.by_channel.items())) or "无")
    )
    print(
        "  负反馈 Top 类目: "
        + (", ".join(f"{key}×{count}" for key, count in stats.top_bad_categories) or "无")
    )
    print(
        "  负反馈 Top 词条: "
        + (", ".join(f"{key}×{count}" for key, count in stats.top_bad_words) or "无")
    )
    mutes = ", ".join(f"{word}={weight}" for word, weight in sorted(active.mute_weights.items()))
    penalties = ", ".join(
        f"{cat}={weight}" for cat, weight in sorted(active.category_penalties.items())
    )
    print(f"  活跃词降权: {mutes or '无'}")
    print(f"  活跃类目降权: {penalties or '无'}")
    print("  (enrich 启用时应用于评分与路由;enrich 关闭时仅入库、不生效)")
    for note in active.prompt_notes[:1]:
        print(f"  评分要点: {note}")
    print(f"  调参历史: 最近 {len(history)} 条(全量在 feedback_tuning 表,可追溯)")
    return EXIT_OK


def _cmd_feedback(args: argparse.Namespace) -> int:
    """``shishi feedback list|stats|mark`` 的分发入口(退出码 0/1)。"""
    as_json = args.as_json
    _configure_logging(as_json=as_json)
    if args.feedback_command == "mark":
        return _feedback_mark(args, as_json=as_json)
    if args.feedback_command == "list":
        return _feedback_list(args, as_json=as_json)
    return _feedback_stats(args, as_json=as_json)


# ---------------------------------------------------------------------------
# myia skill:Agent Skill 安装通路(PRD 10-02-v11;纯文件操作,退出码 0/1)
# ---------------------------------------------------------------------------


def skill_source() -> Path | None:
    """Locate the canonical ``skill/SKILL.md``(装进 agent 目录的就是这份).

    探测顺序:环境变量 ``MYIA_SKILL_SOURCE``(显式逃生口——设了但文件不存在
    直接返回 None,绝不静默回退装上另一份文件)→ 仓库源码布局(src/myia/
    cli.py 上三级 = 仓库根)→ 当前工作目录下的 skill/。找不到返回 None
    (调用方结构化上报,绝不猜路径装错文件)。
    """
    env_source = os.environ.get(SKILL_SOURCE_ENV)
    if env_source:  # 显式指定只认它自己:存在即用,不存在即失败
        candidate = Path(env_source)
        return candidate if candidate.is_file() else None
    # src/myia/cli.py → parents: [0]=src/myia [1]=src [2]=仓库根
    for candidate in (
        Path(__file__).resolve().parents[2] / "skill" / "SKILL.md",
        Path.cwd() / "skill" / "SKILL.md",
    ):
        if candidate.is_file():
            return candidate
    return None


def _skill_target_dir(agent: str) -> Path:
    """One agent's install directory(技能根展开 home 后拼技能目录名)."""
    return Path(SKILL_AGENT_DIRS[agent]).expanduser() / SKILL_DIR_NAME


def _skill_install_target(args: argparse.Namespace) -> tuple[Path, str | None]:
    """Resolve the install directory:--path 显式优先,否则 --agent(或探测)."""
    if args.target_dir:
        return Path(args.target_dir).expanduser(), args.agent
    agent = args.agent
    if agent is None:  # 缺省:挑第一个「技能根已存在」的 agent(本机在用的)
        agent = next(
            (name for name in sorted(SKILL_AGENT_DIRS)
             if Path(SKILL_AGENT_DIRS[name]).expanduser().is_dir()),
            "claude",
        )
    return _skill_target_dir(agent), agent


def _skill_agent_entries() -> list[dict[str, Any]]:
    """Per-agent path report rows(path/install 共用;只读不写)."""
    source = skill_source()
    source_bytes = source.read_bytes() if source is not None else None
    rows: list[dict[str, Any]] = []
    for agent in sorted(SKILL_AGENT_DIRS):
        target_dir = _skill_target_dir(agent)
        skill_file = target_dir / "SKILL.md"
        installed = skill_file.is_file()
        if not installed:
            matches = False
        elif skill_file.is_symlink():
            matches = skill_file.resolve() == (source.resolve() if source else None)
        else:
            # 副本模式:比对内容,源更新后旧副本如实报不一致(agent 据此 --force 重装)
            try:
                matches = skill_file.read_bytes() == source_bytes
            except OSError:
                matches = False
        rows.append(
            {
                "agent": agent,
                "install_dir": str(target_dir),
                "skill_file": str(skill_file),
                "installed": installed,
                "is_link": installed and skill_file.is_symlink(),
                "matches_source": matches,
            }
        )
    return rows


def _skill_path_payload() -> dict[str, Any]:
    """``shishi skill path`` report(信息性:完成即 0,源缺失如实 found=false)."""
    source = skill_source()
    return {
        "command": "skill",
        "action": "path",
        "source": {"path": str(source) if source else None, "found": source is not None},
        "agents": _skill_agent_entries(),
        "hint": (
            "shishi skill install [--agent claude|cursor|zcode|agents] [--path DIR] [--link] [--force]"
        ),
    }


class SkillInstallError(Exception):
    """skill 安装的结构化失败(code + 中文消息,退出码 1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _skill_install_one(source: Path, target_dir: Path, *, link: bool, force: bool) -> dict[str, Any]:
    """Install SKILL.md into one directory(copy or symlink);refuse unless --force.

    拒绝判据是**目标 SKILL.md 已存在**(与 path 报告的 installed 同一判据);
    目录已存在但缺 SKILL.md 视为未完成安装,允许补装。返回结果字典,失败抛
    :class:`SkillInstallError`(code 结构化上抛)。
    """
    target_file = target_dir / "SKILL.md"
    if target_file.is_dir():
        raise SkillInstallError("target_is_directory", f"目标是目录不是文件: {target_file}")
    if target_file.exists() and not force:
        raise SkillInstallError(
            "target_exists",
            f"目标已存在: {target_file}(--force 覆盖,或 --path 换目录)",
        )
    try:
        if link:
            target_dir.mkdir(parents=True, exist_ok=True)
            if target_file.exists() or target_file.is_symlink():
                target_file.unlink()
            os.symlink(str(source.resolve()), target_file)
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            if target_file.exists() or target_file.is_symlink():
                # 先摘除旧目标(含悬空链接)再写:copy2 沿符号链接写入会把
                # 链接指向的文件(可能在安装目录之外)整体写穿。与 --link
                # 分支同一判据,授权范围是 <技能根>/myia/SKILL.md 这一路径。
                target_file.unlink()
            shutil.copy2(source, target_file)
    except OSError as exc:
        raise SkillInstallError(
            "symlink_failed" if link else "copy_failed", f"写入 {target_file} 失败: {exc}"
        ) from exc
    return {
        "target": str(target_file),
        "mode": "link" if link else "copy",
        "overwritten": None,  # 由调用方按安装前的存在性回填
    }


def _skill_install_payload(args: argparse.Namespace, *, as_json: bool) -> tuple[dict[str, Any], int]:
    """Run one install and build its report(退出码与报告一起返回)."""
    source = skill_source()
    if source is None:
        env_source = os.environ.get(SKILL_SOURCE_ENV)
        if env_source:
            hint = f"{SKILL_SOURCE_ENV}={env_source} 指向的文件不存在"
        else:
            hint = (f"源码仓库内运行,或设 {SKILL_SOURCE_ENV} 指向 SKILL.md;"
                    f"探测过 {Path(__file__).resolve().parents[2] / 'skill' / 'SKILL.md'}")
        _emit_generic_error(
            "skill_source",
            f"找不到 skill/SKILL.md 源({hint})",
            as_json=as_json,
            code="skill_source_not_found",
        )
        return {}, EXIT_CONFIG_ERROR
    target_dir, agent = _skill_install_target(args)
    existed_before = (target_dir / "SKILL.md").exists()
    try:
        result = _skill_install_one(source, target_dir, link=args.link, force=args.force)
    except SkillInstallError as exc:
        _emit_generic_error("skill_install", str(exc), as_json=as_json, code=exc.code)
        return {}, EXIT_CONFIG_ERROR
    payload = {
        "command": "skill",
        "action": "install",
        "agent": agent,
        "source": str(source),
        "target_dir": str(target_dir),
        "target": result["target"],
        "mode": result["mode"],
        "forced": bool(args.force),
        "overwritten": existed_before,
    }
    return payload, EXIT_OK


def _print_human_skill_path(payload: dict[str, Any]) -> None:
    """人类可读的 path 报告(与 --json 同一信息)."""
    source = payload["source"]
    state = str(source["path"]) if source["found"] else "未找到(源码仓库内运行或设 MYIA_SKILL_SOURCE)"
    print(f"世事 skill path:源 {state}")
    for agent in payload["agents"]:
        mark = "已装" if agent["installed"] else "未装"
        if agent["installed"]:
            mark += "(链接)" if agent["is_link"] else "(副本)"
        print(f"  {agent['agent']}: {agent['skill_file']} [{mark}]")


def _cmd_skill(args: argparse.Namespace) -> int:
    """``shishi skill install|path``:纯文件操作,退出码只有 0/1。"""
    as_json = args.as_json
    _configure_logging(as_json=as_json)
    if args.skill_command == "path":
        payload = _skill_path_payload()
        if as_json:
            _print_json(payload)
        else:
            _print_human_skill_path(payload)
        return EXIT_OK
    payload, exit_code = _skill_install_payload(args, as_json=as_json)
    if exit_code != EXIT_OK:
        return exit_code
    if as_json:
        _print_json(payload)
    else:
        note = f"({'--force 覆盖' if payload['overwritten'] else '新装'},{payload['mode']})"
        print(f"已安装 myia 技能 {note} → {payload['target']}")
        print(f"源:{payload['source']}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _not_implemented(command: str) -> int:
    """子命令留位的结构化提示(退出码 1,机器可读)。"""
    print(
        json.dumps(
            {
                "error": "not_implemented",
                "command": command,
                "message": f"{command} 子命令规划于后续版本实现,当前未实装",
                "scheduled_version": "v0.2",
            },
            ensure_ascii=False,
        )
    )
    return EXIT_CONFIG_ERROR


# ---------------------------------------------------------------------------
# myia osint(v1.1 源码型插件样板:适配器在插件目录,动态加载,零静态耦合)
# ---------------------------------------------------------------------------


def _import_plugin_adapter(plugins_dir: str | Path, plugin_id: str) -> Any:
    """动态加载 ``<plugins_dir>/<plugin_id>/adapter.py``(插件目录非包,单文件加载).

    刻意不做静态 import:核心 wheel 不含 plugins/,静态耦合会让核心反过来
    依赖插件侧文件 —— 恰是铁律要防的反向。加载用 compile + exec(模块命名
    空间),**不走 importlib 的 SourceFileLoader**:后者会在插件目录写
    ``__pycache__`` 字节码垃圾,污染插件包形状(仓库即公开)。
    加载失败抛 OSError/SyntaxError,由调用命令结构化降级(退 1)。
    """
    adapter_file = Path(plugins_dir) / plugin_id / "adapter.py"
    if not adapter_file.is_file():
        raise FileNotFoundError(f"适配器不存在:{adapter_file}(样板应随仓库 plugins/ 分发)")
    module = types.ModuleType(f"myia_{plugin_id.replace('-', '_')}_adapter")
    module.__file__ = str(adapter_file)
    source = adapter_file.read_text(encoding="utf-8")
    executable = compile(source, str(adapter_file), "exec")
    exec(executable, module.__dict__)  # noqa: S102 - 适配器是仓库内受控代码,非任意用户输入
    return module


def _import_osint_adapter(plugins_dir: str | Path) -> Any:
    """``shishi osint`` 的适配器加载(:func:`_import_plugin_adapter` 的样板别名)."""
    return _import_plugin_adapter(plugins_dir, OSINT_PLUGIN_ID)


def _print_osint_human(payload: dict[str, Any]) -> None:
    """人类可读摘要(与 --json 同一信息,另一种皮)."""
    vendor = payload.get("vendor") or {}
    print(f"世事 osint:{payload.get('target')}({payload.get('plugin')}) 状态:{payload.get('status')}")
    if vendor.get("commit"):
        print(f"  vendor pin:{vendor['commit']}")
    results = payload.get("results") or {}
    for name in sorted(results):
        values = results[name]
        count = len(values) if isinstance(values, list) else (1 if values else 0)
        if count:
            print(f"  {name}:{count} 条")
    datasets = ",".join(payload.get("datasets") or []) or "无"
    print(f"  耗时:{payload.get('duration_seconds')}s;数据集:{datasets}")


def _cmd_osint(args: argparse.Namespace) -> int:
    """``shishi osint``:跑一次上游 Photon 侦察,结构化输出.

    退出码:0 成功;1 适配器缺失/vendor 未初始化/目标非法(配置或环境错误);
    2 采集失败(Photon 非零退出/超时/导出缺失或损坏)。失败码到退出码的
    映射用 :data:`OSINT_FETCH_FAILURE_CODES`(CLI 所有,不依赖插件侧导出)。
    任何失败都只影响本命令,核心品类流水线照常(铁律)。
    """
    try:
        adapter = _import_osint_adapter(args.plugins_dir)
    except (OSError, ImportError, SyntaxError) as exc:
        _emit_generic_error(
            "osint_adapter_missing",
            str(exc),
            as_json=args.as_json,
            plugins_dir=str(args.plugins_dir),
        )
        return EXIT_CONFIG_ERROR
    try:
        payload = adapter.run(args.target, timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001 — 适配器一切失败都结构化降级,绝不拦核心
        details = exc.to_dict() if hasattr(exc, "to_dict") else {"code": "osint_failed", "message": str(exc)}
        code = str(details.get("code", "osint_failed"))
        exit_code = EXIT_FETCH_ALL_FAILED if code in OSINT_FETCH_FAILURE_CODES else EXIT_CONFIG_ERROR
        extra = {key: value for key, value in details.items() if key not in ("code", "message")}
        _emit_generic_error(code, str(details.get("message", exc)), as_json=args.as_json, **extra)
        return exit_code
    if args.as_json:
        _print_json(payload)
    else:
        _print_osint_human(payload)
    return EXIT_OK


def _print_proxy_human(payload: dict[str, Any]) -> None:
    """人类可读摘要(与 --json 同一信息,另一种皮)."""
    print(
        f"MYIA proxy:{payload.get('status')}(候选 {payload.get('fetched')},"
        f"测活 {payload.get('checked')},可用 {len(payload.get('alive') or [])})"
    )
    for source in payload.get("sources") or []:
        state = f"{source.get('proxies')} 条" if not source.get("error") else f"失败({source.get('error')})"
        print(f"  源:{source.get('url')} → {state}")
    for item in payload.get("alive") or []:
        print(f"  可用:{item.get('proxy')}(延迟 {item.get('latency_ms')}ms)")
    print(f"  耗时:{payload.get('duration_seconds')}s")


def _cmd_proxy(args: argparse.Namespace) -> int:
    """``shishi proxy``:进程内轻量代理抓取+测活(myia-proxy 插件,零 Redis 零 docker).

    退出码:0 成功(拿到 ≥1 个可用代理);1 适配器缺失/用法错误(count/timeout
    非法);2 采集失败(全部源抓取失败 ``fetch_failed`` / 测活零可用
    ``no_alive_proxy``)。失败码到退出码的映射用 :data:`PROXY_FETCH_FAILURE_CODES`
    (CLI 所有,不依赖插件侧导出)。任何失败都只影响本命令,核心品类流水线
    照常(铁律)。
    """
    try:
        adapter = _import_plugin_adapter(args.plugins_dir, PROXY_PLUGIN_ID)
    except (OSError, ImportError, SyntaxError) as exc:
        _emit_generic_error(
            "proxy_adapter_missing",
            str(exc),
            as_json=args.as_json,
            plugins_dir=str(args.plugins_dir),
        )
        return EXIT_CONFIG_ERROR
    try:
        payload = adapter.run(count=args.count, check_timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001 — 适配器一切失败都结构化降级,绝不拦核心
        details = exc.to_dict() if hasattr(exc, "to_dict") else {"code": "proxy_failed", "message": str(exc)}
        code = str(details.get("code", "proxy_failed"))
        exit_code = EXIT_FETCH_ALL_FAILED if code in PROXY_FETCH_FAILURE_CODES else EXIT_CONFIG_ERROR
        extra = {key: value for key, value in details.items() if key not in ("code", "message")}
        _emit_generic_error(code, str(details.get("message", exc)), as_json=args.as_json, **extra)
        return exit_code
    if args.as_json:
        _print_json(payload)
    else:
        _print_proxy_human(payload)
    return EXIT_OK


def _add_proxy_parser(sub: argparse._SubParsersAction) -> None:
    """``shishi proxy``:进程内轻量代理抓取+测活(v1.1 desktop 分级 myia-proxy)."""
    proxy = sub.add_parser(
        "proxy",
        help="轻量代理抓取+测活(myia-proxy 插件,进程内、零 Redis 零 docker;失败绝不拦核心)",
        description=(
            "桌面路径(默认):定位 <plugins-dir>/myia-proxy(适配器 adapter.py),"
            "进程内抓取公开免费代理列表并逐个测活(经代理请求校验目标),输出结构化"
            "结果。零 Redis 零 docker;完整 proxy_pool 服务形态(定时抓取+池化+API)"
            "见 docker/plugins/ 下的可选服务端部署。全部源抓取失败或测活零可用 → "
            "结构化错误退 2;适配器缺失/用法错误退 1;任何失败都不影响核心品类"
            "流水线(铁律)。"
        ),
    )
    proxy.add_argument(
        "--count",
        type=int,
        default=DEFAULT_PROXY_COUNT,
        help=f"期望拿到的可用代理数(默认 {DEFAULT_PROXY_COUNT};测活凑够即止)",
    )
    proxy.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_PROXY_CHECK_TIMEOUT_SECONDS,
        help=f"单代理测活超时秒数(默认 {DEFAULT_PROXY_CHECK_TIMEOUT_SECONDS:g})",
    )
    proxy.add_argument(
        "--plugins-dir",
        default=DEFAULT_PLUGINS_DIR,
        help=f"插件目录(默认 ./{DEFAULT_PLUGINS_DIR},样板位于 myia-proxy/ 子目录)",
    )
    proxy.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出(单份 JSON,stdout)")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口(pyproject console_scripts:``shishi = "myia.cli:main"``)。

    Args:
        argv: 参数列表;None 表示 ``sys.argv[1:]``。

    Returns:
        退出码:0 成功 / 1 配置或用法错误 / 2 采集全部失败 / 3 部分失败。

    Raises:
        SystemExit: ``--help`` / ``--version``(exit 0)。
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        print(
            json.dumps({"error": "usage", "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    if args.command is None:
        parser.print_help()
        return EXIT_OK
    handlers = {
        "run": _cmd_run,
        "init": _cmd_init,
        "test": _cmd_test,
        "list": _cmd_list,
        "doctor": _cmd_doctor,
        "secret": _cmd_secret,
        "plugin": _cmd_plugin,
        "channels": _cmd_channels,
        "feedback": _cmd_feedback,
        "skill": _cmd_skill,
        "osint": _cmd_osint,
        "proxy": _cmd_proxy,
    }
    handler = handlers.get(args.command)
    if handler is not None:
        return handler(args)
    return _not_implemented(args.command)


if __name__ == "__main__":
    raise SystemExit(main())

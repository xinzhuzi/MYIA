"""myia-credhunter 插件 ghhunt 模块(R1 GitHub 工件猎手)契约测试。

任务 10-03-aipocket-fusion R1 配套测试:零真网(全部 I/O 走注入的
httpx.AsyncClient + 异步 MockTransport)、零真实凭据(fixture 均为人工
合成的脱敏键,不对应任何真实账号)、限速退避走 FakeClock(零真实等待)。

被钉住的契约(行为规格 = ``research/behavior-specs/ghhunt.md``):

1. **规格数值**:5 页 ×100、commit 预算 12(Q8)、退避上限 90s、blob
   ≤1 MiB、工件 ≤200/并发 8/重试 5;
2. **噪音路径过滤**:.env 系永不判噪、examples/docs/testdata/mocks 等
   目录段与目录清单文件/文档 basename 丢弃;
3. **统一 diff 三侧解析**:hunk 行号计数、侧别分类、元数据行跳过、
   Added→Removed→Context 聚合扫描(被删除行密钥仍可见)与行号范围;
4. **token 池轮转 + 403/429 退避**:同轮换 token、整轮全 429 才退避
   (retry-after 优先、reset-now 兜底、+1s、仅重试一轮、>90s 放弃)、
   非 403/429 直接失败、无 token 报错;
5. **code 泳道**:页游标接续/提前停/页数封顶重置、公开性(visibility
   仅 code 泳道算数)、噪音过滤、blob ≤1 MiB 才采用(替换基础文本);
6. **commit 泳道**:仅第 1 页、预算轮转跨 run 接续、仅 private==false、
   单查询报错继续下一查询;
7. **工件二次加工**:重试后 terminal / 耗尽 transient / 超限
   artifact_too_large、≤200 领取、并发 ≤8、blob diff 三侧增强;
8. **Q9 掩码-only**:全部 items 经 find_full_key_leak 自检零全文密钥
   (含兄弟键:同文本多键时彼此不全文入 content)、apiurl 只走前缀
   静态映射。

加载纪律:与 tests/test_credhunter_fingerprints.py 同款 compile+exec
手法加载 ghhunt(模块内 _sibling 自举指纹库/findings,canonical 名与
适配器共享 —— 有复用性断言)。
"""

from __future__ import annotations

import asyncio
import base64
import sys
import types
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from conftest import FakeClock

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = REPO_ROOT / "plugins"
PLUGIN_DIR = PLUGINS_DIR / "myia-credhunter"

# ---------------------------------------------------------------------------
# 合成脱敏 fixture 键(人工构造,不对应任何真实账号;避开噪声子串与
# ≥8 字母序递增段,只服务于正则行为验证)。
# ---------------------------------------------------------------------------
KEY_XAI = "xai-Hs5vBn8mKj2wQr7tYc3f"
KEY_KIRO = "ksk_Pz6xWc4vNk9mQt2bGj7y"
KEY_CURSOR = "crsr_Ax7vNm3kQp9wZt5bHc2jLr8sYd4fGu61e"
KEY_QODER = "pt-Dw8cXm5nBq3vKr7tZj9y"
ALL_FIXTURE_KEYS = (KEY_XAI, KEY_KIRO, KEY_CURSOR, KEY_QODER)

#: 脱敏统一 diff fixture(三侧各埋一键;行号断言依赖此结构)。
DIFF_TEXT = "\n".join(
    [
        "diff --git a/config.py b/config.py",
        "index 1a2b3c4..5d6e7f8 100644",
        "--- a/config.py",
        "+++ b/config.py",
        "@@ -10,4 +10,5 @@ def load():",
        '     base = "https://api.example.test"',
        f'-    token = "{KEY_KIRO}"',
        f'+    token = "{KEY_XAI}"',
        f'+    spare = "{KEY_CURSOR}"',
        "     return base",
    ]
)


def _load_ghhunt() -> Any:
    """按适配器同款 compile+exec 手法加载 ghhunt 子模块(零 __pycache__)。"""
    canonical = "myia_credhunter_ghhunt"
    module = sys.modules.get(canonical)
    if module is not None:
        return module
    module_file = PLUGIN_DIR / "credhunter" / "ghhunt.py"
    module = types.ModuleType(canonical)
    module.__file__ = str(module_file)
    sys.modules[canonical] = module
    executable = compile(module_file.read_text(encoding="utf-8"), str(module_file), "exec")
    exec(executable, module.__dict__)  # noqa: S102 - 仓库内受控插件代码
    return module


gh = _load_ghhunt()
findings_mod = gh._findings


# ---------------------------------------------------------------------------
# 测试基建:异步 MockTransport(httpx.MockTransport 只收同步 handler,
# 并发断言需要 handler 内 await,故自建 AsyncBaseTransport)+ 请求记录器。
# ---------------------------------------------------------------------------


class _AsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler: Callable[[httpx.Request], Any]) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._handler(request)


def make_client(handler: Callable[[httpx.Request], Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_AsyncTransport(handler))


def json_response(payload: Any, status_code: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=payload, headers=headers or {})


class Recorder:
    """请求记录器:(method, path, query-params) 三元组列表。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def record(self, request: httpx.Request) -> None:
        self.calls.append((request.method, request.url.path, dict(request.url.params)))

    def paths(self, path: str) -> list[tuple[str, str, dict[str, str]]]:
        return [call for call in self.calls if call[1] == path]


async def nosleep(seconds: float) -> None:
    """零等待睡眠(工件重试退避注入口,测试不真睡)。"""


async def _hunt_and_close(handler: Callable[[httpx.Request], Any], **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("tokens", ["tok-test"])
    kwargs.setdefault("artifact_sleep", nosleep)
    async with make_client(handler) as client:
        return await gh.hunt(client=client, **kwargs)


def run_hunt(handler: Callable[[httpx.Request], Any], **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(_hunt_and_close(handler, **kwargs))


def run_client(
    handler: Callable[[httpx.Request], Any],
    coro_factory: Callable[[Any], Any],
    *,
    tokens: tuple[str, ...] = ("tok-a",),
    clock: FakeClock | None = None,
) -> tuple[Any, FakeClock]:
    """在独立事件循环里跑一个 GithubClient 协程(客户端同循环开关)。"""
    clock = clock or FakeClock()

    async def _go() -> Any:
        async with make_client(handler) as client:
            github_client = gh.GithubClient(client=client, tokens=tokens, now=clock.time, sleep=clock.sleep)
            return await coro_factory(github_client)

    return asyncio.run(_go()), clock


def run_artifacts(
    handler: Callable[[httpx.Request], Any],
    works: list[Any],
    **kwargs: Any,
) -> dict[str, Any]:
    async def _go() -> dict[str, Any]:
        async with make_client(handler) as client:
            github_client = gh.GithubClient(client=client, tokens=["tok-test"])
            kwargs.setdefault("sleep", nosleep)
            return await gh.process_artifacts(github_client, works, **kwargs)

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# GitHub API 响应 fixture 构造(形状照 GitHub REST 文档,内容全合成)
# ---------------------------------------------------------------------------


def code_entry(
    *,
    repo: str = "octo/demo",
    path: str = "config/.env",
    sha: str = "bl0b11",
    private: bool | None = False,
    visibility: str | None = None,
    fragments: tuple[str, ...] = (),
    omit_text_matches: bool = False,
) -> dict[str, Any]:
    repository: dict[str, Any] = {"full_name": repo}
    if private is not None:
        repository["private"] = private
    if visibility is not None:
        repository["visibility"] = visibility
    entry: dict[str, Any] = {
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "sha": sha,
        "html_url": f"https://github.com/{repo}/blob/{sha}/{path}",
        "repository": repository,
    }
    if not omit_text_matches:
        entry["text_matches"] = [{"fragment": fragment, "object_url": f"https://github.com/{repo}"} for fragment in fragments]
    return entry


def commit_entry(
    *,
    repo: str = "octo/demo",
    sha: str = "c0ffee",
    private: bool | None = False,
    message: str = "",
    omit_private: bool = False,
) -> dict[str, Any]:
    repository: dict[str, Any] = {"full_name": repo}
    if not omit_private:
        repository["private"] = private
    return {
        "sha": sha,
        "html_url": f"https://github.com/{repo}/commit/{sha}",
        "repository": repository,
        "commit": {"message": message},
    }


def blob_payload(text: str, *, size: int | None = None, omit_size: bool = False) -> dict[str, Any]:
    raw = text.encode("utf-8")
    payload: dict[str, Any] = {
        "content": base64.b64encode(raw).decode("ascii"),
        "encoding": "base64",
    }
    if not omit_size:
        payload["size"] = len(raw) if size is None else size
    return payload


# ---------------------------------------------------------------------------
# 契约一:规格数值(测试钉死)
# ---------------------------------------------------------------------------


class TestSpecValues:
    def test_upstream_defaults_pinned(self):
        assert gh.CODE_PAGES_MAX == 5
        assert gh.PER_PAGE_DEFAULT == 100
        assert gh.COMMIT_QUERY_BUDGET == 12  # Q8
        assert gh.RATE_LIMIT_MAX_WAIT_SECONDS == 90.0
        assert gh.MAX_BLOB_BYTES == 1024 * 1024
        assert gh.ARTIFACT_BATCH_LIMIT == 200
        assert gh.ARTIFACT_CONCURRENCY == 8
        assert gh.ARTIFACT_RETRY_LIMIT == 5
        assert gh.GITHUB_API_BASE == "https://api.github.com"
        assert gh.API_VERSION == "2022-11-28"

    def test_sibling_modules_shared_with_adapter(self):
        """后加载的 ghhunt 经 canonical 名复用适配器的兄弟模块对象(生产接线顺序)。"""
        from myia.cli import _import_plugin_adapter

        adapter = _import_plugin_adapter(PLUGINS_DIR, "myia-credhunter")
        # 适配器先加载四子模块;随后 compile+exec 加载的 ghhunt 自举时复用之
        module_file = PLUGIN_DIR / "credhunter" / "ghhunt.py"
        late = types.ModuleType("myia_credhunter_ghhunt_late")
        late.__file__ = str(module_file)
        sys.modules["myia_credhunter_ghhunt_late"] = late
        exec(compile(module_file.read_text(encoding="utf-8"), str(module_file), "exec"), late.__dict__)  # noqa: S102
        assert late._findings is adapter.findings
        assert late._fingerprints is adapter.fingerprints


# ---------------------------------------------------------------------------
# 契约二:噪音路径过滤
# ---------------------------------------------------------------------------


class TestNoisePathFilter:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            (".env", False),
            ("config/.env", False),
            (".env.example", False),  # basename .env 前缀永不判噪
            ("deploy/.env.production", False),
            ("examples/app.py", True),
            ("docs/guide/config.py", True),
            ("documentation/reference.py", True),
            ("samples/seed.py", True),
            ("test/fixtures/auth.py", True),
            ("testdata/a.json", True),
            ("pkg/test-data/b.py", True),
            ("src/test_data/c.py", True),
            ("src/mocks/client.py", True),
            ("vendor/mock/thing.py", True),
            ("changelog/notes.txt", True),
            ("pkg/catalog.json", True),
            ("openclaw.plugin.json", True),
            ("a/provider-catalog/b.py", True),
            ("conf/official-external-provider.yaml", True),
            ("README.md", True),
            ("notes.md", True),
            ("docs/CHANGELOG.rst", True),
            ("LICENSE", True),
            ("readme-first", True),
            ("src/config/settings.py", False),
            ("", False),  # 空路径保守放行
        ],
    )
    def test_noise_paths(self, path, expected):
        assert gh.should_discard_path(path) is expected


# ---------------------------------------------------------------------------
# 契约三:统一 diff 三侧解析
# ---------------------------------------------------------------------------


class TestUnifiedDiffParsing:
    def test_sides_and_line_counters(self):
        lines = gh.parse_diff_sides(DIFF_TEXT)
        assert [line.side for line in lines] == ["context", "removed", "added", "added", "context"]
        # 元数据行(diff/index/---/+++)全部跳过
        assert all(line.content or line.side == "context" for line in lines)
        # 行号:removed 记 old,added 记 new,context 双侧同进
        assert (lines[0].old_lineno, lines[0].new_lineno) == (10, 10)
        assert lines[1].old_lineno == 11 and lines[1].new_lineno is None
        assert lines[2].new_lineno == 11 and lines[2].old_lineno is None
        assert lines[3].new_lineno == 12
        assert (lines[4].old_lineno, lines[4].new_lineno) == (12, 13)
        # 首字符剥离后才是内容
        assert lines[1].content == f'    token = "{KEY_KIRO}"'

    def test_skip_prefixes_empty_line_and_stray_chars(self):
        text = "@@ -1,2 +1,2 @@\n kept\n\\ No newline at end of file\n> stray\n\n-removed line"
        lines = gh.parse_diff_sides(text)
        # 反斜杠行跳过、">" 首字符丢弃、空行 → context(两计数器同进)
        assert [(line.side, line.content) for line in lines] == [
            ("context", "kept"),
            ("context", ""),
            ("removed", "removed line"),
        ]
        assert (lines[0].old_lineno, lines[0].new_lineno) == (1, 1)
        assert lines[2].old_lineno == 3

    def test_multibyte_first_char_is_not_a_marker(self):
        # 首字符按码点剥离:多字节字符既非 +/- 也非空格 → 整行丢弃
        assert gh.parse_diff_sides("@@ -1,1 +1,1 @@\n中文 context 行") == []

    def test_hunk_header_resets_counters_across_hunks(self):
        text = "@@ -1,1 +1,1 @@\n-a\n@@ -40,2 +50,3 @@\n+b\n+c"
        lines = gh.parse_diff_sides(text)
        assert [(line.side, line.old_lineno, line.new_lineno) for line in lines] == [
            ("removed", 1, None),
            ("added", None, 50),
            ("added", None, 51),
        ]

    def test_diff_detection_heuristic(self):
        assert gh.is_unified_diff(DIFF_TEXT) is True
        assert gh.is_unified_diff("plain text\nno hunks here") is False
        # text_matches 基础文本(JSON 串,换行被转义)不会误判为 diff
        import json

        assert gh.is_unified_diff(json.dumps([{"fragment": "@@ -1,2 +1,2 @@\nkey"}])) is False


class TestPatchScan:
    def test_three_side_aggregation(self):
        side_hits = gh.scan_unified_patch(DIFF_TEXT)
        by_side: dict[str, list[Any]] = {}
        for side_hit in side_hits:
            by_side.setdefault(side_hit.change_side, []).append(side_hit)
        # Added 侧两键,Removed 侧一键(被删除行里的密钥仍然可见),Context 侧零
        assert {sh.hit.apikey for sh in by_side["added"]} == {KEY_XAI, KEY_CURSOR}
        assert {sh.hit.apikey for sh in by_side["removed"]} == {KEY_KIRO}
        assert "context" not in by_side
        # 行号范围 = 该侧所选行行号的最小/最大值(非命中行精确行号)
        for side_hit in by_side["added"]:
            assert (side_hit.line_start, side_hit.line_end) == (11, 12)
        for side_hit in by_side["removed"]:
            assert (side_hit.line_start, side_hit.line_end) == (11, 11)

    def test_scan_text_flat_for_non_diff(self):
        side_hits = gh.scan_text(f'x = "{KEY_XAI}"')
        assert len(side_hits) == 1
        assert side_hits[0].change_side == "context"
        assert side_hits[0].line_start is None
        assert side_hits[0].line_end is None
        assert gh.scan_text("") == []

    def test_scan_text_routes_diff(self):
        side_hits = gh.scan_text(DIFF_TEXT)
        assert {side_hit.change_side for side_hit in side_hits} == {"added", "removed"}


# ---------------------------------------------------------------------------
# 契约四:token 池轮转 + 403/429 退避
# ---------------------------------------------------------------------------


class TestTokenPool:
    def test_rotation_advances_start(self):
        pool = gh.TokenPool(["t1", "t2", "t3"])
        assert gh.TokenPool.token_at(pool, pool.next_start(), 0) == "t1"
        start = pool.next_start()
        assert gh.TokenPool.token_at(pool, start, 0) == "t2"
        assert gh.TokenPool.token_at(pool, start, 1) == "t3"  # 同轮依次尝试全部 token
        start = pool.next_start()
        assert gh.TokenPool.token_at(pool, start, 3) == "t3"  # 环形回绕

    def test_missing_tokens_raise(self):
        with pytest.raises(gh.GithubHuntError) as excinfo:
            gh.TokenPool(["  ", ""])
        assert excinfo.value.code == "tokens_missing"


class TestClientBackoff:
    def test_request_headers_and_params(self):
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            captured["params"] = dict(request.url.params)
            return json_response({"items": []})

        payload, _clock = run_client(handler, lambda gc: gc.search_code("sk- filename:.env", page=2, per_page=100))
        assert payload == {"items": []}
        assert captured["headers"]["authorization"] == "Bearer tok-a"
        assert captured["headers"]["x-github-api-version"] == "2022-11-28"
        assert captured["headers"]["accept"] == "application/vnd.github+json"
        assert captured["params"] == {"q": "sk- filename:.env", "page": "2", "per_page": "100"}

    def test_403_falls_through_to_next_token_without_backoff(self):
        seen_tokens: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_tokens.append(request.headers["authorization"])
            if request.headers["authorization"] == "Bearer tok-a":
                return httpx.Response(403, headers={"retry-after": "5"}, json={"message": "secondary limit"})
            return json_response({"items": [1]})

        async def call(gc: Any) -> Any:
            return await gc.search_commits("QODER_PAT")

        payload, clock = run_client(handler, call, tokens=("tok-a", "tok-b"))
        assert payload == {"items": [1]}
        assert seen_tokens == ["Bearer tok-a", "Bearer tok-b"]
        assert clock.sleeps == []  # 同轮内换 token 不触发退避

    def test_all_tokens_rate_limited_backoff_then_success(self):
        clock = FakeClock()
        calls = {"count": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            if calls["count"] <= 2:  # 第一轮两个 token 全 429
                return httpx.Response(429, headers={"retry-after": "2"}, json={})
            return json_response({"resources": {}})

        payload, clock = run_client(handler, lambda gc: gc.get_rate_limit(), tokens=("t1", "t2"), clock=clock)
        assert payload == {"resources": {}}
        assert clock.sleeps == [3.0]  # 等待值 2 + 1 秒
        assert calls["count"] == 3  # 重试仅一轮

    def test_reset_header_wait_over_limit_gives_up(self):
        clock = FakeClock()  # now = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            # retry-after 缺席 → x-ratelimit-reset - now = 100 > 90
            return httpx.Response(403, headers={"x-ratelimit-reset": "100"}, json={})

        async def call(gc: Any) -> Any:
            return await gc.search_code("q")

        with pytest.raises(gh.GithubHuntError) as excinfo:
            run_client(handler, call, clock=clock)
        assert excinfo.value.code == "github_rate_limit_wait_too_long"
        assert excinfo.value.details["wait_seconds"] == 100.0

    def test_still_rate_limited_after_single_retry_round(self):
        clock = FakeClock()
        calls = {"count": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(429, headers={"retry-after": "30"}, json={})

        async def call(gc: Any) -> Any:
            return await gc.search_code("q")

        with pytest.raises(gh.GithubHuntError) as excinfo:
            run_client(handler, call, clock=clock)
        assert excinfo.value.code == "github_rate_limited"
        assert clock.sleeps == [31.0]
        assert calls["count"] == 2  # 单 token:初始轮 + 一次重试轮

    def test_non_rate_error_fails_immediately(self):
        recorder = Recorder()

        async def handler(request: httpx.Request) -> httpx.Response:
            recorder.record(request)
            return httpx.Response(500, json={"message": "boom"})

        async def call(gc: Any) -> Any:
            return await gc.search_code("q")

        with pytest.raises(gh.GithubHuntError) as excinfo:
            run_client(handler, call)
        assert excinfo.value.code == "github_api_error"
        assert excinfo.value.details["status"] == 500
        assert len(recorder.calls) == 1  # 非 403/429 不重试

    def test_invalid_json_payload_raises(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not-json", headers={"content-type": "text/plain"})

        async def call(gc: Any) -> Any:
            return await gc.search_code("q")

        with pytest.raises(gh.GithubHuntError) as excinfo:
            run_client(handler, call)
        assert excinfo.value.code == "github_response_invalid"


# ---------------------------------------------------------------------------
# 契约五:code 泳道
# ---------------------------------------------------------------------------


class TestCodeLane:
    def test_early_stop_and_checkpoint_reset(self):
        recorder = Recorder()

        async def handler(request: httpx.Request) -> httpx.Response:
            recorder.record(request)
            if request.url.path == "/search/code":
                page = int(request.url.params["page"])
                items = [code_entry(sha="bl0b11", fragments=[f'x = "{KEY_XAI}"'])] if page == 1 else []
                return json_response({"items": items})
            if request.url.path.startswith("/repos/octo/demo/git/blobs/"):
                return json_response(blob_payload(f'x = "{KEY_XAI}"'))
            return json_response({})

        result = run_hunt(handler, queries=["sk- filename:.env"], per_page=2)
        # 页 1 返回 1 条 < per_page=2 → 提前停页(只搜了一次)
        assert [call[2]["page"] for call in recorder.paths("/search/code")] == ["1"]
        # 跑完即重置游标为 1
        assert result["checkpoint"]["code_pages"]["sk- filename:.env"] == 1
        assert result["counts"]["code_items"] == 1
        assert result["counts"]["code_pages"] == 1
        assert any(item["provider"] == "xai" for item in result["items"])

    def test_page_cap_five_pages_then_reset(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/code":
                # 恒满页(每页 1 条 = per_page)→ 走满 1..=5 页封顶
                return json_response({"items": [code_entry(sha=f"sha{request.url.params['page']}")]})
            return json_response(blob_payload(""))

        result = run_hunt(handler, queries=["q"], per_page=1)
        assert result["counts"]["code_pages"] == 5
        assert result["checkpoint"]["code_pages"]["q"] == 1  # 封顶跑完同样重置

    def test_cursor_resumes_at_failed_page(self):
        async def failing_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/code":
                page = int(request.url.params["page"])
                if page == 1:
                    return json_response({"items": [code_entry(sha="p1"), code_entry(sha="p2")]})  # 满页
                return httpx.Response(500, json={"message": "boom"})
            return json_response(blob_payload(""))

        result1 = run_hunt(failing_handler, queries=["q1"], per_page=2)
        # 页 2 报错:记 errors、游标停在 2、不打断整轮
        assert result1["checkpoint"]["code_pages"]["q1"] == 2
        assert any(error["code"] == "github_api_error" and error["page"] == 2 for error in result1["errors"])

        recorder = Recorder()

        async def resuming_handler(request: httpx.Request) -> httpx.Response:
            recorder.record(request)
            return json_response({"items": []})

        run_hunt(resuming_handler, queries=["q1"], per_page=2, checkpoint=result1["checkpoint"])
        # 带 checkpoint 续跑:直接从页 2 开始
        assert [call[2]["page"] for call in recorder.paths("/search/code")] == ["2"]

    def test_publicity_filter(self):
        entries = [
            code_entry(sha="a1", fragments=[f'k = "{KEY_XAI}"']),
            code_entry(sha="b2", private=True, fragments=[f'k = "{KEY_XAI}"']),
            code_entry(sha="c3", repo="octo/vis", visibility="public", fragments=[f'k = "{KEY_XAI}"']),
            code_entry(sha="d4", repo="octo/none", private=None, fragments=[f'k = "{KEY_XAI}"']),  # private 缺失
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/code":
                return json_response({"items": entries})
            return json_response(blob_payload(f'k = "{KEY_XAI}"\n'))

        result = run_hunt(handler, queries=["q"], per_page=10)
        assert result["counts"]["code_items"] == 4
        assert result["counts"]["code_private_skipped"] == 2  # 私库 + private 缺失
        # visibility 兜底在 code 泳道算数:a1 与 c3 各产泳道 + 工件两条
        assert {item["object_sha"] for item in result["items"]} == {"a1", "c3"}

    def test_noise_path_filtered(self):
        entries = [
            code_entry(path="docs/guide.md", sha="n1", fragments=[f'k = "{KEY_XAI}"']),
            code_entry(path="config/.env", sha="n2", fragments=[f'k = "{KEY_XAI}"']),
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/code":
                return json_response({"items": entries})
            return json_response(blob_payload(f'k = "{KEY_XAI}"\n'))

        result = run_hunt(handler, queries=["q"], per_page=10)
        assert result["counts"]["noise_dropped"] == 1
        kept = [item for item in result["items"] if item["provider"] == "xai"]
        assert kept and all(item["file_path"] == "config/.env" for item in kept)

    def test_blob_replaces_base_text_when_decodable(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/code":
                return json_response({"items": [code_entry(fragments=["nothing secret in fragment"])]})
            return json_response(blob_payload(f'token = "{KEY_KIRO}"\n'))

        result = run_hunt(handler, queries=["ksk_ kiro"], per_page=10)
        kiro_items = [item for item in result["items"] if item["provider"] == "kiro"]
        # blob 全文替换基础文本:泳道扫描 + 工件二次加工各产一条(上游双段语义)
        assert len(kiro_items) == 2
        assert all(item["source_type"] == "code_snapshot" for item in kiro_items)
        assert all(item["object_sha"] == "bl0b11" for item in kiro_items)
        assert all(item["query"] == "ksk_ kiro" for item in kiro_items)
        assert all(item["file_path"] == "config/.env" for item in kiro_items)

    def test_blob_too_large_by_size_field_falls_back_to_base_text(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/code":
                return json_response({"items": [code_entry(fragments=[f'k = "{KEY_QODER}"'])]})
            return json_response(blob_payload("x" * 64, size=gh.MAX_BLOB_BYTES + 1))  # size 快速路径

        result = run_hunt(handler, queries=["pt- filename:.env"], per_page=10)
        assert result["counts"]["blob_too_large"] >= 1
        # blob 被弃 → 回落基础文本(fragment 命中仍产出)
        assert any(item["provider"] == "qoder" for item in result["items"])

    def test_blob_too_large_by_decoded_length(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/code":
                return json_response({"items": [code_entry(fragments=["empty"])]})
            # 无 size 字段 → 走解码后长度判定
            return json_response(blob_payload("y" * (gh.MAX_BLOB_BYTES + 1), omit_size=True))

        result = run_hunt(handler, queries=["q"], per_page=10)
        assert result["counts"]["blob_too_large"] >= 1
        assert result["counts"]["blobs_decoded"] == 0


# ---------------------------------------------------------------------------
# 契约六:commit 泳道
# ---------------------------------------------------------------------------


class TestCommitLane:
    def test_single_page_and_lane_plus_artifact_items(self):
        recorder = Recorder()

        async def handler(request: httpx.Request) -> httpx.Response:
            recorder.record(request)
            if request.url.path == "/search/commits":
                return json_response({"items": [commit_entry(message=f"chore: remove leaked {KEY_CURSOR}")]})
            if request.url.path == "/repos/octo/demo/commits/c0ffee":
                return json_response({"sha": "c0ffee", "commit": {"message": f"chore: remove leaked {KEY_CURSOR}"}})
            return json_response({})

        result = run_hunt(handler, queries=["CURSOR_API_KEY"])
        # 仅第 1 页(结果再少也不会翻页——commit 泳道硬编码只搜一页)
        assert len(recorder.paths("/search/commits")) == 1
        cursor_items = [item for item in result["items"] if item["provider"] == "cursor"]
        assert len(cursor_items) == 2  # 泳道扫描 + 工件二次加工(commit message 再取一次)
        assert all(item["source_type"] == "commit_message" for item in cursor_items)
        assert all(item["commit_sha"] == "c0ffee" for item in cursor_items)

    def test_publicity_requires_private_false(self):
        entries = [
            commit_entry(sha="ok1", message=f"fix {KEY_XAI}"),
            commit_entry(sha="pk1", private=True, message=f"fix {KEY_XAI}"),
            commit_entry(sha="pk2", omit_private=True, message=f"fix {KEY_XAI}"),  # private 缺失
            commit_entry(sha="pk3", omit_private=True, message=f"fix {KEY_XAI}"),  # visibility 在 commit 泳道不算数
        ]
        entries[3]["repository"]["visibility"] = "public"

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/commits":
                return json_response({"items": entries})
            return json_response({"commit": {"message": ""}})

        result = run_hunt(handler, queries=["XAI_API_KEY"])
        assert result["counts"]["commit_items"] == 4
        assert result["counts"]["commit_private_skipped"] == 3
        assert {item["commit_sha"] for item in result["items"]} == {"ok1"}

    def test_budget_rotation_across_runs(self):
        pool = [f"Q{i:02d}" for i in range(15)]
        recorder = Recorder()

        async def handler(request: httpx.Request) -> httpx.Response:
            recorder.record(request)
            return json_response({"items": []})

        result1 = run_hunt(handler, queries=pool)
        assert [call[2]["q"] for call in recorder.paths("/search/commits")] == pool[:12]
        assert result1["checkpoint"]["commit_rotation"] == 12

        recorder.calls.clear()
        result2 = run_hunt(handler, queries=pool, checkpoint=result1["checkpoint"])
        # 第二轮从游标 12 起:余下 3 条 + 环形回绕补 9 条(预算仍是整 12 条)
        assert [call[2]["q"] for call in recorder.paths("/search/commits")] == pool[12:] + pool[:9]
        assert result2["checkpoint"]["commit_rotation"] == 9

    def test_small_pool_takes_all(self):
        recorder = Recorder()

        async def handler(request: httpx.Request) -> httpx.Response:
            recorder.record(request)
            return json_response({"items": []})

        result = run_hunt(handler, queries=["A", "B"])
        assert [call[2]["q"] for call in recorder.paths("/search/commits")] == ["A", "B"]
        assert result["checkpoint"]["commit_rotation"] == 0

    def test_query_error_continues_to_next(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/commits":
                if request.url.params["q"] == "bad one":
                    return httpx.Response(500, json={"message": "boom"})
                return json_response({"items": [commit_entry(sha="ok9", message=f"leak {KEY_XAI}")]})
            return json_response({"commit": {"message": ""}})

        result = run_hunt(handler, queries=["bad one", "good one"])
        assert result["counts"]["commit_queries"] == 2
        assert [error["query"] for error in result["errors"]] == ["bad one"]
        assert all(error["lane"] == "commit_message" for error in result["errors"])
        assert any(item["commit_sha"] == "ok9" for item in result["items"])


# ---------------------------------------------------------------------------
# 契约七:工件二次加工
# ---------------------------------------------------------------------------


class TestArtifactProcessing:
    def test_transient_retry_then_terminal(self):
        calls = {"count": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            if calls["count"] <= 2:
                return httpx.Response(500, json={"message": "boom"})
            return json_response({"commit": {"message": f"leak {KEY_XAI}"}})

        work = gh.ArtifactWork(repo_full_name="o/r", sha="dead10", kind="commit", html_url="https://github.com/o/r/commit/dead10")
        outcome = run_artifacts(handler, [work])
        assert outcome["counts"]["artifact_terminal"] == 1
        assert work.status == "terminal"
        assert work.attempts == 3  # 首试 + 2 次重试后成功
        assert any(item["provider"] == "xai" for item in outcome["items"])

    def test_retries_exhausted_is_transient(self):
        calls = {"count": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(500, json={"message": "boom"})

        work = gh.ArtifactWork(repo_full_name="o/r", sha="err01", kind="blob")
        outcome = run_artifacts(handler, [work])
        assert outcome["counts"]["artifact_transient"] == 1
        assert work.status == "transient"
        assert work.attempts == 6  # 首试 + 至多 5 次重试
        assert calls["count"] == 6

    def test_too_large_no_retry(self):
        calls = {"count": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return json_response(blob_payload("z" * 32, size=gh.MAX_BLOB_BYTES + 100))

        work = gh.ArtifactWork(repo_full_name="o/r", sha="big01", kind="blob")
        outcome = run_artifacts(handler, [work])
        assert outcome["counts"]["artifact_too_large"] == 1
        assert work.status == "artifact_too_large"
        assert calls["count"] == 1  # 超限不重试

    def test_batch_limit_two_hundred(self):
        calls = {"count": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return json_response(blob_payload(""))

        works = [gh.ArtifactWork(repo_full_name="o/r", sha=f"s{i:03d}", kind="blob") for i in range(250)]
        outcome = run_artifacts(handler, works)
        assert outcome["counts"]["artifact_queued"] == 250
        assert outcome["counts"]["artifact_batch"] == 200  # 领取上限
        assert calls["count"] == 200

    def test_concurrency_capped_at_eight(self):
        state = {"inflight": 0, "max": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            state["inflight"] += 1
            state["max"] = max(state["max"], state["inflight"])
            await asyncio.sleep(0.01)
            state["inflight"] -= 1
            return json_response(blob_payload(""))

        works = [gh.ArtifactWork(repo_full_name="o/r", sha=f"c{i:03d}", kind="blob") for i in range(40)]
        outcome = run_artifacts(handler, works)
        assert outcome["counts"]["artifact_terminal"] == 40
        assert state["max"] <= 8
        assert state["max"] >= 2  # 确认真并发(否则上限断言无意义)

    def test_blob_diff_three_side_enhancement(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return json_response(blob_payload(DIFF_TEXT))

        work = gh.ArtifactWork(repo_full_name="o/r", sha="diff1", kind="blob", lane="code_snapshot")
        outcome = run_artifacts(handler, [work])
        sides = {item["change_side"] for item in outcome["items"]}
        assert sides == {"added", "removed"}
        added_item = next(item for item in outcome["items"] if item["change_side"] == "added")
        assert (added_item["line_start"], added_item["line_end"]) == (11, 12)
        removed_item = next(item for item in outcome["items"] if item["change_side"] == "removed")
        assert (removed_item["line_start"], removed_item["line_end"]) == (11, 11)


# ---------------------------------------------------------------------------
# 契约八:主入口(Q9 掩码-only + apiurl 前缀归因 + 缺省查询池)
# ---------------------------------------------------------------------------


class TestHuntEndToEnd:
    def test_items_masked_and_apiurl_from_prefix_map(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/code":
                return json_response(
                    {"items": [code_entry(sha="e1", fragments=["no hit"], path="config/.env")]}
                )
            if request.url.path.startswith("/repos/octo/demo/git/blobs/"):
                # 同一 blob 两键:兄弟键也不得全文进任何 item 的 content
                return json_response(blob_payload(f'a = "{KEY_XAI}"\nb = "{KEY_CURSOR}"\n'))
            if request.url.path == "/search/commits":
                return json_response({"items": [commit_entry(sha="c1", message=f"leak {KEY_KIRO}")]})
            if request.url.path == "/repos/octo/demo/commits/c1":
                return json_response({"commit": {"message": f"leak {KEY_KIRO}"}})
            return json_response({})

        result = run_hunt(handler, queries=["sk- filename:.env", "KIRO_API_KEY"])
        assert result["items"]
        for item in result["items"]:
            for key in ALL_FIXTURE_KEYS:
                assert findings_mod.find_full_key_leak(item, key) == [], f"全文密钥泄漏进 item:{item}"
        # apiurl 只走前缀静态映射(命中项 URL 不当 endpoint 用)
        xai_items = [item for item in result["items"] if item["provider"] == "xai"]
        assert xai_items and all(item["apiurl"] == "https://api.x.ai/v1" for item in xai_items)
        kiro_items = [item for item in result["items"] if item["provider"] == "kiro"]
        assert kiro_items and all(item["apiurl"] == "https://app.kiro.dev" for item in kiro_items)
        assert set(result["checkpoint"]) == {"code_pages", "commit_rotation"}
        assert result["counts"]["items"] == len(result["items"])

    def test_default_queries_from_packs_library(self):
        recorder = Recorder()

        async def handler(request: httpx.Request) -> httpx.Response:
            recorder.record(request)
            return json_response({"items": []})

        result = run_hunt(handler)  # queries=None → 发现层数据文件查询池
        packs_mod = gh._sibling("packs")
        pool = packs_mod.github_query_pool(packs_mod.load_packs())
        assert len(pool) == 34  # 20 包 github_terms 去重(R3 数据文件)
        # code 泳道跑全池;commit 泳道取预算 12 条(Q8)
        assert [call[2]["q"] for call in recorder.paths("/search/code")] == pool
        assert [call[2]["q"] for call in recorder.paths("/search/commits")] == pool[:12]
        assert result["checkpoint"]["commit_rotation"] == 12
        assert result["errors"] == []

    def test_empty_query_pool_is_quiet(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("空查询池不应发出任何请求")

        result = run_hunt(handler, queries=[])
        assert result["items"] == []
        assert result["errors"] == []
        assert result["counts"]["items"] == 0

    def test_missing_tokens_raises(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("无 token 不应出网")

        with pytest.raises(gh.GithubHuntError) as excinfo:
            run_hunt(handler, tokens=[], queries=["q"])
        assert excinfo.value.code == "tokens_missing"

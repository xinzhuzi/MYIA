"""Tests for 10-03-messaging-w2-platforms 步骤 3 — 企微适配器。

覆盖(prd 验收 1/3 的企微面):token 生命周期(缓存命中、过期重取、
40001/42001 逐出重试一次、缓存文件原子落盘/损坏退化)、2048 字节分块
(行边界优先、多字节安全)、touser 寻址(定向/legacy/corpid:userid 复合)、
错误分类(40001 重取后仍失败 → forbidden;userid 失效族 → not_found;
45009/-1/超时 → 瞬态)。

蓝本对照:Hermes ``plugins/platforms/wecom/callback_adapter.py`` 的
``_send_text``/``_get_access_token``/``_refresh_access_token``
(NousResearch/Hermes-Agent,MIT)——此处测 MYIA 重写(token 缓存落点改为
数据根文件,design D2)。

No pytest-asyncio in dev deps: async calls run through ``asyncio.run``。
All network I/O goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from myia.push import SendContext, WecomChannel
from myia.push.base import PushSendError
from myia.push.delivery import classify_dead_error
from myia.push.directory import ChannelDirectory, DirectoryDiscoverUnsupported
from myia.push.targets import RESOLVED_DIRECT, ChannelTarget, resolve_target
from myia.push.wecom import MAX_CONTENT_BYTES, split_utf8_chunks

CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
NOW = 1_762_000_000.0


def _capture(request: httpx.Request) -> dict:
    return {
        "url": str(request.url),
        "params": dict(request.url.params),
        "body": json.loads(request.content.decode("utf-8")) if request.content else None,
    }


class _Router:
    """mock 路由:gettoken 固定应答;message/send 按脚本顺序出应答。"""

    def __init__(self, *, token: str = "tok-1", send_responses: list[httpx.Response] | None = None):
        self.calls: list[dict] = []
        self.token = token
        self.token_responses: list[httpx.Response] = []
        self.send_responses = list(send_responses or [])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(_capture(request))
        url = str(request.url)
        if "gettoken" in url:
            if self.token_responses:
                return self.token_responses.pop(0)
            return httpx.Response(
                200, json={"errcode": 0, "access_token": self.token, "expires_in": 7200}
            )
        if self.send_responses:
            return self.send_responses.pop(0)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "m1"})


def _channel(
    router: _Router,
    *,
    target: str | None = "env:MYIA_TEST_WECOM_TUSER",
    token_cache_path: str | Path | None = None,
    clock=lambda: NOW,
    **kwargs: Any,
) -> WecomChannel:
    return WecomChannel(
        target=target,
        corpid_ref="env:MYIA_TEST_WECOM_CORPID",
        corpsecret_ref="env:MYIA_TEST_WECOM_SECRET",
        agentid_ref="env:MYIA_TEST_WECOM_AGENTID",
        token_cache_path=token_cache_path,
        client=httpx.AsyncClient(transport=httpx.MockTransport(router.handler)),
        clock=clock,
        **kwargs,
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target(chat_id: str) -> ChannelTarget:
    return ChannelTarget(platform="wecom", chat_id=chat_id, resolved_from=RESOLVED_DIRECT)


@pytest.fixture()
def creds_env(monkeypatch):
    monkeypatch.setenv("MYIA_TEST_WECOM_CORPID", "ww1234corp")
    monkeypatch.setenv("MYIA_TEST_WECOM_SECRET", "secret-xyz")
    monkeypatch.setenv("MYIA_TEST_WECOM_AGENTID", "1000002")
    monkeypatch.setenv("MYIA_TEST_WECOM_TUSER", "ZhangSan")


def _token_calls(router: _Router) -> list[dict]:
    return [c for c in router.calls if "gettoken" in c["url"]]


def _send_calls(router: _Router) -> list[dict]:
    return [c for c in router.calls if "message/send" in c["url"]]


# ---------------------------------------------------------------------------
# 发送形态 + touser 寻址
# ---------------------------------------------------------------------------


class TestSendShape:
    def test_posts_text_payload_to_touser(self, creds_env):
        """蓝本形态:touser/msgtype=text/agentid/text.content/safe=0。"""
        router = _Router()
        channel = _channel(router)

        _run(channel.send([{"title": "羊毛", "url": "https://x/1"}], CONTEXT))

        send = _send_calls(router)[0]
        assert send["params"]["access_token"] == "tok-1"
        assert send["body"] == {
            "touser": "ZhangSan",
            "msgtype": "text",
            "agentid": 1000002,
            "text": {"content": send["body"]["text"]["content"]},
            "safe": 0,
        }
        assert "羊毛" in send["body"]["text"]["content"]

    def test_context_target_userid_overrides_legacy(self, creds_env):
        router = _Router()
        channel = _channel(router)

        _run(channel.send([{"title": "t"}], replace(CONTEXT, target=_target("LiSi"))))

        assert _send_calls(router)[0]["body"]["touser"] == "LiSi"

    def test_corp_userid_compound_form_keeps_userid(self, creds_env):
        """蓝本同款:``corpid:userid`` 复合形态取 userid 段。"""
        router = _Router()
        channel = _channel(router, target="env:MYIA_TEST_WECOM_TUSER")

        _run(
            channel.send(
                [{"title": "t"}],
                replace(CONTEXT, target=_target("ww1234corp:WangWu")),
            )
        )

        assert _send_calls(router)[0]["body"]["touser"] == "WangWu"

    def test_no_target_and_no_context_target_fails_fast(self, creds_env):
        router = _Router()
        channel = _channel(router, target=None)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "missing_target"
        assert router.calls == []

    def test_missing_credential_is_structured_error(self, monkeypatch, creds_env):
        monkeypatch.delenv("MYIA_TEST_WECOM_SECRET", raising=False)
        router = _Router()
        channel = _channel(router)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "env_var_missing"

    def test_non_numeric_agentid_rejected(self, monkeypatch, creds_env):
        monkeypatch.setenv("MYIA_TEST_WECOM_AGENTID", "not-a-number")
        router = _Router()
        channel = _channel(router)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_credential_ref"


# ---------------------------------------------------------------------------
# 2048 字节分块
# ---------------------------------------------------------------------------


class TestChunking:
    def test_split_prefers_line_boundaries(self):
        text = "\n".join("行" * 10 for _ in range(300))  # 每行 30B,总 ~9KB
        chunks = split_utf8_chunks(text)

        assert all(len(c.encode("utf-8")) <= MAX_CONTENT_BYTES for c in chunks)
        assert "".join(c + "\n" for c in chunks).replace("\n", "").count("行") == (
            text.count("行")
        )

    def test_split_hardcuts_single_long_line_multibyte_safe(self):
        text = "字" * 2000  # 6000B 单行:无行边界可切
        chunks = split_utf8_chunks(text)

        assert all(len(c.encode("utf-8")) <= MAX_CONTENT_BYTES for c in chunks)
        # 多字节字符不跨块:拼回(块间无换行插入)与原文一致
        assert "".join(chunks) == text

    def test_short_text_single_chunk(self):
        assert split_utf8_chunks("hello") == ["hello"]
        assert split_utf8_chunks("") == []

    def test_long_send_posts_multiple_chunks_in_order(self, creds_env):
        router = _Router()
        channel = _channel(router)
        items = [{"title": "题" * 1500, "url": f"https://x/{i}"} for i in range(4)]

        _run(channel.send(items, CONTEXT))

        sends = _send_calls(router)
        assert len(sends) > 1
        assert all(len(s["body"]["text"]["content"].encode("utf-8")) <= MAX_CONTENT_BYTES for s in sends)
        # 块序即发送序:首块是标题行,末块带 3 号条目 URL(0-2 号在中途块)
        assert sends[0]["body"]["text"]["content"].startswith("📡")
        assert "https://x/3" in sends[-1]["body"]["text"]["content"]
        assert any("https://x/0" in s["body"]["text"]["content"] for s in sends)


# ---------------------------------------------------------------------------
# token 生命周期(prd 验收 3)
# ---------------------------------------------------------------------------


class TestTokenLifecycle:
    def test_cache_file_roundtrip_second_channel_hits_cache(self, creds_env, tmp_path):
        """跨实例缓存:第一只现取落盘,第二只(新实例,同数据根)零 gettoken。"""
        cache = tmp_path / "wecom_token_cache.json"
        router1 = _Router(token="tok-A")
        _run(_channel(router1, token_cache_path=cache).send([{"title": "t"}], CONTEXT))
        assert len(_token_calls(router1)) == 1
        assert json.loads(cache.read_text(encoding="utf-8"))["token"] == "tok-A"

        router2 = _Router(token="tok-B")
        _run(_channel(router2, token_cache_path=cache).send([{"title": "t"}], CONTEXT))
        assert _token_calls(router2) == []  # 缓存命中:不再 gettoken
        assert _send_calls(router2)[0]["params"]["access_token"] == "tok-A"

    def test_expired_cache_refetches(self, creds_env, tmp_path):
        """缓存超窗(> expires_in - 60s 余量)→ 重取。"""
        cache = tmp_path / "wecom_token_cache.json"
        router1 = _Router(token="tok-old")
        _run(_channel(router1, token_cache_path=cache).send([{"title": "t"}], CONTEXT))

        # 同一数据根、时间前进 7200s(超出余量):现取 tok-new
        router2 = _Router(token="tok-new")
        _run(
            _channel(router2, token_cache_path=cache, clock=lambda: NOW + 7200).send(
                [{"title": "t"}], CONTEXT
            )
        )
        assert len(_token_calls(router2)) == 1
        assert _send_calls(router2)[0]["params"]["access_token"] == "tok-new"

    def test_within_margin_not_refetched(self, creds_env, tmp_path):
        """剩余寿命 > 60s 的缓存照用(蓝本 now+60 判据,不吃满 7200s)。"""
        cache = tmp_path / "wecom_token_cache.json"
        _run(_channel(_Router(token="tok-C"), token_cache_path=cache).send([{"title": "t"}], CONTEXT))

        router = _Router(token="tok-D")
        _run(
            _channel(router, token_cache_path=cache, clock=lambda: NOW + 7100).send(
                [{"title": "t"}], CONTEXT
            )
        )
        assert _token_calls(router) == []
        assert _send_calls(router)[0]["params"]["access_token"] == "tok-C"

    def test_corrupt_cache_degrades_to_refetch(self, creds_env, tmp_path):
        """缓存文件损坏 → 静默重取(绝不阻塞发送)。"""
        cache = tmp_path / "wecom_token_cache.json"
        cache.write_text("{not json", encoding="utf-8")
        router = _Router(token="tok-fresh")

        _run(_channel(router, token_cache_path=cache).send([{"title": "t"}], CONTEXT))

        assert len(_token_calls(router)) == 1
        assert _send_calls(router)[0]["params"]["access_token"] == "tok-fresh"

    def test_gettoken_error_is_structured(self, creds_env):
        router = _Router()
        router.token_responses.append(
            httpx.Response(200, json={"errcode": 40001, "errmsg": "不合法的secret参数"})
        )
        channel = _channel(router)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "errcode=40001" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"


class TestTokenRefreshRetry:
    def test_42001_evicts_and_retries_once(self, creds_env, tmp_path):
        """42001(token 过期):逐出缓存 → gettoken 现取 → 重发成功(蓝本两轮)。"""
        cache = tmp_path / "wecom_token_cache.json"
        router1 = _Router(token="tok-stale")
        _run(_channel(router1, token_cache_path=cache).send([{"title": "t"}], CONTEXT))

        router = _Router(token="tok-stale")  # 缓存读出的旧 token
        # 第一次 send:42001;gettoken 重取给新 token;第二次 send:成功。
        router.token = "tok-fresh"
        router.send_responses = [
            httpx.Response(200, json={"errcode": 42001, "errmsg": "access_token已过期"}),
            httpx.Response(200, json={"errcode": 0, "errmsg": "ok"}),
        ]
        _run(_channel(router, token_cache_path=cache).send([{"title": "t"}], CONTEXT))

        tokens_used = [s["params"]["access_token"] for s in _send_calls(router)]
        assert tokens_used == ["tok-stale", "tok-fresh"]
        assert len(_token_calls(router)) == 1
        # 逐出后新 token 已回写缓存(下一只实例零 gettoken)
        assert json.loads(cache.read_text(encoding="utf-8"))["token"] == "tok-fresh"

    def test_40001_after_retry_surfaces_forbidden(self, creds_env):
        """40001 重取后仍失败:浮为 wecom_api_error,死信分类 forbidden。"""
        router = _Router(token="tok-x")
        router.token_responses = [
            httpx.Response(200, json={"errcode": 0, "access_token": "tok-x2", "expires_in": 7200}),
        ]
        router.send_responses = [
            httpx.Response(200, json={"errcode": 40001, "errmsg": "不合法的secret参数"}),
            httpx.Response(200, json={"errcode": 40001, "errmsg": "不合法的secret参数"}),
        ]
        channel = _channel(router)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert "errcode=40001" in str(excinfo.value)
        assert classify_dead_error(excinfo.value) == "forbidden"
        assert len(_send_calls(router)) == 2  # 恰好两轮,无第三轮

    def test_non_retryable_errcode_fails_without_refetch(self, creds_env):
        router = _Router(token="tok-x")
        router.send_responses = [
            httpx.Response(200, json={"errcode": 81013, "errmsg": "UserID、部门ID、标签ID全部非法或无权限"}),
        ]
        channel = _channel(router)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) == "forbidden"
        assert len(_send_calls(router)) == 1
        assert len(_token_calls(router)) == 1  # 不触发 token 重取


# ---------------------------------------------------------------------------
# 错误分类(prd D3,官方错误码表核订)
# ---------------------------------------------------------------------------


class TestErrorClassification:
    @pytest.mark.parametrize(
        ("errcode", "kind"),
        [
            (40003, "not_found"),  # 无效的 UserID
            (60111, "not_found"),  # UserID 不存在
            (46004, "not_found"),  # 指定的用户不存在
            (60020, "forbidden"),  # 不安全的访问 IP
            (60021, "forbidden"),  # userid 不在应用可见范围内
        ],
    )
    def test_hard_errcodes_classify(self, creds_env, errcode, kind):
        router = _Router(token="tok-x")
        router.send_responses = [
            httpx.Response(200, json={"errcode": errcode, "errmsg": "boom"})
        ]
        channel = _channel(router)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) == kind

    @pytest.mark.parametrize("errcode", [45009, -1])  # 接口调用超过限制 / 系统繁忙
    def test_transient_errcodes_do_not_mark_dead(self, creds_env, errcode):
        router = _Router(token="tok-x")
        router.send_responses = [
            httpx.Response(200, json={"errcode": errcode, "errmsg": "busy"})
        ]
        channel = _channel(router)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert classify_dead_error(excinfo.value) is None

    def test_transport_error_is_transient(self, creds_env):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        channel = WecomChannel(
            target="env:MYIA_TEST_WECOM_TUSER",
            corpid_ref="env:MYIA_TEST_WECOM_CORPID",
            corpsecret_ref="env:MYIA_TEST_WECOM_SECRET",
            agentid_ref="env:MYIA_TEST_WECOM_AGENTID",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            clock=lambda: NOW,
        )

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "http_error"
        assert classify_dead_error(excinfo.value) is None

    def test_non_json_response_is_invalid(self, creds_env):
        router = _Router()
        router.send_responses = [httpx.Response(200, text="not-json")]
        channel = _channel(router)

        with pytest.raises(PushSendError) as excinfo:
            _run(channel.send([{"title": "t"}], CONTEXT))
        assert excinfo.value.code == "invalid_response"


# ---------------------------------------------------------------------------
# 寻址:直达 + 别名;目录语义
# ---------------------------------------------------------------------------


class TestAddressingAndDirectory:
    def test_direct_ref_parse(self):
        for ref in ["ZhangSan", "u_01", "Ab-999", "a" * 64]:
            target = WecomChannel.parse_direct_ref(ref)
            assert target is not None
            assert (target.platform, target.chat_id, target.resolved_from) == (
                "wecom",
                ref,
                RESOLVED_DIRECT,
            )
        for ref in ["张三", "has space", "", "a" * 65]:
            assert WecomChannel.parse_direct_ref(ref) is None

    def test_resolve_via_registry_bypasses_directory(self, tmp_path):
        target = resolve_target("wecom:ZhangSan", ChannelDirectory(tmp_path))

        assert (target.platform, target.chat_id) == ("wecom", "ZhangSan")

    def test_alias_registered_userid_resolves_by_name(self, tmp_path):
        directory = ChannelDirectory(tmp_path)
        directory.set_alias("wecom", "ZhangSan", "张老板")

        target = resolve_target("wecom:张老板", directory)

        assert (target.chat_id, target.resolved_from) == ("ZhangSan", "directory_name")

    def test_discover_directory_raises_unsupported(self):
        with pytest.raises(DirectoryDiscoverUnsupported) as excinfo:
            _run(WecomChannel().discover_directory())
        assert "无自动发现" in str(excinfo.value)

    def test_directory_refresh_skips_unsupported_quietly(self, tmp_path):
        directory = ChannelDirectory(tmp_path)

        counts = _run(directory.refresh({"wecom": WecomChannel()}, now=100.0))

        assert counts == {}

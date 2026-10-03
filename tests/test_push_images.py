"""Tests for 推送带图(immediate 组装层)— PRD 10-03-vision-v2 推送侧。

覆盖:base ``item_images``/``clip_text`` 契约;TG ``sendPhoto`` multipart
形状(URL/content-type/caption/文件字节)、caption 1024 截断、图缺失与
发送失败回退纯文本、digest 不带图、模板路径同增强;飞书上传成功 → 卡片
``img`` 元素(经 ``im/v1/images`` multipart 换 image_key),上传失败/图
缺失 → 图析摘要文本卡,digest 卡逐字节不变。全部 httpx.MockTransport,
零真实网络(任务纪律);真实收发由主人实证(openIssues)。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from shishi.push import PushSendError, SendContext, TelegramChannel, send_immediate
from shishi.push.base import ItemImages, clip_text, item_images
from shishi.push.feishu_card import (
    CAPTION_EXCERPT_CHARS,
    IMAGES_API_URL,
    FeishuCardChannel,
    build_card,
)
from shishi.push.telegram import CAPTION_LIMIT, build_photo_caption

TIMEZONE = timezone(timedelta(hours=8))
DIGEST_CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="digest")
IMMEDIATE_CONTEXT = SendContext(slot="am", date="2026-10-03", category="羊毛", kind="immediate")
#: 伪 PNG 字节(mock 端点不校验内容;后缀驱动 mimetypes → image/png)。
FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes-for-mock"


@pytest.fixture(autouse=True)
def _channel_credentials(monkeypatch):
    """通道 target 走 env: 引用解析(安全基线:target 只收凭据引用)。"""
    monkeypatch.setenv("MYIA_TEST_TG_CHAT", "424242")
    monkeypatch.setenv("MYIA_TEST_FEISHU_CHAT", "oc_test")


def _image(tmp_path, name: str = "pic.png") -> str:
    path = tmp_path / name
    path.write_bytes(FAKE_PNG)
    return str(path)


def _item(image_path: str, *, caption: str = "图析摘要:一张示意图", missing: str | None = None) -> dict:
    files = [image_path] + ([missing] if missing else [])
    return {
        "title": "公开示例羊毛",
        "url": "https://example.com/a",
        "category": "freebie",
        "metadata": {"image_files": files, "image_caption": caption},
    }


class _Recorder:
    """MockTransport 双端点录音:JSON 体与 multipart 原始字节都留档。"""

    def __init__(self, calls: list[dict], photo_ok: bool = True, upload_ok: bool = True) -> None:
        self.calls = calls
        self.photo_ok = photo_ok
        self.upload_ok = upload_ok

    def handler(self, request: httpx.Request) -> httpx.Response:
        try:
            parsed = json.loads(request.content.decode("utf-8"))
        except ValueError:
            parsed = None
        self.calls.append(
            {
                "url": str(request.url),
                "content_type": request.headers.get("content-type", ""),
                "body": request.content,
                "json": parsed,
            }
        )
        if request.url.path.endswith("sendPhoto"):
            if not self.photo_ok:
                return httpx.Response(
                    400, json={"ok": False, "error_code": 400, "description": "Bad Request: file too big"}
                )
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
        if str(request.url).startswith(IMAGES_API_URL):
            if not self.upload_ok:
                return httpx.Response(200, json={"code": 99991672, "msg": "no im:resource scope"})
            return httpx.Response(
                200, json={"code": 0, "msg": "success", "data": {"image_key": "img_v2_mock_key"}}
            )
        if request.url.host == "open.feishu.cn":
            return httpx.Response(200, json={"code": 0, "msg": "success", "data": {}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


def _tg_channel(recorder: _Recorder) -> TelegramChannel:
    return TelegramChannel(
        token="test-token",
        target="env:MYIA_TEST_TG_CHAT",
        client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler)),
    )


def _feishu_channel(recorder: _Recorder) -> FeishuCardChannel:
    return FeishuCardChannel(
        token="t",
        target="env:MYIA_TEST_FEISHU_CHAT",
        client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler)),
    )


def _sent_card(call: dict) -> dict:
    return json.loads(call["json"]["content"])


# ---------------------------------------------------------------------------
# base:clip_text / item_images 契约
# ---------------------------------------------------------------------------


def test_clip_text_short_passthrough_and_ellipsis_counts_into_limit():
    assert clip_text("abc", 10) == "abc"
    clipped = clip_text("a" * 20, 10)
    assert len(clipped) == 10 and clipped.endswith("…")  # 省略号计入上限


def test_clip_text_limit_below_one_degrades_to_ellipsis():
    assert clip_text("abcdef", 0) == "…"


def test_item_images_reads_metadata_merges_and_filters_missing_files(tmp_path):
    existing = _image(tmp_path)
    missing = str(tmp_path / "gone.png")
    info = item_images(_item(existing, missing=missing))
    assert info == ItemImages(paths=(existing,), declared=2, caption="图析摘要:一张示意图")


def test_item_images_supports_object_items_with_metadata_attr(tmp_path):
    existing = _image(tmp_path, "obj.png")
    item = SimpleNamespace(title="t", metadata={"image_files": [existing], "image_caption": " 摘要 "})
    info = item_images(item)
    assert info is not None
    assert info.paths == (existing,) and info.declared == 1 and info.caption == "摘要"


@pytest.mark.parametrize(
    "metadata",
    [
        None,  # 无 metadata
        {"image_caption": "只有摘要没有文件"},  # 无 image_files:不构成图片条目
        {"image_files": "not-a-list"},
        {"image_files": []},
        {"image_files": [123, None, "  "]},  # 无一有效字符串条目
    ],
)
def test_item_images_returns_none_for_absent_or_malformed(metadata):
    item = {"title": "t", "metadata": metadata} if metadata is not None else {"title": "t"}
    assert item_images(item) is None


def test_item_images_non_string_caption_degrades_to_empty(tmp_path):
    existing = _image(tmp_path)
    info = item_images({"title": "t", "metadata": {"image_files": [existing], "image_caption": 42}})
    assert info is not None and info.caption == ""


# ---------------------------------------------------------------------------
# telegram:build_photo_caption(纯函数,1024 上限)
# ---------------------------------------------------------------------------


def test_build_photo_caption_title_only_without_excerpt():
    assert build_photo_caption({"title": "标题"}, "") == "标题"
    assert build_photo_caption({}, "") == "(无标题)"


def test_build_photo_caption_combines_title_and_excerpt_when_fits():
    caption = build_photo_caption({"title": "标题"}, "摘要内容")
    assert caption == "标题\n摘要内容"


def test_build_photo_caption_clips_excerpt_to_caption_limit():
    title = "十个字标题十个字标题"
    excerpt = "析" * 3000
    caption = build_photo_caption({"title": title}, excerpt)
    assert len(caption) <= CAPTION_LIMIT
    assert caption.startswith(f"{title}\n") and caption.endswith("…")


def test_build_photo_caption_oversized_title_keeps_title_only():
    caption = build_photo_caption({"title": "题" * 2000}, "摘要")
    assert len(caption) == CAPTION_LIMIT and caption.endswith("…")
    assert caption.startswith("题")


# ---------------------------------------------------------------------------
# telegram:sendPhoto multipart 形状 + immediate 路由
# ---------------------------------------------------------------------------


def test_telegram_immediate_sends_photo_first_then_message(tmp_path):
    image_path = _image(tmp_path)
    calls: list[dict] = []
    recorder = _Recorder(calls)
    channel = _tg_channel(recorder)

    asyncio.run(channel.send([_item(image_path)], IMMEDIATE_CONTEXT))

    assert len(calls) == 2
    photo = calls[0]
    assert photo["url"] == "https://api.telegram.org/bottest-token/sendPhoto"
    assert photo["content_type"].startswith("multipart/form-data")
    body = photo["body"]
    assert b'name="chat_id"' in body and b"424242" in body
    assert b'name="caption"' in body and "公开示例羊毛\n图析摘要:一张示意图".encode() in body
    assert b'name="photo"' in body and b'filename="pic.png"' in body
    assert b"image/png" in body and FAKE_PNG in body
    message = calls[1]
    assert message["url"].endswith("/sendMessage")
    assert message["json"]["parse_mode"] == "HTML"  # 正文逐字节走原路径


def test_telegram_immediate_via_send_immediate_dispatch(tmp_path):
    """打通 digest.send_immediate:kind=immediate 上下文经派发层原样生效。"""
    image_path = _image(tmp_path)
    calls: list[dict] = []
    recorder = _Recorder(calls)
    channel = _tg_channel(recorder)

    reports = asyncio.run(
        send_immediate(
            [_item(image_path)], channels=[channel], tz=TIMEZONE,
            now=datetime(2026, 10, 3, 9, 0, tzinfo=TIMEZONE), category="羊毛",
        )
    )
    assert [r.ok for r in reports] == [True]
    assert calls[0]["url"].endswith("/sendPhoto") and calls[1]["url"].endswith("/sendMessage")


def test_telegram_immediate_missing_image_falls_back_to_text_only(tmp_path, caplog):
    calls: list[dict] = []
    recorder = _Recorder(calls)
    channel = _tg_channel(recorder)
    item = _item(str(tmp_path / "missing.png"))  # 声明 1 图,本机不存在

    asyncio.run(channel.send([item], IMMEDIATE_CONTEXT))

    assert len(calls) == 1 and calls[0]["url"].endswith("/sendMessage")
    assert any("回退纯文本" in rec.message for rec in caplog.records)


def test_telegram_photo_api_failure_still_sends_text_message(tmp_path):
    image_path = _image(tmp_path)
    calls: list[dict] = []
    recorder = _Recorder(calls, photo_ok=False)
    channel = _tg_channel(recorder)

    asyncio.run(channel.send([_item(image_path)], IMMEDIATE_CONTEXT))

    assert [call["url"].rsplit("/", 1)[-1] for call in calls] == ["sendPhoto", "sendMessage"]


def test_telegram_digest_with_images_never_sends_photo(tmp_path):
    """digest 批量不带图(PRD 定案):有图 metadata 也只走 sendMessage。"""
    image_path = _image(tmp_path)
    calls: list[dict] = []
    recorder = _Recorder(calls)
    channel = _tg_channel(recorder)

    asyncio.run(channel.send([_item(image_path)], DIGEST_CONTEXT))

    assert len(calls) == 1 and calls[0]["url"].endswith("/sendMessage")


def test_telegram_immediate_template_path_also_carries_photo(tmp_path):
    image_path = _image(tmp_path)
    calls: list[dict] = []
    recorder = _Recorder(calls)
    channel = TelegramChannel(
        token="test-token",
        target="env:MYIA_TEST_TG_CHAT",
        template="{{ count }} 条速报",
        client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler)),
    )

    asyncio.run(channel.send([_item(image_path)], IMMEDIATE_CONTEXT))

    assert calls[0]["url"].endswith("/sendPhoto")
    assert calls[1]["json"]["text"] == "1 条速报"
    assert "parse_mode" not in calls[1]["json"]


def test_telegram_send_photo_unreadable_file_raises_structured_error(tmp_path):
    calls: list[dict] = []
    recorder = _Recorder(calls)
    channel = _tg_channel(recorder)
    with pytest.raises(PushSendError) as excinfo:
        asyncio.run(channel.send_photo("424242", str(tmp_path / "nope.png"), "c"))
    assert excinfo.value.code == "photo_read_error"


# ---------------------------------------------------------------------------
# feishu:上传成功 img 元素 / 降级图析摘要卡 / digest 不动
# ---------------------------------------------------------------------------


def test_feishu_immediate_uploads_image_and_adds_img_element(tmp_path):
    image_path = _image(tmp_path)
    calls: list[dict] = []
    recorder = _Recorder(calls)
    channel = _feishu_channel(recorder)

    asyncio.run(channel.send([_item(image_path)], IMMEDIATE_CONTEXT))

    assert len(calls) == 2
    upload = calls[0]
    assert upload["url"].startswith(IMAGES_API_URL)
    assert "receive_id_type" not in upload["url"]  # 上传端点不带消息查询参数
    assert upload["content_type"].startswith("multipart/form-data")
    assert b'name="image_type"' in upload["body"] and b"message" in upload["body"]
    assert b'name="image"' in upload["body"] and b'filename="pic.png"' in upload["body"]
    assert FAKE_PNG in upload["body"]

    card = _sent_card(calls[1])
    elements = card["elements"]
    assert elements[0]["tag"] == "div"  # 条目 div 仍居首
    assert elements[1] == {
        "tag": "img",
        "img_key": "img_v2_mock_key",
        "alt": {"tag": "plain_text", "content": "图析摘要:一张示意图"},
    }
    assert elements[-1]["tag"] == "note"  # 页脚注记仍居末
    assert calls[1]["json"]["receive_id"] == "oc_test"


def test_feishu_immediate_template_path_also_gets_img_element(tmp_path):
    image_path = _image(tmp_path)
    calls: list[dict] = []
    recorder = _Recorder(calls)
    channel = FeishuCardChannel(
        token="t",
        target="env:MYIA_TEST_FEISHU_CHAT",
        template="{{ count }} 条",
        client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler)),
    )

    asyncio.run(channel.send([_item(image_path)], IMMEDIATE_CONTEXT))

    card = _sent_card(calls[-1])
    assert card["elements"][1]["tag"] == "img"
    assert card["elements"][0]["text"]["content"] == "1 条"


def test_feishu_upload_failure_degrades_to_caption_summary_card(tmp_path):
    """上传非零 code(如缺 im:resource 权限)→ 图析摘要行 + 配图注记,不阻投递。"""
    image_path = _image(tmp_path)
    calls: list[dict] = []
    recorder = _Recorder(calls, upload_ok=False)
    channel = _feishu_channel(recorder)

    asyncio.run(channel.send([_item(image_path)], IMMEDIATE_CONTEXT))

    assert len(calls) == 2  # 上传尝试 + 消息投递都发生
    card = _sent_card(calls[1])
    summary = card["elements"][1]
    assert summary["tag"] == "div" and summary["text"]["tag"] == "lark_md"
    assert "　└ 图析: 图析摘要:一张示意图" in summary["text"]["content"]
    assert "　└ [配图 1 张未附]" in summary["text"]["content"]
    assert all(el.get("tag") != "img" for el in card["elements"])


def test_feishu_missing_image_files_skip_upload_and_use_summary_card(tmp_path):
    item = _item(str(tmp_path / "gone.png"), missing=str(tmp_path / "also-gone.png"))
    calls: list[dict] = []
    recorder = _Recorder(calls)
    channel = _feishu_channel(recorder)

    asyncio.run(channel.send([item], IMMEDIATE_CONTEXT))

    assert len(calls) == 1  # 文件全缺:连上传尝试都不发
    content = _sent_card(calls[0])["elements"][1]["text"]["content"]
    assert "配图 2 张未附" in content  # declared 计数含缺失文件


def test_feishu_long_caption_excerpt_is_clipped_in_card(tmp_path):
    image_path = _image(tmp_path)
    item = _item(image_path, caption="析" * 1000)
    calls: list[dict] = []
    recorder = _Recorder(calls)
    channel = _feishu_channel(recorder)

    asyncio.run(channel.send([item], IMMEDIATE_CONTEXT))

    card = _sent_card(calls[1])
    alt = card["elements"][1]["alt"]["content"]
    assert len(alt) <= CAPTION_EXCERPT_CHARS and alt.endswith("…")


def test_feishu_caption_lark_md_chars_escaped_in_summary_card(tmp_path):
    """摘要含 []< 序列:入 lark_md 前字面化(防飞书解析成残缺链接/标签吃掉摘要)。"""
    image_path = _image(tmp_path)
    spicy = "图析:K线 [突破](压力位) <alloc> 完成"
    item = _item(image_path, caption=spicy)
    calls: list[dict] = []
    recorder = _Recorder(calls, upload_ok=False)  # 降级路径才走 lark_md 摘要行
    channel = _feishu_channel(recorder)

    asyncio.run(channel.send([item], IMMEDIATE_CONTEXT))

    content = _sent_card(calls[1])["elements"][1]["text"]["content"]
    assert "图析:K线 \\[突破\\](压力位) \\<alloc> 完成" in content
    assert "[突破](压力位)" not in content  # 原文裸链接形态不得出现
    # 裸标签形态的判定:< 前必须有转义反斜杠(正断言已覆盖,负断言会被
    # \\<alloc> 的子串误伤,故不再另设)


def test_feishu_caption_lark_md_chars_escaped_in_alt_fallback_truncated(tmp_path):
    """无摘要时 alt 回退 title:同样截断到 CAPTION 上限(超长标题不留全文)。"""
    image_path = _image(tmp_path)
    long_title = "题" * 600
    item = _item(image_path, caption="")
    item["title"] = long_title
    calls: list[dict] = []
    recorder = _Recorder(calls)  # 上传成功路径:alt = excerpt(空)回退 title
    channel = _feishu_channel(recorder)

    asyncio.run(channel.send([item], IMMEDIATE_CONTEXT))

    alt = _sent_card(calls[1])["elements"][1]["alt"]["content"]
    assert len(alt) <= CAPTION_EXCERPT_CHARS and alt.endswith("…")


def test_feishu_digest_with_images_card_is_byte_identical(tmp_path):
    """digest 不带图:有图 metadata 时卡片与 build_card 产出逐字节一致。"""
    image_path = _image(tmp_path)
    calls: list[dict] = []
    recorder = _Recorder(calls)
    channel = _feishu_channel(recorder)
    items = [_item(image_path)]

    asyncio.run(channel.send(items, DIGEST_CONTEXT))

    expected = build_card(items, title="📡 羊毛日报 10-03 · 上午摘要")
    assert len(calls) == 1  # 无上传调用
    assert _sent_card(calls[0]) == expected

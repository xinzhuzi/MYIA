"""sources.write 文本手术单测(task 10-03-yaml-toggle-comments)。

写回不再 ``yaml.safe_dump`` 整份重写(那会抹掉全文件注释),而是在原文上
按行搬运 ``sources:`` 条目:被搬条目自身及其前导空行、上方锚点注释随行,
其余行逐字节不动;移出条目的逐字节原文随 ``.disabled.json`` 暂存条目
(``_myia_toggle.raw_block``),enable 插回原位 —— 停用+启用往返 diff 为空。
手术不支持的形态(锚点/别名引用、流式 sources、条目行与解析结果对不齐)
= ``source_write_unsupported`` 结构化拒写、零写入,绝不静默回退 safe_dump。
既有协议用例(test_desktop_sidecar_protocol.py)零改动;本文件只补手术
专属断言:逐字节 diff、锚点随行、引号/顺序保真、CRLF/缩进容错、官方
品类 YAML 全源往返、暂存契约。
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_PATH = REPO_ROOT / "desktop" / "entry.py"
PLUGINS_DIR = REPO_ROOT / "plugins"

_spec = importlib.util.spec_from_file_location("desktop_entry_surgery", ENTRY_PATH)
entry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(entry)


# ---------------------------------------------------------------------------
# 夹具与助手(对齐 test_desktop_sidecar_protocol.py 的 rpc/write_yaml 风格)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch):
    """ambient MYIA_HOME 不得影响任何用例(与既有协议套件同一约定)。"""
    monkeypatch.delenv("MYIA_HOME", raising=False)
    monkeypatch.delenv("MYIA_PLUGIN_DIR", raising=False)


def rpc(*requests: dict) -> list[dict]:
    """整轮 RPC(sources.write 专用):返回应答对象列表。"""
    lines = [json.dumps(req, ensure_ascii=False) for req in requests]
    stdin = io.StringIO("".join(line + "\n" for line in lines))
    out = io.StringIO()
    code = entry.serve(stdin=stdin, stdout=out)
    assert code == 0
    return [json.loads(line) for line in out.getvalue().splitlines()]


def write_yaml(tmp_path: Path, text: str, name: str = "surgery-demo.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def toggle(path: Path, **params) -> dict:
    """直接调 handlers(与 serve 同一入口表),返回 result 或抛 ProtocolError。"""
    return entry._HANDLERS["sources.write"]({"file": str(path), **params})


def lines_of(data: bytes) -> list[str]:
    """逐字节切行(``"".join()`` 还原原文;CRLF 的 ``\\r`` 留在行尾)。"""
    return entry._split_keep_lines(data.decode("utf-8"))


def without_chunk(lines: list[str], chunk: list[str]) -> list[str]:
    """行序中剜掉连续的一段(必须恰好命中一次,否则断言自身失效)。"""
    n = len(chunk)
    hits = [i for i in range(len(lines) - n + 1) if lines[i : i + n] == chunk]
    assert len(hits) == 1, f"chunk 应在原文中恰好出现一次,实得 {len(hits)} 次"
    i = hits[0]
    return lines[:i] + lines[i + n :]


#: 三源夹具:顶部注释 / 锚点注释(条目上方,2 行与 1 行与中文)/ 行内注释 /
#: 引号三态(双引号+行内注、单引号、裸值)/ 条目间空行 / parked 注释占位 /
#: 尾节 watchlist。覆盖注释保真断言需要的全部形态。
THREE_SOURCES_YAML = """# 顶部说明:启停写回不得动我(节外注释)
id: surgery-demo
name: 手术夹具
schedule: "0 9 * * *"
sources:
  # A 的锚点注释第一行
  # A 的锚点注释第二行
  - name: alpha
    url: "https://alpha.example/"    # 行内注释跟 URL 走
    headers:
      Accept: "application/json"

  # B 的锚点注释
  - name: beta
    url: 'https://beta.example/'
    retry: 2

  # C 的锚点注释(中文,协作者留的)
  - name: gamma
    url: https://gamma.example/

  # - name: parked
  #   url: https://parked.example/   # 注释掉的占位条目:空行分隔 = 块尾注
  #                                   # 释,不归属任何解析条目,永不被搬运

watchlist:
  keywords: [羊毛, 白嫖]
"""

STASH_SUFFIX = ".disabled.json"


# ---------------------------------------------------------------------------
# 往返逐字节一致 + 非搬运行 diff
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["alpha", "beta", "gamma"])
def test_disable_enable_roundtrip_byte_identical(tmp_path, name):
    """单源停用+启用一轮:文件逐字节还原,应答契约形状不变,暂存清空。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    before = path.read_bytes()

    result = toggle(path, disable=[name])
    mid = path.read_bytes()
    assert mid != before
    assert result == {
        "file": str(path),
        "written": True,
        "enabled": [n for n in ("alpha", "beta", "gamma") if n != name],
        "disabled": [name],
    }
    # 写回后仍是 myia 可装载品类(往返一致 = doctor 同门)
    from myia.schema import load_category_file

    assert [s.name for s in load_category_file(path).sources] != []

    result = toggle(path, enable=[name])
    assert result["enabled"] == ["alpha", "beta", "gamma"]
    assert result["disabled"] == []
    assert path.read_bytes() == before  # 往返 diff 为空
    assert not (tmp_path / ("surgery-demo.yaml" + STASH_SUFFIX)).exists()


def test_disable_removes_exactly_the_chunk_lines(tmp_path):
    """停用 = 精确剜掉被搬条目区间:非搬运行逐字节、按原序全保留。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    before = path.read_bytes()
    toggle(path, disable=["beta"])

    stash = json.loads((tmp_path / ("surgery-demo.yaml" + STASH_SUFFIX)).read_text("utf-8"))
    chunk = entry._split_keep_lines(stash[0]["_myia_toggle"]["raw_block"])
    after_lines = lines_of(path.read_bytes())
    assert after_lines == without_chunk(lines_of(before), chunk)
    # 节外内容逐字节原位:顶部注释、parked 占位、尾节
    text = path.read_text("utf-8")
    assert "# 顶部说明:启停写回不得动我(节外注释)" in text
    assert "# - name: parked" in text
    assert text.rstrip().endswith("keywords: [羊毛, 白嫖]")
    assert "alpha" in text and "gamma" in text


def test_second_cycle_stays_byte_identical(tmp_path):
    """停/启再走一轮(暂存重建):仍逐字节一致 —— 手术无累积漂移。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    before = path.read_bytes()
    for _ in range(2):
        toggle(path, disable=["beta"])
        toggle(path, enable=["beta"])
    assert path.read_bytes() == before


def test_out_of_order_enable_restores_original_layout(tmp_path):
    """乱序启用(disable a,c → enable c → enable a)仍还原原始相对顺序。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    before = path.read_bytes()
    toggle(path, disable=["alpha", "gamma"])
    assert toggle(path, enable=["gamma"])["enabled"] == ["beta", "gamma"]
    assert toggle(path, enable=["alpha"])["enabled"] == ["alpha", "beta", "gamma"]
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# 无主注释块夹缝(评审实证破口回归):不归属任何条目区间的整行注释块
# ---------------------------------------------------------------------------


#: parked 占位被空行隔开、夹在两个活条目之间 —— 与谁都不紧贴 = 不落入任何
#: 条目区间。曾因「插回前驱区间之后」被跳过(b 搬到 parked 之前,静默写盘)。
PARKED_BETWEEN_YAML = (
    "id: parked-mid\n"
    "name: 夹缝占位\n"
    'schedule: "0 9 * * *"\n'
    "sources:\n"
    "  - name: a\n"
    "    url: https://a/\n"
    "\n"
    "  # parked: old source\n"
    "  #   url: https://old/\n"
    "\n"
    "  - name: b\n"
    "    url: https://b/\n"
    "watchlist:\n"
    "  keywords: []\n"
)

#: 多段锚点注释被空行劈开:para 2 紧贴条目 = 锚点随行;para 1 与谁都隔一空行
#: = 无主注释块。曾被撕裂(para 2 随 b 走、para 1 被撇在 b 之后)。
SPLIT_ANCHOR_YAML = (
    "id: split-anchor\n"
    "name: 劈开锚点\n"
    'schedule: "0 9 * * *"\n'
    "sources:\n"
    "  - name: a\n"
    "    url: https://a/\n"
    "\n"
    "  # para 1 of header\n"
    "\n"
    "  # para 2 of header\n"
    "  - name: b\n"
    "    url: https://b/\n"
    "watchlist:\n"
    "  keywords: []\n"
)

#: 无主注释块在「被移条目之后」:a/b 紧邻,parked 夹在 b 与 c 之间。b 原位在
#: gap 之前 —— 插回必须停在 gap 之前,而非一路推到后继区间起点。
PARKED_AFTER_YAML = (
    "id: parked-after\n"
    "name: 尾缝占位\n"
    'schedule: "0 9 * * *"\n'
    "sources:\n"
    "  - name: a\n"
    "    url: https://a/\n"
    "  - name: b\n"
    "    url: https://b/\n"
    "\n"
    "  # parked: retired source\n"
    "\n"
    "  - name: c\n"
    "    url: https://c/\n"
    "watchlist:\n"
    "  keywords: []\n"
)


@pytest.mark.parametrize(
    ("text", "file_name"),
    [
        (PARKED_BETWEEN_YAML, "parked_gap.yaml"),
        (SPLIT_ANCHOR_YAML, "split_anchor.yaml"),
        (PARKED_AFTER_YAML, "parked_after.yaml"),
        (PARKED_BETWEEN_YAML.replace("\n", "\r\n"), "parked_gap_crlf.yaml"),
    ],
    ids=["parked-between", "split-anchor", "parked-after", "parked-between-crlf"],
)
def test_orphan_comment_block_roundtrip_byte_identical(tmp_path, text, file_name):
    """夹缝里的无主注释块:停用再启用逐字节还原 —— 块不被搬位、多段锚点
    不被撕裂、被移条目回到块的原位那一侧;CRLF 同样成立。"""
    path = write_yaml(tmp_path, text, file_name)
    before = path.read_bytes()
    toggle(path, disable=["b"])
    mid = path.read_text("utf-8")
    assert "- name: b" not in mid  # 条目离场
    assert "# parked" in mid or "# para" in mid  # 无主注释块原地不动
    toggle(path, enable=["b"])
    assert path.read_bytes() == before  # 往返 diff 为空


def test_stash_records_gap_before_orphan_block(tmp_path):
    """暂存元数据 ``gap_before``:前驱区间与被移条目原位之间的无主注释块
    逐字节入档,enable 据此插回到块之后(而非跳过它)。"""
    path = write_yaml(tmp_path, PARKED_BETWEEN_YAML, "gap-meta.yaml")
    toggle(path, disable=["b"])
    stash = json.loads((tmp_path / ("gap-meta.yaml" + STASH_SUFFIX)).read_text("utf-8"))
    meta = stash[0]["_myia_toggle"]
    assert meta["pred"] == "a"
    assert meta["gap_before"] == "\n  # parked: old source\n  #   url: https://old/\n"


def test_stale_gap_falls_back_to_predecessor_anchor(tmp_path):
    """停用后文件被外部改过(gap 失配):退回兜底锚(前驱区间之后)照常
    写回,不拒载不崩溃 —— gap 校验是尽力精确,失配由 .bak 与两道门兜底。"""
    path = write_yaml(tmp_path, PARKED_BETWEEN_YAML, "stale.yaml")
    toggle(path, disable=["b"])
    path.write_text(
        path.read_text("utf-8").replace("# parked: old source", "# parked: reworded"),
        encoding="utf-8",
        newline="",
    )
    result = toggle(path, enable=["b"])
    assert result["written"] is True
    from myia.schema import load_category_file

    assert [s.name for s in load_category_file(path).sources] == ["a", "b"]


# ---------------------------------------------------------------------------
# 列 0 分节注释紧贴末条目(评审遗留 low-2):它属节间注释,不随末条目搬运
# ---------------------------------------------------------------------------


#: 列 0 分节注释紧贴末条目(无空行):它是给下面 watchlist 节写的分节注释,
#: 不归属末条目 —— 曾被并进末条目搬运块,停用即整体离开主文件,中间态一节
#: 注释不可见(往返虽逐字节一致,注释在停用期间消失)。
SECTION_COMMENT_TAIL_YAML = (
    "id: sect-tail\n"
    "name: 节间注释\n"
    'schedule: "0 9 * * *"\n'
    "sources:\n"
    "  - name: a\n"
    "    url: https://a/\n"
    "  - name: b\n"
    "    url: https://b/\n"
    "# 分节注释:紧贴末条目,为下面的 watchlist 节所写\n"
    "watchlist:\n"
    "  keywords: []\n"
)

#: 同布局的三源形态:多停多启 + 乱序启用时,分节注释同样原地不动。
SECTION_COMMENT_THREE_YAML = (
    "id: sect-three\n"
    "name: 节间三源\n"
    'schedule: "0 9 * * *"\n'
    "sources:\n"
    "  - name: a\n"
    "    url: https://a/\n"
    "  - name: b\n"
    "    url: https://b/\n"
    "  - name: c\n"
    "    url: https://c/\n"
    "# 分节注释:紧贴末条目 c\n"
    "watchlist:\n"
    "  keywords: []\n"
)


@pytest.mark.parametrize(
    ("text", "file_name"),
    [
        (SECTION_COMMENT_TAIL_YAML, "sect_tail.yaml"),
        (SECTION_COMMENT_TAIL_YAML.replace("\n", "\r\n"), "sect_tail_crlf.yaml"),
        (
            SECTION_COMMENT_TAIL_YAML.replace(
                "# 分节注释:紧贴末条目,为下面的 watchlist 节所写\nwatchlist:\n  keywords: []\n",
                "# 分节注释:紧贴末条目,直到 EOF\n",
            ),
            "sect_tail_eof.yaml",
        ),
    ],
    ids=["plain", "crlf", "eof-tail"],
)
def test_col0_section_comment_stays_when_last_entry_disabled(tmp_path, text, file_name):
    """停用末条目:紧贴其上的列 0 分节注释留在主文件、不入搬运块;启用还原
    后逐字节一致(CRLF 与直到 EOF 的形态同样成立)。"""
    path = write_yaml(tmp_path, text, file_name)
    before = path.read_bytes()
    toggle(path, disable=["b"])

    mid = path.read_text("utf-8")
    assert "- name: b" not in mid  # 条目离场
    assert "# 分节注释" in mid  # 分节注释原地不动
    stash = json.loads((tmp_path / (file_name + STASH_SUFFIX)).read_text("utf-8"))
    assert "# 分节注释" not in stash[0]["_myia_toggle"]["raw_block"]  # 不入搬运块
    assert stash[0]["_myia_toggle"]["pred"] == "a"

    toggle(path, enable=["b"])
    assert path.read_bytes() == before  # 往返 diff 为空


def test_col0_section_comment_survives_multi_move_cycle(tmp_path):
    """三源 + 紧贴末条目的列 0 分节注释:多停多启、乱序启用全程注释原地;
    块尾追加也落在注释之前,不把注释推走。往返逐字节一致。"""
    path = write_yaml(tmp_path, SECTION_COMMENT_THREE_YAML, "sect_three.yaml")
    before = path.read_bytes()
    toggle(path, disable=["b", "c"])
    mid = path.read_text("utf-8")
    assert "# 分节注释" in mid  # 两条目齐走,注释仍留主文件
    toggle(path, enable=["c"])  # pred=b 不在文本 → 追加块尾
    mid = path.read_text("utf-8")
    assert mid.index("- name: c") < mid.index("# 分节注释")  # 追加在注释之前
    toggle(path, enable=["b"])  # 乱序启用仍还原原始相对顺序
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# 列 0 注释嵌在末条目正文中段(low-2 修复回归):注释透明,随条目整段搬运
# ---------------------------------------------------------------------------


#: 回归夹具:列 0 注释出现在**末条目 b 的字段中段**(其后仍有 b 自己的缩进
#: 字段 ``retry: 2``)。YAML 注释透明,该字段仍属 b —— 曾因「列 0 即分节
#: 注释」的字形判定被拦腰截断:raw_block 只搬走前两行,``retry: 2`` 残留
#: 主文件,停用中间态解析时静默并入前一启用源 a(retry 语义污染,headers
#: 同理;往返字节还原掩盖中间态错挂)。
MID_ENTRY_COL0_COMMENT_YAML = (
    "id: mid-comment\n"
    "name: 条目中段注释\n"
    'schedule: "0 9 * * *"\n'
    "sources:\n"
    "  - name: a\n"
    "    url: https://a/\n"
    "  - name: b\n"
    "    url: https://b/\n"
    "# 备注:嵌在 b 的字段中段,不是分节注释\n"
    "    retry: 2\n"
    "watchlist:\n"
    "  keywords: []\n"
)


def test_mid_entry_col0_comment_travels_with_last_entry(tmp_path):
    """末条目正文中段的列 0 注释随条目整段搬运:停用中间态干净(b 的字段
    不残留并入 a,仍取默认 retry),启用逐字节还原;紧贴块界的真分节注释
    形态(缩进前瞻无续行)由上一组用例守住。"""
    from myia.schema import load_category_file

    path = write_yaml(tmp_path, MID_ENTRY_COL0_COMMENT_YAML, "mid_comment.yaml")
    before = path.read_bytes()

    toggle(path, disable=["b"])
    parsed = load_category_file(path)
    assert [s.name for s in parsed.sources] == ["a"]
    assert parsed.sources[0].retry == 3  # 默认值;b 的 retry:2 随 b 离场,不并入 a
    mid = path.read_text("utf-8")
    assert "- name: b" not in mid  # 条目离场
    assert "retry: 2" not in mid and "# 备注" not in mid  # 字段与中段注释整段随行
    stash = json.loads((tmp_path / ("mid_comment.yaml" + STASH_SUFFIX)).read_text("utf-8"))
    raw_block = stash[0]["_myia_toggle"]["raw_block"]
    assert "# 备注" in raw_block and "retry: 2" in raw_block  # 整段入搬运块

    toggle(path, enable=["b"])
    assert path.read_bytes() == before  # 往返 diff 为空


# ---------------------------------------------------------------------------
# 锚点注释随行 / 引号与顺序风格保真
# ---------------------------------------------------------------------------


def test_anchor_comment_travels_with_entry(tmp_path):
    """锚点注释随条目走:停用即离场(主文件无、暂存原文有),启用归位。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    toggle(path, disable=["beta"])
    stash = json.loads((tmp_path / ("surgery-demo.yaml" + STASH_SUFFIX)).read_text("utf-8"))
    raw_block = stash[0]["_myia_toggle"]["raw_block"]

    text = path.read_text("utf-8")
    assert "# B 的锚点注释" not in text  # 连注释一起离场,不留孤儿锚点
    assert "# B 的锚点注释" in raw_block  # 原文在暂存里
    assert "# A 的锚点注释第一行" in text  # 邻条锚点纹丝不动
    assert "# C 的锚点注释(中文,协作者留的)" in text

    toggle(path, enable=["beta"])
    assert "# B 的锚点注释" in path.read_text("utf-8")  # 随条目归位


def test_quote_and_value_style_fidelity(tmp_path):
    """引号/裸值/行内注释风格逐字节保真(safe_dump 会把三态拉平成一种)。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    before = path.read_bytes()
    for _ in range(2):
        toggle(path, disable=["alpha"])
        toggle(path, disable=["gamma"])
        toggle(path, enable=["gamma"])
        toggle(path, enable=["alpha"])
    after = path.read_text("utf-8")
    assert after == before.decode("utf-8")
    assert 'url: "https://alpha.example/"    # 行内注释跟 URL 走' in after  # 双引号+行内注
    assert "url: 'https://beta.example/'" in after  # 单引号
    assert "url: https://gamma.example/" in after  # 裸值
    # 声明顺序保真(alpha → beta → gamma)
    assert after.index("- name: alpha") < after.index("- name: beta") < after.index("- name: gamma")


# ---------------------------------------------------------------------------
# 边角路径:空 sources / 单源 / EOF 无换行 / CRLF / 异常缩进
# ---------------------------------------------------------------------------


def test_empty_sources_refuses_with_zero_write(tmp_path):
    """空 sources:停用(无源可停)与启用(暂存为空)都结构化拒绝、零写入。"""
    path = write_yaml(
        tmp_path,
        'id: empty\nname: 空源\nschedule: "0 9 * * *"\nsources: []\nwatchlist:\n  keywords: []\n',
        "empty.yaml",
    )
    before = path.read_bytes()
    with pytest.raises(entry.ProtocolError) as exc:
        toggle(path, disable=["x"])
    assert exc.value.code == "source_unknown"
    with pytest.raises(entry.ProtocolError) as exc:
        toggle(path, enable=["x"])
    assert exc.value.code == "source_unknown"
    assert path.read_bytes() == before
    assert not (tmp_path / ("empty.yaml" + STASH_SUFFIX)).exists()


def test_single_source_disable_refused_zero_write(tmp_path):
    """单源品类:停用即拒(last_source),文件零改动 —— 官方单源品类的
    「停用+启用」合法形态就是「停用被拒 + 零写入」。"""
    path = write_yaml(
        tmp_path,
        'id: solo\nname: 单源\nschedule: "0 9 * * *"\nsources:\n'
        '  - name: only\n    url: "https://only.example/"\nwatchlist:\n  keywords: []\n',
        "solo.yaml",
    )
    before = path.read_bytes()
    with pytest.raises(entry.ProtocolError) as exc:
        toggle(path, disable=["only"])
    assert exc.value.code == "last_source"
    assert path.read_bytes() == before


def test_eof_without_trailing_newline_roundtrip(tmp_path):
    """文件尾无换行:停用末条目(区间到 EOF)再启用,逐字节还原。"""
    body = THREE_SOURCES_YAML.replace(
        "watchlist:\n  keywords: [羊毛, 白嫖]\n", ""
    ).rstrip("\n")
    path = write_yaml(tmp_path, body, "tail.yaml")
    before = path.read_bytes()
    assert not before.endswith(b"\n")
    toggle(path, disable=["gamma"])
    toggle(path, enable=["gamma"])
    assert path.read_bytes() == before


def test_crlf_roundtrip_and_midstate(tmp_path):
    """CRLF 文件:中间态仍是 CRLF、被搬行带 \\r\\n 入暂存,往返逐字节一致。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML.replace("\n", "\r\n"), "crlf.yaml")
    before = path.read_bytes()
    assert b"\r\n" in before
    toggle(path, disable=["beta"])
    mid = path.read_bytes()
    stash = json.loads((tmp_path / ("crlf.yaml" + STASH_SUFFIX)).read_text("utf-8"))
    assert b"\r\n" in mid  # 未搬行没有被换行翻译
    assert "\r\n" in stash[0]["_myia_toggle"]["raw_block"]  # 被搬行整行原样入暂存
    toggle(path, enable=["beta"])
    assert path.read_bytes() == before  # 往返逐字节(含行尾)


def test_deep_indent_and_nested_list_roundtrip(tmp_path):
    """异常(但合法)缩进:4 空格条目 + 条目内嵌套块序列(更深的短横线
    不参与条目计数),往返逐字节一致。"""
    deep = (
        "id: deep\nname: 深缩进\nschedule: \"0 9 * * *\"\nsources:\n"
        "    # 深缩进锚点\n"
        "    - name: alpha\n"
        "      url: \"https://alpha.example/\"\n"
        "      extra_list:\n"
        "        - m1\n"
        "        - m2\n"
        "    - name: beta\n"
        "      url: \"https://beta.example/\"\n"
        "watchlist:\n  keywords: []\n"
    )
    path = write_yaml(tmp_path, deep, "deep.yaml")
    before = path.read_bytes()
    toggle(path, disable=["alpha"])
    assert "- m1" not in path.read_text("utf-8")  # 嵌套列表随条目整段离场
    toggle(path, enable=["alpha"])
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# 不支持的结构:结构化拒写、零写入(PRD AC3)
# ---------------------------------------------------------------------------


def _assert_refused(path: Path, code: str, params: dict) -> None:
    before = path.read_bytes()
    with pytest.raises(entry.ProtocolError) as exc:
        toggle(path, **params)
    assert exc.value.code == code
    assert path.read_bytes() == before  # 主文件零改动
    assert not path.with_name(path.name + ".bak").exists()  # 零写入(连 .bak 都不留)
    assert not path.with_name(path.name + STASH_SUFFIX).exists()


def test_flow_style_sources_refused(tmp_path):
    """流式 sources(单行条目):手术不支持 → source_write_unsupported 零写入
    (PRD 边界:支持与否以实现实测定,不支持即走 AC3 拒写)。"""
    path = write_yaml(
        tmp_path,
        'id: flow\nname: 流式\nschedule: "0 9 * * *"\n'
        'sources: [{name: a, url: "https://a/"}, {name: b, url: "https://b/"}]\n'
        "watchlist:\n  keywords: []\n",
        "flow.yaml",
    )
    _assert_refused(path, "source_write_unsupported", {"disable": ["a"]})


def test_alias_referencing_source_entry_refused(tmp_path):
    """锚点/别名引用源条目(含合并键 <<):行级搬运可能造前向引用 → 拒写。"""
    path = write_yaml(
        tmp_path,
        'id: alias\nname: 别名\nschedule: "0 9 * * *"\nsources:\n'
        "  - &tpl\n    name: a\n    url: \"https://a/\"\n"
        "  - name: b\n    <<: *tpl\n    url: \"https://b/\"\n"
        "watchlist:\n  keywords: []\n",
        "alias.yaml",
    )
    _assert_refused(path, "source_write_unsupported", {"disable": ["a"]})


def test_multi_document_refused_by_existing_parse_gate(tmp_path):
    """多文档(--- 分隔):上游既有解析门就以 category_invalid 拒载,零写入
    (手术不重复设卡;此处锁定该分工不回退)。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML + "---\nid: other\n", "multi.yaml")
    _assert_refused(path, "category_invalid", {"disable": ["alpha"]})


def test_misaligned_sources_keys_refused(tmp_path):
    """两个顶层 sources: 键(解析取末、文本扫描取首,条目行与解析结果对不齐)
    → 拒写(末块给 2 个条目以穿过 last_source 门,落到手术的对齐检查)。"""
    path = write_yaml(
        tmp_path,
        'id: dup\nname: 双键\nschedule: "0 9 * * *"\n'
        'sources:\n  - name: first\n    url: "https://first/"\n'
        'sources:\n  - name: second\n    url: "https://second/"\n'
        '  - name: third\n    url: "https://third/"\n'
        "watchlist:\n  keywords: []\n",
        "dup.yaml",
    )
    _assert_refused(path, "source_write_unsupported", {"disable": ["second"]})


# ---------------------------------------------------------------------------
# .disabled.json 暂存契约(机制原样:字段平铺在外层 + 手术元数据)
# ---------------------------------------------------------------------------


def test_stash_preserves_entry_fields_and_raw_block(tmp_path):
    """暂存条目:源字段平铺在外层(既有断言直读 name/url),手术原文与
    前驱挂在 _myia_toggle;再停用一次,原文逐字节重现。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    toggle(path, disable=["gamma"])
    stash_file = tmp_path / ("surgery-demo.yaml" + STASH_SUFFIX)
    stash = json.loads(stash_file.read_text("utf-8"))
    assert [item["name"] for item in stash] == ["gamma"]
    assert stash[0]["url"] == "https://gamma.example/"
    meta = stash[0]["_myia_toggle"]
    assert meta["pred"] == "beta"  # 原文本位置 = beta 之后
    assert "# C 的锚点注释(中文,协作者留的)" in meta["raw_block"]
    assert "url: https://gamma.example/" in meta["raw_block"]
    first_raw = meta["raw_block"]

    toggle(path, enable=["gamma"])
    toggle(path, disable=["gamma"])
    stash = json.loads(stash_file.read_text("utf-8"))
    assert stash[0]["_myia_toggle"]["raw_block"] == first_raw  # 逐字节重现


def test_stash_unreadable_contract_unchanged(tmp_path):
    """暂存文件损坏:既有 stash_unreadable 结构化拒绝,主文件零改动。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    (tmp_path / ("surgery-demo.yaml" + STASH_SUFFIX)).write_text("{broken", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(entry.ProtocolError) as exc:
        toggle(path, enable=["x"])
    assert exc.value.code == "stash_unreadable"
    assert path.read_bytes() == before


def test_legacy_stash_without_meta_falls_back_to_append(tmp_path):
    """旧版暂存(无 _myia_toggle,sidecar 升级窗口):数据无损移入块尾,
    注释不还原但不报错 —— 兜底不挡升级。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    (tmp_path / ("surgery-demo.yaml" + STASH_SUFFIX)).write_text(
        json.dumps([{"name": "legacy", "url": "https://legacy.example/"}], ensure_ascii=False),
        encoding="utf-8",
    )
    result = toggle(path, enable=["legacy"])
    assert result["enabled"] == ["alpha", "beta", "gamma", "legacy"]
    from myia.schema import load_category_file

    assert [s.name for s in load_category_file(path).sources][-1] == "legacy"
    # 追加在块尾(parked 占位之前、watchlist 之前)
    text = path.read_text("utf-8")
    assert text.index("- name: legacy") < text.index("# - name: parked")


def test_corrupted_stash_meta_stripped_from_fallback_block(tmp_path):
    """损坏暂存(``raw_block`` 非字符串)走重序列化兜底:``_myia_toggle``
    内部键先剥离再 dump —— 零痕迹进主 YAML,该源此后停用照常(修前内部键
    写进主 YAML,再停用撞保留键被拒 = 启停变砖;评审遗留 low-1)。"""
    # 单元面:兜底序列化对带内部键的条目剥离之,对无内部键的原样
    block = entry._toggle_fallback_block(
        {"name": "x", "url": "u", "_myia_toggle": {"pred": None}}, 2
    )
    assert "_myia_toggle" not in block
    assert "- name: x" in block

    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    (tmp_path / ("surgery-demo.yaml" + STASH_SUFFIX)).write_text(
        json.dumps(
            [
                {
                    "name": "legacy",
                    "url": "https://legacy.example/",
                    "_myia_toggle": {"raw_block": 123, "pred": "gamma"},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = toggle(path, enable=["legacy"])
    assert result["written"] is True
    text = path.read_text("utf-8")
    assert "_myia_toggle" not in text  # 主 YAML 零内部键痕迹
    assert "- name: legacy" in text

    # 此后停用照常;再入暂存的原文同样不带内部键(下一轮 enable 干净还原)
    result = toggle(path, disable=["legacy"])
    assert result["disabled"] == ["legacy"]
    stash = json.loads(
        (tmp_path / ("surgery-demo.yaml" + STASH_SUFFIX)).read_text("utf-8")
    )
    assert "_myia_toggle" not in stash[0]["_myia_toggle"]["raw_block"]


def test_duplicate_residue_heals_from_file_copy(tmp_path):
    """崩溃窗口双份残留(同名源在文件与暂存):以文件为准,只清暂存,
    不制造同名双条目。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    (tmp_path / ("surgery-demo.yaml" + STASH_SUFFIX)).write_text(
        json.dumps([{"name": "alpha", "url": "https://residue.example/"}], ensure_ascii=False),
        encoding="utf-8",
    )
    result = toggle(path, enable=["alpha"])
    names = [s["name"] for s in entry.yaml.safe_load(path.read_text("utf-8"))["sources"]]
    assert names == ["alpha", "beta", "gamma"]  # 没有第二个 alpha
    assert result["enabled"] == names
    assert not (tmp_path / ("surgery-demo.yaml" + STASH_SUFFIX)).exists()


def test_bak_still_written_as_second_belt(tmp_path):
    """.bak 止血保留为第二道保险:内容 = 操作前逐字节原文。"""
    path = write_yaml(tmp_path, THREE_SOURCES_YAML)
    before = path.read_bytes()
    toggle(path, disable=["beta"])
    bak = tmp_path / "surgery-demo.yaml.bak"
    assert bak.read_bytes() == before
    assert "# B 的锚点注释" in bak.read_text("utf-8")


# ---------------------------------------------------------------------------
# 官方品类 YAML:全源停用+启用往返(PRD AC1;6 份官方件 + demo 共 7 份全测)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "yaml_name",
    sorted(p.name for p in PLUGINS_DIR.glob("*.yaml")) if PLUGINS_DIR.is_dir() else [],
)
def test_official_category_yaml_roundtrip(yaml_name):
    """对官方品类 YAML 的每个解析源各做一轮停用+启用:逐字节还原。

    单源品类(monitor/stocks 等)的停用被 last_source 拒(品类必须保留至少
    一个源),合法形态即「拒写 + 零字节改动」,同样断言。
    """
    import yaml as yaml_mod

    source = PLUGINS_DIR / yaml_name
    assert source.exists(), f"官方品类缺失: {source}"
    original = source.read_bytes()
    doc = yaml_mod.safe_load(original.decode("utf-8"))
    names = [s["name"] for s in doc["sources"]]
    assert names, f"{yaml_name} 无解析源"

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / yaml_name
        for name in names:
            work.write_bytes(original)
            if len(names) == 1:
                with pytest.raises(entry.ProtocolError) as exc:
                    toggle(work, disable=[name])
                assert exc.value.code == "last_source"
                assert work.read_bytes() == original
                continue
            result = toggle(work, disable=[name])
            assert result["written"] is True
            result = toggle(work, enable=[name])
            assert result["written"] is True
            assert result["enabled"] == names  # 名单集合与顺序都还原
            assert work.read_bytes() == original, f"{yaml_name}:{name} 往返后字节不一致"

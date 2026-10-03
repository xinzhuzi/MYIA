# 实证3:飞书 im/v1/images 真探(10-03-vision-v2 proofs)
# 凭据链:~/.hermes/.env 的 FEISHU_APP_ID/FEISHU_APP_SECRET(tenant token 获取链路)
# 红线:token/app_secret 绝不打印、绝不落盘 —— 只输出 HTTP 状态与业务 code/msg。
import json
import struct
import sys
import urllib.request
import urllib.parse
import zlib
from pathlib import Path

EVIDENCE = Path(__file__).with_name("feishu-images-probe.log")


def log(line: str) -> None:
    print(line)
    with EVIDENCE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def make_png(width: int = 64, height: int = 32, rgb=(90, 130, 220)) -> bytes:
    """最小纯 Python PNG(纯色小图,zlib 手打包,零第三方依赖)。"""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    # 1) 读 hermes env(值不打印)
    env: dict[str, str] = {}
    for line in Path.home().joinpath(".hermes/.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    app_id = env.get("FEISHU_APP_ID", "")
    app_secret = env.get("FEISHU_APP_SECRET", "")
    # Hermes 同款映射(plugins/platforms/feishu/adapter.py:1252):
    # domain_name == "lark" → open.larksuite.com,其余(含 "feishu")→ open.feishu.cn
    domain_name = (env.get("FEISHU_DOMAIN") or "feishu").lower()
    domain = (
        "https://open.larksuite.com" if domain_name == "lark" else "https://open.feishu.cn"
    )
    parsed = urllib.parse.urlparse(domain)
    log(f"[probe] FEISHU_APP_ID present={bool(app_id)} FEISHU_APP_SECRET present={bool(app_secret)}")
    log(f"[probe] FEISHU_DOMAIN={domain_name} → host={parsed.netloc}")
    if not app_id or not app_secret:
        log("[probe] BLOCKED: 凭据不完整(app_id/app_secret 缺席)")
        return 2

    # 2) tenant_access_token(值只进内存变量,不打印)
    req = urllib.request.Request(
        f"{domain.rstrip('/')}/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
    log(f"[probe] tenant_access_token http={resp.status} code={body.get('code')} msg={body.get('msg')}")
    token = body.get("tenant_access_token") or ""
    if body.get("code") != 0 or not token:
        log("[probe] BLOCKED: tenant_access_token 获取失败(token 不落日志)")
        return 3

    # 3) im/v1/images 上传小图(image_type=message)
    png = make_png()
    boundary = "----myiavisionproof"
    parts = []
    parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image_type"\r\n\r\n'
            "message\r\n"
        ).encode()
    )
    parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image"; filename="vision-proof.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        + png
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    data = b"".join(parts)
    req = urllib.request.Request(
        f"{domain.rstrip('/')}/open-apis/im/v1/images",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = resp.status
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        status = exc.code
        result = json.loads(exc.read() or b"{}")
    image_key = (result.get("data") or {}).get("image_key")
    log(
        f"[probe] im/v1/images http={status} code={result.get('code')} "
        f"msg={result.get('msg')} image_key={'<present>' if image_key else '<none>'}"
    )
    # 预期:code=99991672(im:resource 未开,即「主人需后台开 im:resource」的实证)
    # 或 code=0(上传成功)。两者都算实证通过,记录实际 code。
    if result.get("code") == 0 and image_key:
        log("[probe] RESULT=uploaded(code=0, image_key 已取得;值不落日志)")
        return 0
    if result.get("code") == 99991672:
        log("[probe] RESULT=permission_denied(code=99991672,im:resource 未开 —— 实证成立)")
        return 0
    log(f"[probe] RESULT=unexpected(code={result.get('code')})")
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""生成 E2E fixture 图:含字截图 / 无字渐变 / >10MB 超大图 / <10KB 像素图。"""
from PIL import Image, ImageDraw, ImageFont

STATIC = "/tmp/myia-e2e/static"

# 1) 含文字截图(1200x400,黑字白底,中英混排 — vision OCR zh-Hans+en-US)
img = Image.new("RGB", (1200, 400), "white")
d = ImageDraw.Draw(img)
font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 44)
d.text((40, 40), "MYIA VISION PIPELINE E2E", fill="black", font=font)
d.text((40, 140), "QINJI 2026 SCREENSHOT", fill="black", font=font)
d.text((40, 240), "图像文字识别测试", fill="black", font=font)
img.save(f"{STATIC}/text-shot.png")

# 2) 无字渐变(1200x400,彩虹横向渐变,零文字)
img = Image.new("RGB", (1200, 400))
d = ImageDraw.Draw(img)
for x in range(1200):
    r = int(255 * x / 1200)
    g = int(255 * (1 - x / 1200))
    b = int(128 + 127 * (x % 300) / 300)
    d.line([(x, 0), (x, 400)], fill=(r, g, b))
img.save(f"{STATIC}/gradient.png")

# 3) >10MB 超大图(合法 PNG 头 + 随机负载;流式截断拒)
import os, random
random.seed(42)
payload = random.randbytes(11 * 1024 * 1024)
with open(f"{STATIC}/big.png", "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + payload)

# 4) <10KB 小图(4x4 像素,合法 PNG,too_small 拒)
Image.new("RGB", (4, 4), "red").save(f"{STATIC}/tiny.png")

for name in ("text-shot.png", "gradient.png", "big.png", "tiny.png"):
    print(name, os.path.getsize(f"{STATIC}/{name}"))

# 5) 重制无字渐变:加轻噪声撑过 10KB min_bytes(仍零文字)
import random as _r
_r.seed(7)
img = Image.new("RGB", (1200, 400))
d = ImageDraw.Draw(img)
px = img.load()
for x in range(1200):
    base_r = int(255 * x / 1200); base_g = int(255 * (1 - x / 1200)); base_b = 120
    for y in range(400):
        n = _r.randint(-6, 6)
        px[x, y] = (max(0, min(255, base_r + n)), max(0, min(255, base_g - n)), max(0, min(255, base_b + n // 2)))
img.save(f"{STATIC}/gradient.png")
print("gradient.png", os.path.getsize(f"{STATIC}/gradient.png"))

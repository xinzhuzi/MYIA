"""生成 detail E2E fixture 图:含字截图(经 detail 链 OCR)+ 无字噪声渐变。

文案专为 10-03-detail-images 定制——run 里读到的 image_ocr 若含这些串,
只能来自「列表页(无图)→ 追抓详情页 → 同域 <img> → 下载 → OCR」链。
"""
from PIL import Image, ImageDraw, ImageFont

STATIC = "/tmp/myia-di-e2e/static"

# 1) 含文字截图(1200x400,黑字白底,中英混排)
img = Image.new("RGB", (1200, 400), "white")
d = ImageDraw.Draw(img)
font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 44)
d.text((40, 40), "MYIA DETAIL IMAGES E2E", fill="black", font=font)
d.text((40, 140), "DETAIL PAGE OCR 2026", fill="black", font=font)
d.text((40, 240), "详情页取图链测试", fill="black", font=font)
img.save(f"{STATIC}/text-shot.png")

# 2) 无字噪声渐变(撑过 min_bytes,零文字 → ok 无 ocr 键)
import random as _r
_r.seed(11)
img = Image.new("RGB", (1200, 400))
px = img.load()
for x in range(1200):
    base_r = int(255 * x / 1200); base_g = int(255 * (1 - x / 1200)); base_b = 120
    for y in range(400):
        n = _r.randint(-6, 6)
        px[x, y] = (max(0, min(255, base_r + n)), max(0, min(255, base_g - n)), max(0, min(255, base_b + n // 2)))
img.save(f"{STATIC}/gradient.png")

import os
for name in ("text-shot.png", "gradient.png"):
    print(name, os.path.getsize(f"{STATIC}/{name}"))

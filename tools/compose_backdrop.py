"""合成位图背景 assets/backdrop.jpg，给 /maid-room image 用。

做法：2560x1600 的页面底色画布，原图放在右下角（高度占 76%，和矢量场景一致），
人物本身完全不透明，房间部分按椭圆向左、向上渐隐，融进页面底色。
图片背景由 App 按“居中铺满”绘制，窗口比例和 16:10 差得多时边缘会被裁掉。

用法：python tools/compose_backdrop.py
"""
import os

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'source', 'maid.webp')
MASK = os.path.join(HERE, 'source', 'mask.png')
OUT = os.path.join(HERE, '..', 'assets', 'backdrop.jpg')

PAGE = (243, 231, 219)
W, H = 2560, 1600
SHOW_H = round(H * .76)

rgb = Image.open(SRC).convert('RGB')
mask = Image.open(MASK).convert('L')
show_w = round(rgb.width * SHOW_H / rgb.height)
rgb = np.array(rgb.resize((show_w, SHOW_H), Image.LANCZOS)).astype(np.float32)
figure = np.array(mask.resize((show_w, SHOW_H), Image.LANCZOS)).astype(np.float32) / 255
figure = cv2.GaussianBlur(figure, (0, 0), 1.2)

yy, xx = np.mgrid[0:SHOW_H, 0:show_w].astype(np.float32)
d = np.sqrt(((xx / show_w - .64) / .64) ** 2 + ((yy / SHOW_H - .66) / .66) ** 2)
room = np.clip((1.0 - d) / .42, 0, 1)
room = room * room * (3 - 2 * room)
foot = np.clip((1.0 - yy / SHOW_H) / .13, 0, 1)   # 底部也淡出，和矢量场景一致
room = room * foot * foot * (3 - 2 * foot)
alpha = np.maximum(figure, room)[:, :, None]

canvas = np.empty((H, W, 3), np.float32)
canvas[:] = PAGE
LIFT = round(H * .07)   # 整体上移，让脚露在输入框上方
x0, y0 = W - show_w, H - SHOW_H - LIFT
canvas[y0:y0 + SHOW_H, x0:] = rgb * alpha + canvas[y0:y0 + SHOW_H, x0:] * (1 - alpha)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
Image.fromarray(canvas.astype(np.uint8)).save(OUT, quality=90, subsampling=0)
print('已生成', os.path.normpath(OUT), os.path.getsize(OUT), '字节（上限 1048576）')

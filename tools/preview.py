"""不开 App，在本地把场景渲染成一张图，用来检查描摹效果和布局。

用法：python tools/preview.py 输出.png [窗口宽 窗口高]
它直接读 hooks/scene.js 里的 SVG，自己解析路径并光栅化（只认本项目生成的那几种写法：
M/q/t/z 路径、translate+scale 变换、右下对齐的嵌套层、房间的模糊滤镜），
柔光渐变不画。画出来的图和 App 里的效果在形状和颜色上一致。
"""
import os
import re
import sys

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SCENE = os.path.join(HERE, '..', 'hooks', 'scene.js')
SS = 2  # 超采样倍数


def flatten(d, steps=5):
    polys = []
    for sub in re.findall(r'M[^M]*', d):
        m = re.match(r'M(-?\d+) (-?\d+)q(.*?)(?:t(.*))?z', sub)
        x, y = int(m.group(1)), int(m.group(2))
        q = [int(v) for v in re.findall(r'-?\d+', m.group(3))]
        t = [int(v) for v in re.findall(r'-?\d+', m.group(4) or '')]
        cx, cy = x + q[0], y + q[1]
        ex, ey = x + q[2], y + q[3]
        segs = [((x, y), (cx, cy), (ex, ey))]
        px, py = ex, ey
        for i in range(0, len(t), 2):
            cx, cy = 2 * px - cx, 2 * py - cy
            ex, ey = px + t[i], py + t[i + 1]
            segs.append(((px, py), (cx, cy), (ex, ey)))
            px, py = ex, ey
        pts = [(x, y)]
        for p0, c, p1 in segs:
            for s in range(1, steps + 1):
                u = s / steps
                pts.append(((1 - u) ** 2 * p0[0] + 2 * u * (1 - u) * c[0] + u * u * p1[0],
                            (1 - u) ** 2 * p0[1] + 2 * u * (1 - u) * c[1] + u * u * p1[1]))
        polys.append(np.array(pts, np.float32))
    return polys


def main(out, win_w=1600, win_h=1000):
    svg = open(SCENE, encoding='utf-8').read().split('`')[1]
    page = re.search(r'<rect width="100%" height="100%" fill="(#[0-9a-f]{6})"', svg).group(1)
    canvas = np.empty((win_h * SS, win_w * SS, 3), np.uint8)
    canvas[:] = tuple(int(page[i:i + 2], 16) for i in (1, 3, 5))
    side = re.search(r'<svg class="side"[^>]*viewBox="0 0 (\d+) (\d+)"[^>]*>(.*)</svg></svg>$', svg, re.S)
    lw, lh, body = int(side.group(1)), int(side.group(2)), side.group(3)
    fit = min(win_w / lw, win_h / lh)            # meet
    ox, oy = win_w - lw * fit, win_h - lh * fit   # xMaxYMax
    blur = float(re.search(r'stdDeviation="([\d.]+)"', svg).group(1))
    for g in re.finditer(r'(<g filter="url\(#room\)">)?<g transform="translate\(([-\d.]+) ([-\d.]+)\) scale\(([\d.]+)\)"><g transform="scale\(.5\)">(.*?)</g></g>', body, re.S):
        blurred, tx, ty, s, inner = g.group(1), float(g.group(2)), float(g.group(3)), float(g.group(4)), g.group(5)
        k = s * .5 * fit * SS
        for color, d in re.findall(r'<path fill="(#[0-9a-f]{6})" fill-rule="evenodd" d="([^"]*)"/>', inner):
            rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
            polys = [np.round((p * k + np.array([(ox + tx * fit) * SS, (oy + ty * fit) * SS], np.float32)) * 16).astype(np.int32) for p in flatten(d)]
            cv2.fillPoly(canvas, polys, rgb, lineType=cv2.LINE_AA, shift=4)
        if blurred:
            canvas = cv2.GaussianBlur(canvas, (0, 0), blur * fit * SS)
    img = cv2.resize(canvas, (win_w, win_h), interpolation=cv2.INTER_AREA)
    Image.fromarray(img).save(out)
    print('已输出', out, f'{win_w}x{win_h}')


if __name__ == '__main__':
    args = sys.argv[1:]
    main(args[0], *(int(v) for v in args[1:3]))

"""生成女仆小屋主题的背景场景 hooks/scene.js（一个纯矢量 SVG 字符串）。

用法（在 mod 文件夹里执行）:
    python tools/cutout.py        # 只在换图或改抠图提示后才需要，生成 tools/source/mask.png
    python tools/build_scene.py   # 生成 hooks/scene.js
    python tools/preview.py 输出.png   # 不开 App，本地渲染一张 1600x1000 的效果图

为什么是纯矢量：桌面端的背景场景只画矢量图形，嵌入的位图会被拦掉，
所以插画要描摹成分层矢量路径（tools/trace.py）。整段 SVG 必须 ≤ 131072 字符。

为什么是静态：场景按整张图绘制，动画会让 App 每帧重画十万字符的路径，持续占 CPU。

场景结构（根 svg 无 viewBox，铺满窗口）：
1. 奶油色底 + 几团暖色柔光（居中 slice 层）
2. 右下角（右下对齐 meet 层，始终完整可见，整体上移一点让脚露在输入框上方）：
   a. 房间：把人物从原图里抹掉、整体模糊、向页面底色渐隐，再描成几块色斑并加模糊滤镜，
      得到一片柔焦的卧室氛围，边缘自然融进页面
   b. 人物：按 mask 抠出后精描；脸部另用更高分辨率描一小块叠在上面
窗口宽度小于 900 像素时右下角整层隐藏，避免挡住正文。
"""
import os
import sys

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from trace import palette, to_group, trace  # noqa: E402

SRC = os.path.join(HERE, 'source', 'maid.webp')
MASK = os.path.join(HERE, 'source', 'mask.png')
OUT = os.path.join(HERE, '..', 'hooks', 'scene.js')
OUT_DTS = os.path.join(HERE, '..', 'hooks', 'scene.d.ts')
LIMIT = 131072

PAGE = (243, 231, 219)           # 页面底色 #f3e7db，房间向它渐隐
LAYER_W, LAYER_H = 1600, 1000    # 右下角层的坐标系
SHOW_H = 760                     # 插画在这一层里显示的高度
LIFT = 70                        # 插画整体上移的量：让脚露在输入框上方，不被挡住
FACE_BOX = (340, 185, 540, 370)  # 原图里脸部的范围（x0, y0, x1, y1），单独精描
ROOM_BLUR = 9                    # 房间的模糊半径（右下角层的单位）
GLOWS = [(120, 60, 620, '#ffe3c2', .55), (900, 180, 520, '#fff7ec', .7), (1380, 520, 640, '#fbd9bd', .38)]

rgb_full = np.array(Image.open(SRC).convert('RGB'))
mask_full = np.array(Image.open(MASK).convert('L'))
SRC_H, SRC_W = mask_full.shape
UNIT = SHOW_H / SRC_H            # 原图 1 像素在右下角层里的大小
X0, Y0 = LAYER_W - SRC_W * UNIT, LAYER_H - SHOW_H - LIFT


def resized(arr, scale, interp=cv2.INTER_AREA):
    return cv2.resize(arr, (round(arr.shape[1] * scale), round(arr.shape[0] * scale)), interpolation=interp)


def room_part(scale=0.2, k=10, eps=1.2):
    """柔焦房间：抹掉人物、模糊、向页面色渐隐后描摹。"""
    small = resized(rgb_full, scale)
    hole = cv2.dilate(resized(mask_full, scale), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    filled = cv2.inpaint(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), hole, 5, cv2.INPAINT_TELEA)
    filled = cv2.cvtColor(cv2.GaussianBlur(filled, (0, 0), 5), cv2.COLOR_BGR2RGB).astype(np.float32)
    h, w = filled.shape[:2]
    # 渐隐权重：以右下偏内的一点为中心的椭圆，中间完全显示，向左、向上淡出
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx / w - .64) / .64) ** 2 + ((yy / h - .66) / .66) ** 2)
    weight = np.clip((1.0 - d) / .42, 0, 1)
    weight = weight * weight * (3 - 2 * weight)   # 平滑过渡
    # 插画上移后底边不再贴着窗口底，所以底部也要淡出，不然地板会留下一条硬边
    foot = np.clip((1.0 - yy / h) / .13, 0, 1)
    weight = (weight * foot * foot * (3 - 2 * foot))[:, :, None]
    faded = (filled * weight + np.array(PAGE, np.float32) * (1 - weight)).astype(np.uint8)
    alpha = np.full((h, w), 255, np.uint8)
    centers = palette(faded, alpha, k)
    page_lab = cv2.cvtColor(np.array([[PAGE]], np.uint8), cv2.COLOR_RGB2LAB).reshape(3).astype(np.float32)
    nearest = int(((centers - page_lab) ** 2).sum(axis=1).argmin())
    # 和页面色最接近的那一种当底并且不画：房间的外缘就是透明的
    layers = trace(faded, alpha, centers, eps=eps, min_area=12, sigma=0, base=nearest, skip=(nearest,))
    return {'layers': layers, 'x': X0, 'y': Y0, 'scale': UNIT / scale, 'blur': ROOM_BLUR}


def figure_parts(scale, k, eps, min_area, face_scale, face_extra, face_eps):
    """人物：整体按 scale 描；脸部按 face_scale 再描一小块叠上去，沿用同一套颜色并补几种脸部专用色。"""
    body_rgb = resized(rgb_full, scale)
    body_alpha = np.where(resized(mask_full, scale) > 127, 255, 0).astype(np.uint8)
    centers = palette(body_rgb, body_alpha, k)
    dark = int(centers[:, 0].argmin())
    parts = [{'layers': trace(body_rgb, body_alpha, centers, eps=eps, min_area=min_area), 'x': X0, 'y': Y0, 'scale': UNIT / scale}]
    if face_scale:
        x0, y0, x1, y1 = FACE_BOX
        crop_rgb = resized(rgb_full[y0:y1, x0:x1], face_scale)
        crop_mask = resized(mask_full[y0:y1, x0:x1], face_scale)
        fh, fw = crop_mask.shape
        # 椭圆形的一块，接缝落在头发里，比方框的直边不显眼
        ell = np.zeros((fh, fw), np.uint8)
        cv2.ellipse(ell, (fw // 2, fh // 2), (fw // 2 - 1, fh // 2 - 1), 0, 0, 360, 255, -1)
        face_alpha = np.where((crop_mask > 127) & (ell > 0), 255, 0).astype(np.uint8)
        face_centers = palette(crop_rgb, face_alpha, face_extra, base=centers, far=8)
        layers = trace(crop_rgb, face_alpha, face_centers, eps=face_eps, min_area=4, sigma=0.5, base=dark)
        parts.append({'layers': layers, 'x': X0 + x0 * UNIT, 'y': Y0 + y0 * UNIT, 'scale': UNIT / face_scale})
    return parts


def parts(cfg):
    return [room_part()] + figure_parts(*cfg)


def group(part):
    g = f'<g transform="translate({part["x"]:.1f} {part["y"]:.1f}) scale({part["scale"]:.4f})">{to_group(part["layers"])}</g>'
    return f'<g filter="url(#room)">{g}</g>' if part.get('blur') else g


def build(cfg):
    page = '#%02x%02x%02x' % PAGE
    defs = [f'<filter id="room" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="{ROOM_BLUR}"/></filter>']
    glows = []
    for i, (cx, cy, r, color, op) in enumerate(GLOWS):
        defs.append(f'<radialGradient id="g{i}"><stop offset="0" stop-color="{color}" stop-opacity="{op}"/><stop offset="1" stop-color="{color}" stop-opacity="0"/></radialGradient>')
        glows.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#g{i})"/>')
    side = (f'<svg class="side" width="100%" height="100%" viewBox="0 0 {LAYER_W} {LAYER_H}" preserveAspectRatio="xMaxYMax meet">'
            + ''.join(group(p) for p in parts(cfg)) + '</svg>')
    css = '@media (max-width:900px){.side{display:none}}'
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%">'
            f'<defs>{"".join(defs)}</defs><style>{css}</style>'
            f'<rect width="100%" height="100%" fill="{page}"/>'
            f'<svg width="100%" height="100%" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice">{"".join(glows)}</svg>'
            f'{side}</svg>')


# 从细到粗依次尝试，挑第一个不超预算的
# （整体缩放、颜色数、化简误差、最小面积、脸部缩放、脸部补色数、脸部化简误差）
CANDIDATES = [
    (0.42, 24, 1.0, 8, 1.0, 6, 0.7),
    (0.40, 24, 1.0, 8, 1.0, 6, 0.7),
    (0.40, 22, 1.0, 8, 0.9, 6, 0.8),
    (0.38, 22, 1.0, 8, 0.9, 6, 0.8),
    (0.36, 22, 1.0, 8, 0.8, 6, 0.8),
    (0.34, 20, 1.1, 9, 0.8, 5, 0.8),
    (0.32, 20, 1.2, 10, 0.7, 5, 0.9),
]


def pick():
    for cfg in CANDIDATES:
        svg = build(cfg)
        print(f'尝试 {cfg}：{len(svg)} 字符')
        if len(svg) <= LIMIT - 512:
            return cfg, svg
    raise SystemExit(f'场景超出 {LIMIT} 字符上限')


if __name__ == '__main__':
    cfg, svg = pick()
    assert '`' not in svg and '${' not in svg and '\\' not in svg
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('// 由 tools/build_scene.py 生成，不要手改；改布局请改脚本后重新生成\n')
        f.write('export const SCENE = `' + svg + '`\n')
    with open(OUT_DTS, 'w', encoding='utf-8') as f:
        f.write('// 由 tools/build_scene.py 生成\nexport declare const SCENE: string\n')
    with open(os.path.join(HERE, 'source', 'scene.svg'), 'w', encoding='utf-8') as f:
        f.write(svg)   # 同一份 SVG 的独立文件，方便用浏览器直接打开看
    print(f'已生成 {os.path.normpath(OUT)}：{len(svg)} 字符（上限 {LIMIT}），参数 {cfg}')

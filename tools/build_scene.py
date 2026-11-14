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
2. 装饰：左上角窗格投下的光、底部层叠的剪纸波浪（蕾丝圆点、细金线）、左下角落花荡开的波纹（见 decor）
3. 右下角（右下对齐 meet 层，始终完整可见，整体上移一点让脚露在输入框上方）：
   a. 房间（画在波浪下面）：把人物从原图里抹掉、整体模糊、向页面底色渐隐，再描成几块色斑并加模糊滤镜，
      得到一片柔焦的卧室氛围，边缘自然融进页面
   b. 人物（画在波浪上面）：按 mask 抠出后精描；脸部另用更高分辨率描一小块叠在上面
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


# 底部的层叠波浪（坐标系 1600x420 的底部条带，横向拉伸）：基线、振幅、波长、相位、颜色、浓度
# 颜色取自插画里的杏色、蜜桃色和裙摆的橙，从远到近逐层加深，像几层叠起来的剪纸
WAVES = [
    (110, 24, 1180, 0, '#f2cfb3', .8),
    (168, 28, 1420, -380, '#ecc1a2', .75),
    (226, 22, 1000, -170, '#e6b090', .75),
    (288, 20, 1260, -620, '#df9f7b', .75),
    (346, 14, 880, -300, '#edc2a4', .9),
]
POND = (300, 700)   # 左下角“落花荡开的波纹”中心（左下对齐的 1600x1000 层）
RINGS = [(40, .95), (84, .8), (138, .62), (204, .45), (284, .28)]


def wave_path(baseline, amp, wavelength, x0):
    """正弦波（q/t 半周期拼接）：返回 (封底的填充形状, 顶边线)"""
    half = round(wavelength / 2)
    n = -(-(1800 - x0) // half)
    rim = f'M{x0} {baseline}q{half // 2} {-amp * 2} {half} 0' + f't{half} 0' * (n - 1)
    return rim + f'V420H{x0}Z', rim


def decor():
    """装饰层：底部层叠波浪、左下角落花波纹、左上角窗格投下的光。返回 (defs, 正文)。

    全部是低对比的暖色，压在正文底下也不影响阅读；不用模糊滤镜，阴影用错位的半透明描边代替，
    绘制开销很小。"""
    defs = ['<path id="pt" d="M0 0q6-9 0-19q-6 10 0 19z"/>',
            '<g id="fl">' + '<use href="#pt"/>' + ''.join(f'<use href="#pt" transform="rotate({a})"/>' for a in range(45, 360, 45)) + '</g>',
            '<radialGradient id="pool"><stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>',
            '<linearGradient id="win" gradientUnits="userSpaceOnUse" x2="330" y2="430"><stop offset="0" stop-color="#fff" stop-opacity=".5"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>']
    band = []
    for i, (base, amp, wl, shift, color, op) in enumerate(WAVES):
        fill_d, rim_d = wave_path(base, amp, wl, -200 + shift)
        defs.append(f'<linearGradient id="w{i}" x2="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity="{op * .6:.2f}"/><stop offset="1" stop-color="{color}" stop-opacity="{op}"/></linearGradient>')
        # 顶边线只定义一次，后面用 <use> 复用；描边宽度按屏幕像素算，不跟着条带的横向拉伸变形
        defs.append(f'<path id="r{i}" d="{rim_d}" vector-effect="non-scaling-stroke"/>')
        # 剪纸阴影：顶边线往上错开、加宽、半透明，先画；再画本层，盖住下半截，露出来的就是边缘上方的一道影子
        band.append(f'<use href="#r{i}" stroke-width="18" stroke-opacity=".09" transform="translate(0 -6)"/>'
                    f'<use href="#r{i}" stroke-width="7" stroke-opacity=".14" transform="translate(0 -2)"/>'
                    f'<path d="{fill_d}" fill="url(#w{i})" stroke="none"/>'
                    f'<use href="#r{i}" stroke="#fff" stroke-width="1.8" stroke-opacity=".9"/>')
    # 第二层边缘缀一串蕾丝圆点，第四层下方走一道细金线，呼应裙边的蕾丝和金色滚边
    band.append('<use href="#r1" stroke="#fff" stroke-width="3.6" stroke-opacity=".95" stroke-dasharray="0 11" stroke-linecap="round" transform="translate(0 9)"/>'
                '<use href="#r3" stroke="#b97a42" stroke-width="1.3" stroke-opacity=".7" transform="translate(0 12)"/>')
    waves = ('<svg y="58%" width="100%" height="42%" viewBox="0 0 1600 420" preserveAspectRatio="none">'
             f'<g fill="none" stroke="#9a6446">{"".join(band)}</g></svg>')

    cx, cy = POND
    ry = .3
    rings = ''.join(f'<ellipse cx="{cx}" cy="{cy}" rx="{r}" ry="{r * ry:.0f}" stroke-opacity="{o}"/>' for r, o in RINGS)
    pond = (f'<ellipse cx="{cx}" cy="{cy}" rx="340" ry="104" fill="url(#pool)"/>'
            # 每道波纹下面错开 3 个单位描一道暗边，像水面的起伏
            f'<g fill="none" stroke="#9a6446" stroke-width="3" stroke-opacity=".5" transform="translate(0 4)" opacity=".5">{rings}</g>'
            f'<g fill="none" stroke="#fff" stroke-width="2.2">{rings}</g>'
            # 落在水面上的一朵橙花（和发饰同款），压扁一点表示平躺；下面一小片影子
            f'<ellipse cx="{cx}" cy="{cy + 7}" rx="34" ry="9" fill="#9a6446" fill-opacity=".25"/>'
            f'<use href="#fl" fill="#e9843f" transform="translate({cx} {cy}) scale(1.8 .9)"/>'
            f'<use href="#fl" fill="#f6b27a" transform="translate({cx} {cy - 1}) scale(1 .5) rotate(22)"/>'
            f'<circle cx="{cx}" cy="{cy - 1}" r="5" fill="#b8541d"/>'
            # 几片随波漂开的花瓣
            '<g fill="#e8833f" fill-opacity=".85">'
            + ''.join(f'<use href="#pt" transform="translate({x} {y}) rotate({a}) scale({s})"/>'
                      for x, y, a, s in [(560, 740, 70, 1.1), (680, 660, 140, .9), (130, 790, -40, 1), (820, 760, 100, .8), (430, 590, 20, .75)])
            + '</g>')
    corner = f'<svg width="100%" height="100%" viewBox="0 0 1600 1000" preserveAspectRatio="xMinYMax meet">{pond}</svg>'
    # 左上角：窗格投在墙上的光（原画里窗户在左边），斜着拉长，很淡
    window = ('<svg width="100%" height="100%" viewBox="0 0 1600 1000" preserveAspectRatio="xMinYMin meet">'
              '<g transform="matrix(1 .1 -.42 1 250 30)" fill="url(#win)">'
              '<rect width="150" height="200"/><rect x="164" width="150" height="200"/>'
              '<rect y="214" width="150" height="200"/><rect x="164" y="214" width="150" height="200"/></g></svg>')
    return defs, window + waves + corner


def build(cfg):
    page = '#%02x%02x%02x' % PAGE
    defs = [f'<filter id="room" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="{ROOM_BLUR}"/></filter>']
    glows = []
    for i, (cx, cy, r, color, op) in enumerate(GLOWS):
        defs.append(f'<radialGradient id="g{i}"><stop offset="0" stop-color="{color}" stop-opacity="{op}"/><stop offset="1" stop-color="{color}" stop-opacity="0"/></radialGradient>')
        glows.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#g{i})"/>')
    decor_defs, decor_body = decor()
    # 右下角分两层：房间在波浪下面，人物在波浪上面。房间向页面底色渐隐，
    # 如果压在波浪上会露出一块浅色的方形边，所以让波浪盖住房间的下半截，人物再坐在波浪上
    side_open = f'<svg class="side" width="100%" height="100%" viewBox="0 0 {LAYER_W} {LAYER_H}" preserveAspectRatio="xMaxYMax meet">'
    room, *figure = parts(cfg)
    side = side_open + group(room) + '</svg>' + decor_body + side_open + ''.join(group(p) for p in figure) + '</svg>'
    css = '@media (max-width:900px){.side{display:none}}'
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%">'
            f'<defs>{"".join(defs + decor_defs)}</defs><style>{css}</style>'
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

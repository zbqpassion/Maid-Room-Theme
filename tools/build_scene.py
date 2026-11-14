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
2. 装饰（见 decor）：左上角窗格投下的光、底部层叠的剪纸波浪、第一层水面上落花荡开的波纹、
   人物坐的软垫、漂着的花瓣
3. 右下角（右下对齐 meet 层，始终完整可见，整体上移一点让脚露在输入框上方），分三段穿插在装饰之间：
   a. 房间（最后面，颜色压淡，只留一点氛围）：把人物从原图里抹掉、整体模糊、向页面底色渐隐，再描成几块色斑并加模糊滤镜，
      得到一片柔焦的卧室氛围，边缘自然融进页面
   b. 软垫（夹在第一层波浪和其余波浪之间）
   c. 人物（最前面）：按 mask 抠出后精描；脸部另用更高分辨率描一小块叠在上面
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
ROOM_STRENGTH = .6               # 房间的浓度：压淡一些，深色块不抢人物，也不显脏
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
    weight = (weight * foot * foot * (3 - 2 * foot))[:, :, None] * ROOM_STRENGTH
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
# 颜色取自插画里的杏色、蜜桃色和裙摆的橙，从远到近逐层加深，像几层叠起来的剪纸。
# 第一层和第二层之间留得宽一些，当作水面，落花的波纹就画在这一层上
WAVES = [
    (110, 24, 1180, 0, '#f2cfb3', .8),
    (196, 28, 1420, -380, '#ecc1a2', .75),
    (250, 22, 1000, -170, '#e6b090', .75),
    (304, 20, 1260, -620, '#df9f7b', .75),
    (356, 14, 880, -300, '#edc2a4', .9),
]
POND = (300, 156)   # 落花的位置（条带坐标），在第一层波浪的水面上
RINGS = [(40, .95), (86, .8), (142, .62), (210, .45), (292, .3)]
SHADE = '#a8704c'   # 阴影用的暖棕色
# 人物坐的软垫（右下角层的坐标）：从人物左边的波浪里隆起，垫在裙子下面，一直到窗口右边
CUSHION = 'M930 1000V800C1030 770 1090 610 1200 552C1290 506 1430 500 1600 514V1000Z'
CUSHION_RIM = 'M930 800C1030 770 1090 610 1200 552C1290 506 1430 500 1600 514'


def wave_path(baseline, amp, wavelength, x0):
    """正弦波（q/t 半周期拼接）：返回 (封底的填充形状, 顶边线)"""
    half = round(wavelength / 2)
    n = -(-(1800 - x0) // half)
    rim = f'M{x0} {baseline}q{half // 2} {-amp * 2} {half} 0' + f't{half} 0' * (n - 1)
    return rim + f'V420H{x0}Z', rim


def papercut(rim_id, fill, white='#fff'):
    """剪纸式的一层：顶边线往上错开、加宽、半透明，先画；再画本层，盖住下半截，
    露出来的就是边缘上方的一道影子；最后描一道白边。不用模糊滤镜，绘制开销很小。"""
    return (f'<use href="#{rim_id}" stroke-width="18" stroke-opacity=".08" transform="translate(0 -6)"/>'
            f'<use href="#{rim_id}" stroke-width="7" stroke-opacity=".13" transform="translate(0 -2)"/>'
            f'{fill}<use href="#{rim_id}" stroke="{white}" stroke-width="1.8" stroke-opacity=".9"/>')


def decor():
    """装饰层，全部是低对比的暖色，压在正文底下也不影响阅读。返回 (defs, 各段正文)。

    从后往前的顺序：窗格的光、第一层波浪和它水面上的落花波纹、软垫、其余几层波浪、漂着的花瓣。
    后面的波浪会盖住波纹和软垫的下半截，所以波纹只出现在水面那一层，线条不会互相穿插。"""
    defs = ['<path id="pt" d="M0 0q6-9 0-19q-6 10 0 19z"/>',
            '<g id="fl">' + '<use href="#pt"/>' + ''.join(f'<use href="#pt" transform="rotate({a})"/>' for a in range(45, 360, 45)) + '</g>',
            '<radialGradient id="pool"><stop offset="0" stop-color="#fff" stop-opacity=".6"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>',
            '<linearGradient id="win" gradientUnits="userSpaceOnUse" x2="330" y2="430"><stop offset="0" stop-color="#fff" stop-opacity=".46"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>',
            # 软垫的填充和描边都向下淡出：波浪是半透明的，不淡出的话软垫的下半截会透出一块生硬的形状
            '<linearGradient id="cu" gradientUnits="userSpaceOnUse" x2="0" y1="500" y2="790"><stop offset="0" stop-color="#fdf7f0"/><stop offset=".5" stop-color="#f6e3d1" stop-opacity=".95"/><stop offset="1" stop-color="#f0d2ba" stop-opacity="0"/></linearGradient>',
            '<linearGradient id="cw" gradientUnits="userSpaceOnUse" x2="0" y1="630" y2="780"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>',
            f'<linearGradient id="cd" gradientUnits="userSpaceOnUse" x2="0" y1="630" y2="780"><stop offset="0" stop-color="{SHADE}"/><stop offset="1" stop-color="{SHADE}" stop-opacity="0"/></linearGradient>',
            f'<path id="rc" d="{CUSHION_RIM}"/>']
    layers = []
    for i, (base, amp, wl, shift, color, op) in enumerate(WAVES):
        fill_d, rim_d = wave_path(base, amp, wl, -200 + shift)
        defs.append(f'<linearGradient id="w{i}" x2="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity="{op * .6:.2f}"/><stop offset="1" stop-color="{color}" stop-opacity="{op}"/></linearGradient>')
        # 顶边线只定义一次，后面用 <use> 复用；描边宽度按屏幕像素算，不跟着条带的横向拉伸变形
        defs.append(f'<path id="r{i}" d="{rim_d}" vector-effect="non-scaling-stroke"/>')
        if i == 0:
            defs.append(f'<clipPath id="c0"><path d="{fill_d}"/></clipPath>')
        layers.append(papercut(f'r{i}', f'<path d="{fill_d}" fill="url(#w{i})" stroke="none"/>'))

    def band(body):
        return ('<svg y="58%" width="100%" height="42%" viewBox="0 0 1600 420" preserveAspectRatio="none">'
                f'<g fill="none" stroke="{SHADE}">{body}</g></svg>')

    cx, cy = POND
    rings = ''.join(f'<ellipse cx="{cx}" cy="{cy}" rx="{r}" ry="{r * .17:.0f}" stroke-opacity="{o}"/>' for r, o in RINGS)
    pond = ('<g clip-path="url(#c0)">'
            f'<ellipse cx="{cx}" cy="{cy}" rx="360" ry="62" fill="url(#pool)" stroke="none"/>'
            # 每道波纹下面错开一点描一道暗边，像水面的起伏
            f'<g stroke-width="1.6" transform="translate(0 1.5)" opacity=".18">{rings}</g>'
            f'<g stroke="#fff" stroke-width="2">{rings}</g></g>'
            # 落在水面上的一朵橙花（和发饰同款），压扁表示平躺；下面一小片影子
            f'<g stroke="none"><ellipse cx="{cx}" cy="{cy + 5}" rx="30" ry="6" fill="{SHADE}" fill-opacity=".14"/>'
            f'<use href="#fl" fill="#e9843f" transform="translate({cx} {cy}) scale(1.8 .72)"/>'
            f'<use href="#fl" fill="#f6b27a" transform="translate({cx} {cy - 1}) scale(1 .4) rotate(22)"/>'
            f'<ellipse cx="{cx}" cy="{cy - 1}" rx="5" ry="3.4" fill="#b8541d"/></g>')
    back = (
        # 左上角：窗格投在墙上的光（原画里窗户在左边），斜着拉长，很淡
        '<svg width="100%" height="100%" viewBox="0 0 1600 1000" preserveAspectRatio="xMinYMin meet">'
        '<g transform="matrix(1 .1 -.42 1 250 30)" fill="url(#win)">'
        '<rect width="150" height="200"/><rect x="164" width="150" height="200"/>'
        '<rect y="214" width="150" height="200"/><rect x="164" y="214" width="150" height="200"/></g></svg>'
        + band(layers[0] + pond))
    # 软垫和波浪同一种剪纸画法；边上缀一串蕾丝圆点，呼应裙边的蕾丝
    cushion = ('<g fill="none" stroke="url(#cd)">' + papercut('rc', f'<path d="{CUSHION}" fill="url(#cu)" stroke="none"/>', 'url(#cw)')
               + '<use href="#rc" stroke="url(#cw)" stroke-width="3.4" stroke-opacity=".95" stroke-dasharray="0 10" stroke-linecap="round" transform="translate(0 11)"/></g>')
    front = (band(''.join(layers[1:])
                  # 第四层下方走一道细金线，呼应裙摆的金色滚边
                  + '<use href="#r3" stroke="#b97a42" stroke-width="1.3" stroke-opacity=".7" transform="translate(0 12)"/>')
             # 几片随波漂开的花瓣
             + '<svg width="100%" height="100%" viewBox="0 0 1600 1000" preserveAspectRatio="xMinYMax meet"><g fill="#e8833f" fill-opacity=".85">'
             + ''.join(f'<use href="#pt" transform="translate({x} {y}) rotate({a}) scale({s})"/>'
                       for x, y, a, s in [(600, 800, 70, 1.1), (760, 690, 140, .9), (120, 860, -40, 1), (900, 880, 100, .8), (470, 610, 20, .75)])
             + '</g></svg>')
    # 两只脚落在波浪上的接触影子，让人物踩实
    feet = ''.join(f'<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}" fill="{SHADE}" fill-opacity="{o}"/>'
                   for x, y, rx, ry, o in [(1254, 890, 52, 9, .12), (1254, 890, 32, 5, .2), (1358, 822, 44, 8, .12), (1358, 822, 26, 4.5, .2)])
    return defs, back, cushion, front, feet


def build(cfg):
    page = '#%02x%02x%02x' % PAGE
    defs = [f'<filter id="room" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="{ROOM_BLUR}"/></filter>']
    glows = []
    for i, (cx, cy, r, color, op) in enumerate(GLOWS):
        defs.append(f'<radialGradient id="g{i}"><stop offset="0" stop-color="{color}" stop-opacity="{op}"/><stop offset="1" stop-color="{color}" stop-opacity="0"/></radialGradient>')
        glows.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#g{i})"/>')
    decor_defs, back, cushion, front, feet = decor()
    # 右下角分三段穿插在装饰之间：房间在最后面，软垫夹在第一层波浪和其余波浪之间，人物在最前面
    side_open = f'<svg class="side" width="100%" height="100%" viewBox="0 0 {LAYER_W} {LAYER_H}" preserveAspectRatio="xMaxYMax meet">'
    room, *figure = parts(cfg)
    side = (side_open + group(room) + '</svg>' + back + side_open + cushion + '</svg>' + front
            + side_open + feet + ''.join(group(p) for p in figure) + '</svg>')
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
    (0.40, 22, 1.0, 8, 0.85, 5, 0.8),
    (0.395, 22, 1.0, 8, 0.85, 5, 0.8),
    (0.39, 22, 1.0, 8, 0.85, 5, 0.8),
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

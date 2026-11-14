"""把位图描摹成分层矢量路径（SVG <path> 片段）。

为什么需要：桌面端的背景场景只画矢量，嵌入的位图会被拦掉，而整段 SVG 又不能超过 131072 字符，
所以要用尽量少的字符把插画描出来。

做法：
1. 用 k-means 在 Lab 空间把颜色归并成 K 种（palette），每个像素归到最近的一种；
   对归类结果做一次轻微的“多数表决”平滑，并把小碎块并入周围，边界更干净、顶点更少。
2. 最暗的那种颜色当底，先把整个轮廓涂满；其余颜色按面积从大到小各画自己的区域。
   每个区域只向“之后才画的颜色”里外扩几个像素，被后面的图层盖住，所以色块之间不露缝，
   又不会吃掉先画的细线。万一化简后还有缝，露出来的是最暗的底色，看起来像描边。
   区域里只含“之后才画的颜色”的洞直接填掉，省掉一圈轮廓。
   （相比“第 i 层 = 第 i 种及之后所有颜色的并集”的叠层画法，每条边界只画两次，字符数大约减半。）
3. 轮廓按 epsilon 化简后写成“过各边中点的二次曲线”：顶点当控制点，曲线经过相邻顶点的中点，
   SVG 里用 q 开头、后面全是 t（控制点自动取上一个的镜像），每个顶点只花一对坐标，
   和折线一样省字符，但放大后是圆滑的。尖角（发梢、褶边）把顶点重复一次，曲线就会真的经过它。
   为了让中点是整数、镜像不积累误差，坐标整体乘 2，使用时外层要再缩小一半（见 to_group）。

依赖：numpy、opencv。
"""
import cv2
import numpy as np


def _smooth(rgb):
    return cv2.bilateralFilter(np.ascontiguousarray(rgb), 7, 40, 7)


def _lab(rgb):
    return cv2.cvtColor(_smooth(rgb), cv2.COLOR_RGB2LAB).astype(np.float32)


def palette(rgb, alpha, k, seed=7, base=None, far=0.0):
    """对 alpha>0 的像素做 k-means，返回 Lab 色板（k x 3，float32）。

    base: 已有的色板；给了就只对“离已有颜色都比较远（Lab 距离 > far）”的像素聚类，
    返回 已有 + 新增，用来给脸部补几种专用颜色而不改动别处的颜色。
    """
    lab = _lab(rgb).reshape(-1, 3)
    samples = lab[alpha.reshape(-1) > 0]
    if base is not None:
        dist = np.sqrt(((samples[:, None, :] - base[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
        samples = samples[dist > far]
        if len(samples) < k * 4:
            return base
    cv2.setRNGSeed(seed)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.5)
    _, _, centers = cv2.kmeans(np.ascontiguousarray(samples), k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    return centers if base is None else np.vstack([base, centers])


def _spline_path(poly, corner_deg):
    """poly: Nx2 整数顶点（闭合）。返回坐标乘 2 后的路径字符串。"""
    n = len(poly)
    pts = [(int(x), int(y)) for x, y in poly]
    verts = []
    cos_limit = np.cos(np.radians(corner_deg))
    for i in range(n):
        ax, ay = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
        bx, by = pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1]
        la, lb = (ax * ax + ay * ay) ** .5, (bx * bx + by * by) ** .5
        verts.append(pts[i])
        # 转角大于 corner_deg 视为尖角：重复一次顶点
        if la and lb and (ax * bx + ay * by) / (la * lb) < cos_limit:
            verts.append(pts[i])
    m = len(verts)
    mids = [(verts[j][0] + verts[(j + 1) % m][0], verts[j][1] + verts[(j + 1) % m][1]) for j in range(m)]
    px, py = mids[0]
    head = f'M{px} {py}'
    rel = []
    for j in range(1, m + 1):
        ex, ey = mids[j % m]
        if j == 1:
            cx, cy = verts[1][0] * 2, verts[1][1] * 2
            rel.append(f'{cx - px} {cy - py} {ex - px} {ey - py}')
        else:
            rel.append(f'{ex - px} {ey - py}')
        px, py = ex, ey
    # 相对坐标：负号可当分隔符，进一步压缩
    body = 'q' + rel[0].replace(' -', '-')
    if len(rel) > 1:
        body += 't' + ' '.join(rel[1:]).replace(' -', '-')
    return head + body + 'z'


def _paths(mask, eps, min_area, corner_deg):
    contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    parts = []
    for c in contours:
        if abs(cv2.contourArea(c)) < min_area:
            continue
        poly = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
        if len(poly) >= 3:
            parts.append(_spline_path(poly, corner_deg))
    return ''.join(parts)


def _labels(rgb, alpha, centers, sigma, min_area):
    lab = _lab(rgb)
    k = len(centers)
    labels = np.stack([((lab - c) ** 2).sum(axis=2) for c in centers]).argmin(axis=0)
    if sigma:
        score = np.stack([cv2.GaussianBlur((labels == i).astype(np.float32), (0, 0), sigma) for i in range(k)])
        labels = score.argmax(axis=0)
    for _ in range(2):
        for i in range(k):
            n, cc, stats, _c = cv2.connectedComponentsWithStats((labels == i).astype(np.uint8), connectivity=4)
            small = [j for j in range(1, n) if stats[j, cv2.CC_STAT_AREA] < min_area]
            if small:
                labels[np.isin(cc, small)] = -2
        hole = labels == -2
        if not hole.any():
            break
        # 小碎块改成离它最近的有效颜色
        valid = (~hole).astype(np.uint8)
        _d, idx = cv2.distanceTransformWithLabels(1 - valid, cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL)
        ys, xs = np.nonzero(valid)
        lut = np.zeros(idx.max() + 1, np.int64)
        lut[idx[ys, xs]] = labels[ys, xs]
        labels[hole] = lut[idx[hole]]
    labels[alpha == 0] = -1
    return labels


def trace(rgb, alpha, centers, eps=1.0, min_area=8.0, sigma=0.8, grow=5, corner_deg=60, base=None, skip=()):
    """rgb: HxWx3 uint8；alpha: HxW uint8（>0 为要画的区域）；centers: palette() 给出的 Lab 色板。

    base: 当底色的色板下标，不给就取最暗的一种。
    skip: 不画的色板下标（比如和页面底色一样的那一种；它当底色时整个底也不画）。
    返回 [(hex, d)]，坐标是乘过 2 的。
    """
    k = len(centers)
    labels = _labels(rgb, alpha, centers, sigma, min_area)
    centers_rgb = cv2.cvtColor(centers.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_LAB2RGB).reshape(-1, 3)
    if base is None:
        base = int(centers[:, 0].argmin())
    areas = np.array([(labels == i).sum() for i in range(k)])
    order = [base] + [int(i) for i in np.argsort(-areas) if i != base]

    def color(i):
        return '#%02x%02x%02x' % tuple(int(v) for v in centers_rgb[i])

    layers = []
    if base not in skip:
        layers.append((color(base), _paths(np.where(alpha > 0, 255, 0).astype(np.uint8), eps, min_area, corner_deg)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (grow, grow))
    for n in range(1, k):
        i = order[n]
        own = labels == i
        if i in skip or not own.any():
            continue
        later = np.isin(labels, order[n + 1:])
        mask = own | ((cv2.dilate(own.astype(np.uint8), kernel) > 0) & later)
        # 填掉只含“之后才画的颜色”的洞
        count, cc = cv2.connectedComponents((~mask).astype(np.uint8), connectivity=4)
        keep_open = np.zeros(count, bool)
        keep_open[np.unique(cc[~mask & ~later])] = True
        mask |= ~keep_open[cc]
        d = _paths(mask.astype(np.uint8) * 255, eps, min_area, corner_deg)
        if d:
            layers.append((color(i), d))
    return layers


def to_group(layers):
    """路径坐标是乘过 2 的，所以外面包一层 scale(.5)。"""
    return '<g transform="scale(.5)">' + ''.join(f'<path fill="{c}" fill-rule="evenodd" d="{d}"/>' for c, d in layers) + '</g>'

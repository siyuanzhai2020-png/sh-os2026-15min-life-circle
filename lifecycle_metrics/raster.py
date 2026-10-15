"""静态地图渲染（PNG）—— 报告与 PDF 的自包含底图。

底图语义：颜色 = 该位置 15 分钟步行可达的**必需要素数量**（不是最短时间）。
时间并集几乎处处都是绿色（总有一个居住组团很近），而"可达要素数量"能直接暴露服务洼地。

用 PIL 直接画，不引入地图瓦片：既能离线出图，也避免报告依赖外部服务。
交互式地图由 web/ 前端用同一份 diagnose.json 渲染。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .geo import M_PER_DEG_LAT, local_xy, m_per_deg_lon
from .schema import Dataset

# (累计比例上界, RGBA, 中文图例, 英文图例)
COVER_BANDS = [
    (0.30, (222, 45, 38, 165), "0–3 项", "0-3"),
    (0.50, (244, 145, 60, 150), "4–5 项", "4-5"),
    (0.70, (250, 215, 100, 140), "6–7 项", "6-7"),
    (0.86, (186, 220, 130, 130), "8–9 项", "8-9"),
    (1.01, (120, 195, 120, 120), "10–11 项（齐备）", "10-11 (full)"),
]

ELEMENT_COLORS: Dict[str, Tuple[int, int, int]] = {
    "market": (214, 39, 40),
    "community_health": (255, 127, 14),
    "canteen": (227, 119, 194),
    "pharmacy": (140, 86, 75),
    "kindergarten": (188, 189, 34),
    "primary_school": (23, 190, 207),
    "culture_center": (148, 103, 189),
    "fitness": (44, 160, 44),
    "park": (76, 175, 80),
    "bus_stop": (31, 119, 180),
    "metro": (0, 60, 130),
    "convenience": (127, 127, 127),
}

ROAD_STYLE = {
    "footway": ((202, 202, 202), 1),
    "residential": ((164, 164, 164), 2),
    "service": ((174, 174, 174), 2),
    "primary": ((118, 118, 118), 3),
    "bridge": ((96, 96, 96), 4),
}

CJK_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]

PANEL_W = 330


def _load_font(size: int) -> Tuple[Any, bool]:
    for path in CJK_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size), True
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default(), False


# ---------------------------------------------------------------------------
# 语义图标（与 web/app.js 的 ICON_GLYPHS 同一套设计语言）：
# 彩色圆底 + 白色图形，让"图例"和"地图上的点"是同一个东西。
# ---------------------------------------------------------------------------
def _icon(draw: ImageDraw.ImageDraw, element: str, cx: float, cy: float, r: float,
          color: Tuple[int, int, int], font: Any = None) -> None:
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color, outline=(255, 255, 255), width=2)
    w = max(1, int(round(r * 0.30)))          # 线宽
    wht = (255, 255, 255)
    k = r / 12.0                              # 以 24×24 图标坐标缩放

    def P(x: float, y: float) -> Tuple[float, float]:
        return cx + (x - 12) * k, cy + (y - 12) * k

    if element == "market":                    # 菜篮
        draw.arc([P(7.8, 5.3), P(16.2, 13.7)], 180, 360, fill=wht, width=w)
        draw.polygon([P(4.2, 10.5), P(19.8, 10.5), P(17.4, 20), P(6.6, 20)], fill=wht)
    elif element == "community_health":        # 医疗十字
        draw.rectangle([P(10.2, 4.5), P(13.8, 19.5)], fill=wht)
        draw.rectangle([P(4.5, 10.2), P(19.5, 13.8)], fill=wht)
    elif element == "canteen":                 # 碗 + 筷
        draw.pieslice([P(4, 4.5), P(20, 20.5)], 0, 180, fill=wht)
        draw.line([P(13.6, 4.4), P(19.4, 10.6)], fill=wht, width=w)
        draw.line([P(16.4, 3.2), P(21, 8.2)], fill=wht, width=w)
    elif element == "pharmacy":                # 胶囊
        draw.rounded_rectangle([P(3.6, 8.6), P(20.4, 15.4)], radius=int(3.4 * k), fill=wht)
        draw.line([P(12, 8.6), P(12, 15.4)], fill=color, width=max(1, w - 1))
    elif element == "kindergarten":            # 气球
        draw.ellipse([P(6, 3.4), P(16.8, 15.8)], fill=wht)
        draw.line([P(11.4, 15.6), P(10.8, 20.6)], fill=wht, width=max(1, w - 1))
    elif element == "primary_school":          # 翻开的书
        draw.polygon([P(3.5, 6.5), P(11, 8), P(11, 19.5), P(3.5, 18)], fill=wht)
        draw.polygon([P(20.5, 6.5), P(13, 8), P(13, 19.5), P(20.5, 18)], fill=wht)
    elif element == "culture_center":          # 音符
        draw.ellipse([P(4.6, 14.3), P(12.6, 20.5)], fill=wht)
        draw.line([P(12.4, 17.2), P(12.4, 4.8)], fill=wht, width=w)
        draw.line([P(12.4, 5.6), P(19.4, 7.8)], fill=wht, width=w)
    elif element == "fitness":                 # 哑铃
        draw.rectangle([P(3.6, 8), P(7.2, 16)], fill=wht)
        draw.rectangle([P(16.8, 8), P(20.4, 16)], fill=wht)
        draw.rectangle([P(6.6, 10.6), P(17.4, 13.4)], fill=wht)
    elif element == "park":                    # 树
        draw.ellipse([P(5.8, 3.6), P(18.2, 16)], fill=wht)
        draw.rectangle([P(10.8, 14.4), P(13.2, 20)], fill=wht)
    elif element == "bus_stop":                # 公交车
        draw.rounded_rectangle([P(4.4, 5.4), P(19.6, 17.6)], radius=int(2 * k), fill=wht)
        draw.rectangle([P(6.6, 8), P(11.2, 12)], fill=color)
        draw.rectangle([P(12.8, 8), P(17.4, 12)], fill=color)
        draw.ellipse([P(6.3, 16.7), P(10.1, 20.5)], fill=wht)
        draw.ellipse([P(13.9, 16.7), P(17.7, 20.5)], fill=wht)
    elif element == "metro":                   # M 标
        if font is not None:
            draw.text((cx, cy), "M", font=font, fill=wht, anchor="mm")
        else:
            draw.rectangle([P(6, 8), P(18, 16)], fill=wht)
    elif element == "convenience":             # 店铺（雨棚 + 门）
        draw.polygon([P(3.8, 6), P(20.2, 6), P(18.6, 11), P(5.4, 11)], fill=wht)
        draw.rectangle([P(5.6, 12), P(18.4, 20)], fill=wht)
        draw.rectangle([P(10.4, 15), P(13.6, 20)], fill=color)
    elif element == "residential":             # 房子
        draw.polygon([P(12, 3.2), P(21.4, 11.4), P(18.6, 11.4), P(18.6, 20.4),
                      P(5.4, 20.4), P(5.4, 11.4), P(2.6, 11.4)], fill=wht)
        draw.rectangle([P(10.2, 14.6), P(13.8, 20.4)], fill=color)
    elif element == "site":                    # 定位图钉
        draw.ellipse([P(5.6, 2.6), P(18.4, 15.4)], fill=wht)
        draw.polygon([P(8.4, 12.6), P(15.6, 12.6), P(12, 21.4)], fill=wht)
        draw.ellipse([P(9.2, 6.2), P(14.8, 11.8)], fill=color)
    else:                                      # 兜底：小圆点
        draw.ellipse([P(8, 8), P(16, 16)], fill=wht)


class _Projector:
    """经纬度 → 像素。y 轴翻转，保证北在上。"""

    def __init__(self, ds: Dataset, size: int, margin: int):
        self.center = ds.community["center"]
        self.half_w = ds.community["extent_m"]["half_width"]
        self.half_h = ds.community["extent_m"]["half_height"]
        self.size = size
        self.margin = margin
        inner = size - 2 * margin
        self.scale = min(inner / (2 * self.half_w), inner / (2 * self.half_h))

    def __call__(self, lon: float, lat: float) -> Tuple[float, float]:
        x, y = local_xy(self.center["lon"], self.center["lat"], lon, lat)
        return (self.margin + (x + self.half_w) * self.scale,
                self.size - self.margin - (y + self.half_h) * self.scale)

    def m(self, meters: float) -> float:
        return meters * self.scale


def band_index(count: int, total: int) -> int:
    if count < 0 or total <= 0:
        return -1
    ratio = count / total
    for i, (upper, *_rest) in enumerate(COVER_BANDS):
        if ratio < upper:
            return i
    return len(COVER_BANDS) - 1


# 步行等时圈分带：5 / 10 / 15 分钟（分钟 → 颜色），未着色即生活圈之外
ISOCHRONE_BANDS = [
    (300, (26, 127, 55, 150), "5 分钟"),
    (600, (53, 176, 160, 140), "10 分钟"),
    (900, (247, 210, 62, 130), "15 分钟"),
]


def time_band_index(seconds: int) -> int:
    if seconds < 0:
        return -1
    for i, (upper, *_rest) in enumerate(ISOCHRONE_BANDS):
        if seconds <= upper:
            return i
    return -1


# 单组团"风圈"：同心分带的填充色（比并集口径更透明，好让底下的路网透出来）
FOCUS_FILLS = [(26, 127, 55, 130), (53, 176, 160, 118), (247, 210, 62, 108)]
FOCUS_LINES = [(20, 96, 42), (32, 128, 116), (176, 146, 20)]
LOST_FILL = (120, 120, 120, 72)          # 理想圆内、实际走不到 → "被路网吃掉的空间"
LOST_LINE = (150, 150, 150, 190)

# 分析范围框：抓取范围比分析范围大（路网/设施外扩 1200 m），底图上必然存在框外的
# 住宅区与设施。不把分析范围画出来，读者无法判断"色块到哪里为止"，也读不懂
# "地图上明明有小区、却没有对应色块"—— 框外居住区不是分析单元。
FRAME_COLOR = (28, 42, 74, 240)


def _dashed_rect(draw: ImageDraw.ImageDraw, box: Tuple[float, float, float, float],
                 color: Tuple[int, int, int, int], width: int = 2,
                 dash: float = 9.0, gap: float = 6.0) -> None:
    """虚线矩形。PIL 没有虚线样式，按边长手动切段（四边独立，转角必落笔）。"""
    x0, y0, x1, y1 = box

    def edge(xa: float, ya: float, xb: float, yb: float) -> None:
        length = math.hypot(xb - xa, yb - ya)
        if length <= 0:
            return
        ux, uy = (xb - xa) / length, (yb - ya) / length
        t = 0.0
        while t < length - 0.5:
            e = min(t + dash, length)
            draw.line([(xa + ux * t, ya + uy * t), (xa + ux * e, ya + uy * e)],
                      fill=color, width=width)
            t = e + gap

    edge(x0, y0, x1, y0)
    edge(x1, y0, x1, y1)
    edge(x1, y1, x0, y1)
    edge(x0, y1, x0, y0)


def draw_analysis_frame(draw: ImageDraw.ImageDraw, proj: Any, ds: Dataset,
                        width: int = 2, inset: int = 0) -> Optional[Tuple[float, float, float, float]]:
    """把"分析范围"画成虚线方框，返回该框的像素坐标 (x0, y0, x1, y1)。

    inset > 0 时向内缩这么多像素 —— 叠加图（GroundOverlay）的框正好压在图像边上，
    不内缩的话描边会被裁掉一半，看起来只是一条图片边界。
    """
    c = ds.community["center"]
    e = ds.community["extent_m"]
    dlon = e["half_width"] / m_per_deg_lon(c["lat"])
    dlat = e["half_height"] / M_PER_DEG_LAT
    x0, y1 = proj(c["lon"] - dlon, c["lat"] - dlat)
    x1, y0 = proj(c["lon"] + dlon, c["lat"] + dlat)
    x0, y0, x1, y1 = x0 + inset, y0 + inset, x1 - inset, y1 - inset
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    _dashed_rect(draw, (x0, y0, x1, y1), FRAME_COLOR, width=width)
    return (x0, y0, x1, y1)


def _label_plate(draw: ImageDraw.ImageDraw, xy: Tuple[float, float], text: str,
                 font: Any, color: Tuple[int, int, int, int],
                 bg: Tuple[int, int, int, int] = (255, 255, 255, 232)) -> None:
    """带底衬的文字：底图上有路网和设施图标，纯文字容易被压住读不出来。"""
    x, y = xy
    try:
        box = draw.textbbox((x, y), text, font=font)
    except AttributeError:                                  # Pillow < 8
        w, h = draw.textsize(text, font=font)
        box = (x, y, x + w, y + h)
    draw.rectangle([box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2], fill=bg)
    draw.text((x, y), text, font=font, fill=color)


class _BoxProjector:
    """经纬度 → 像素，严格铺满分析范围外接框（GL 叠加图用：图必须正好等于 bounds）。

    与 _Projector 的区别：_Projector 用单一 scale 保持等比例（分析框不是正方形时留白），
    而叠加图必须与 bounds 一一对应，所以 x/y 各自拉伸。
    """

    def __init__(self, ds: Dataset, size: int):
        self.center = ds.community["center"]
        self.half_w = float(ds.community["extent_m"]["half_width"])
        self.half_h = float(ds.community["extent_m"]["half_height"])
        self.size = size
        self.sx = size / (2 * self.half_w)
        self.sy = size / (2 * self.half_h)
        self.scale = min(self.sx, self.sy)

    def __call__(self, lon: float, lat: float) -> Tuple[float, float]:
        x, y = local_xy(self.center["lon"], self.center["lat"], lon, lat)
        return ((x + self.half_w) * self.sx, self.size - (y + self.half_h) * self.sy)

    def m(self, meters: float) -> float:
        return meters * self.scale


def overlay_bounds(diag: Dict[str, Any]) -> Dict[str, List[float]]:
    """覆盖图层（栅格）对应的地理范围 —— 与 render_overlay_png 的像素一一对应。"""
    g = diag.get("grid_meta") or {}
    if not g:
        return {"sw": [0.0, 0.0], "ne": [0.0, 0.0]}
    lon0, lat0 = float(g["lon0"]), float(g["lat0"])
    cell_m = float(g.get("cell_m", 40.0))
    mlon = m_per_deg_lon(lat0)
    return {"sw": [round(lon0, 8), round(lat0, 8)],
            "ne": [round(lon0 + int(g["ncols"]) * cell_m / mlon, 8),
                   round(lat0 + int(g["nrows"]) * cell_m / M_PER_DEG_LAT, 8)]}


def focus_overlay_bounds(ds: Dataset) -> Dict[str, List[float]]:
    """等时圈叠加图对应的地理范围（= 分析范围外接框，WGS-84）。"""
    c = ds.community["center"]
    e = ds.community["extent_m"]
    mlon = m_per_deg_lon(c["lat"])
    return {"sw": [round(c["lon"] - e["half_width"] / mlon, 8),
                   round(c["lat"] - e["half_height"] / M_PER_DEG_LAT, 8)],
            "ne": [round(c["lon"] + e["half_width"] / mlon, 8),
                   round(c["lat"] + e["half_height"] / M_PER_DEG_LAT, 8)]}


def _grid_row_index(iso: Dict[str, Any], r: int) -> str:
    return iso["grid"]["rows"][r]


def _focus_cell(iso: Dict[str, Any], r: int, c: int) -> str:
    rows = iso["grid"]["rows"]
    if r < 0 or r >= len(rows):
        return "3"
    row = rows[r]
    if c < 0 or c >= len(row):
        return "3"
    return row[c]


def _draw_focus_fills(draw: ImageDraw.ImageDraw, proj: Any, iso: Dict[str, Any]) -> None:
    """把某组团的 5/10/15 分钟等时圈铺在底图上，并标出"理想圆范围内不可达"的格子。"""
    g = iso["grid"]
    cell_m, ncols, nrows = g["cell_m"], g["ncols"], g["nrows"]
    mlon, mlat = m_per_deg_lon(g["lat0"]), M_PER_DEG_LAT
    hx = (iso["point"]["lon"] - g["lon0"]) * mlon
    hy = (iso["point"]["lat"] - g["lat0"]) * mlat
    r_ideal = iso["compactness"]["radius_15_m"]
    for r in range(nrows):
        row = g["rows"][r]
        for c in range(ncols):
            bi = row[c]
            lon0 = g["lon0"] + c * cell_m / mlon
            lat0 = g["lat0"] + r * cell_m / mlat
            p0 = proj(lon0, lat0)
            p1 = proj(lon0 + cell_m / mlon, lat0 + cell_m / mlat)
            box = [p0[0], p1[1], p1[0], p0[1]]
            if bi in "012":
                draw.rectangle(box, fill=FOCUS_FILLS[int(bi)])
            else:
                # 理想圆内却走不到 → 这块空间是被河道/快速路/断头路吃掉的
                dx = (c + 0.5) * cell_m - hx
                dy = (r + 0.5) * cell_m - hy
                if dx * dx + dy * dy <= r_ideal * r_ideal:
                    draw.rectangle(box, fill=LOST_FILL)


def _draw_focus_lines(draw: ImageDraw.ImageDraw, proj: Any, iso: Dict[str, Any]) -> None:
    """等时线：只在"由内向外"的格边上画，避免同一条线画两遍。"""
    g = iso["grid"]
    cell_m, ncols, nrows = g["cell_m"], g["ncols"], g["nrows"]
    mlon, mlat = m_per_deg_lon(g["lat0"]), M_PER_DEG_LAT
    for r in range(nrows):
        for c in range(ncols):
            bi = _focus_cell(iso, r, c)
            if bi not in "012":
                continue
            lon0 = g["lon0"] + c * cell_m / mlon
            lat0 = g["lat0"] + r * cell_m / mlat
            lon1, lat1 = lon0 + cell_m / mlon, lat0 + cell_m / mlat
            p0, p1 = proj(lon0, lat0), proj(lon1, lat1)
            color = FOCUS_LINES[int(bi)]
            for (nr, nc, seg) in ((r - 1, c, [(p0[0], p0[1]), (p1[0], p0[1])]),      # 南边
                                  (r, c + 1, [(p1[0], p0[1]), (p1[0], p1[1])]),      # 东边
                                  (r + 1, c, [(p0[0], p1[1]), (p1[0], p1[1])]),      # 北边
                                  (r, c - 1, [(p0[0], p0[1]), (p0[0], p1[1])])):     # 西边
                nb = _focus_cell(iso, nr, nc)
                if nb != bi:
                    draw.line(seg, fill=color, width=2)


def _draw_focus_annot(draw: ImageDraw.ImageDraw, proj: Any, ds: Dataset, iso: Dict[str, Any],
                      font: Any, small: Any, cjk: bool, band_labels: bool = True) -> None:
    """圈上刻度：每圈的半径、面积、圈内凑齐了几项服务；理想圆参照；被切断的方向。

    band_labels=False 时不画每圈的刻度标签 —— GL 底图上这些标签由前端用地图 Label 另画
    （位图里的字一缩放就糊），两边都画会重影。
    """
    g = iso["grid"]
    cell_m, ncols, nrows = g["cell_m"], g["ncols"], g["nrows"]
    mlon, mlat = m_per_deg_lon(g["lat0"]), M_PER_DEG_LAT
    point = iso["point"]
    # 1) 理想圆（虚线）：半径 = 步速 × 15 分钟。实际圈比它小多少，就是可达面积比。
    #    半径往往比画布还大，所以标签位置要夹回画布内，否则会画到图外看不见。
    r_ideal = iso["compactness"]["radius_15_m"]
    cx, cy = proj(point["lon"], point["lat"])
    rp = proj.m(r_ideal)
    steps = 96
    pts = [(cx + rp * math.cos(2 * math.pi * i / steps),
            cy - rp * math.sin(2 * math.pi * i / steps)) for i in range(steps + 1)]
    for i in range(0, steps, 2):                       # 手绘虚线（PIL 没有 dash）
        draw.line([pts[i], pts[i + 1]], fill=(110, 110, 110, 200), width=2)
    side = getattr(proj, "size", 1000)
    lx = min(max(cx + rp * 0.66, 70), side - 130)
    ly = min(max(cy - rp * 0.66, 16), side - 40)
    draw.text((lx, ly), f"理想圆 r={r_ideal} m" if cjk else f"ideal r={r_ideal} m",
              font=small, fill=(90, 90, 90))

    # 2) 每圈在北侧边缘打一个刻度标签
    for i, band in enumerate(iso["bands"]):
        if not band_labels:
            break
        rows_with = [r for r in range(nrows) if str(i) in g["rows"][r]]
        if not rows_with:
            continue
        top_r = max(rows_with)
        cols = [c for c in range(ncols) if _focus_cell(iso, top_r, c) == str(i)]
        top_c = cols[len(cols) // 2]
        lon = g["lon0"] + (top_c + 0.5) * cell_m / mlon
        lat = g["lat0"] + (top_r + 1) * cell_m / mlat
        px, py = proj(lon, lat)
        label = (f"{band['label']} {band['radius_m']} m · {band['area_km2']} km² · "
                 f"{band['elements_count']}/{band['elements_total']} 项") if cjk else \
                (f"{band['max_s'] // 60}min {band['elements_count']}/{band['elements_total']}")
        tw = draw.textlength(label, font=small)
        draw.rectangle([px - tw / 2 - 5, py - 11, px + tw / 2 + 5, py + 9],
                       fill=(255, 255, 255, 225), outline=FOCUS_LINES[i], width=2)
        draw.text((px - tw / 2, py), label, font=small, fill=(30, 30, 30))

    # 3) 被切断的方向：从家往外画一小段红箭头 + 半径
    for b in iso.get("blind_directions", [])[:3]:
        ang = math.radians(b["deg"])
        ux, uy = math.sin(ang), math.cos(ang)
        rr = max(proj.m(b["reach_m"]), 6.0)
        ex, ey = cx + ux * rr, cy - uy * rr
        draw.line([(cx, cy), (ex, ey)], fill=(200, 40, 30, 210), width=3)
        draw.ellipse([ex - 5, ey - 5, ex + 5, ey + 5], fill=(200, 40, 30, 230),
                     outline=(255, 255, 255), width=2)
        draw.text((ex + 8, ey - 8), f"{b['label']} {b['reach_m']} m", font=small,
                  fill=(170, 30, 20))

    # 4) 家
    _icon(draw, "residential", cx, cy, 11.0, (11, 46, 89))


def render_focus_overlay_png(ds: Dataset, iso: Dict[str, Any], out_path: str | Path,
                             size: int = 1024) -> Path:
    """单组团等时圈的透明叠加图 —— 给 JSAPI GL 底图当 GroundOverlay。

    和交付给 canvas 模式的矢量格子是**同一份** iso["grid"]，所以两种底图上的圈
    形状、面积、可达面积比完全一致。
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    proj = _BoxProjector(ds, size)
    font, cjk = _load_font(int(size / 46))
    small, _ = _load_font(int(size / 62))
    _draw_focus_fills(d, proj, iso)
    _draw_focus_lines(d, proj, iso)
    draw_analysis_frame(d, proj, ds, width=2, inset=2)
    _draw_focus_annot(d, proj, ds, iso, font, small, cjk, band_labels=False)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG", optimize=True)
    return out


def render_map_png(ds: Dataset, diag: Dict[str, Any], out_path: str | Path,
                   opt: Optional[Dict[str, Any]] = None, size: int = 1100,
                   margin: int = 70, raster: str = "cover",
                   route: Optional[Dict[str, Any]] = None,
                   focus: Optional[Dict[str, Any]] = None) -> Path:
    """体检地图。

    raster="cover"      色块 = 15 分钟内可达的必需要素数量（默认）
    raster="isochrone"  色块 = 步行时间分带（5/10/15 分钟），即等时圈
    route=<一天动线>     额外把某位居民的一天动线画上去（图 3）
    focus=<等时圈>       以某个居住组团为圆心的"风圈"（同心分带 + 圈上刻度 + 可达面积比），
                        传了它就盖掉全社区并集口径的分带（两者含义不同，不能叠着看）
    """
    proj = _Projector(ds, size, margin)
    canvas_w = size + PANEL_W
    base = Image.new("RGBA", (canvas_w, size), (255, 255, 255, 255))
    base.paste(Image.new("RGBA", (size, size), (247, 247, 244, 255)), (0, 0))

    # 1) 底图色块
    gc = diag.get("grid_counts") or {}
    gt = diag.get("grid_times") or {}
    counts = gc.get("counts")
    times = gt.get("times")
    grid = diag.get("grid")
    total_req = gc.get("required_total") or 0
    use_times = raster == "isochrone" and times and not focus
    cells = (times if use_times else counts) if not focus else None
    if focus:
        # 单组团风圈：填充与等时线铺在路网下面（半透明合成，不能直接盖）
        overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        _draw_focus_fills(od, proj, focus)
        _draw_focus_lines(od, proj, focus)
        base.paste(Image.alpha_composite(base.crop((0, 0, size, size)), overlay), (0, 0))
    elif cells and grid is not None:
        overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        mlon, mlat = m_per_deg_lon(grid.lat0), M_PER_DEG_LAT
        for r in range(grid.nrows):
            for c in range(grid.ncols):
                bi = (time_band_index(cells[r][c]) if use_times
                      else band_index(cells[r][c], total_req))
                if bi < 0:
                    continue
                fill = ISOCHRONE_BANDS[bi][1] if use_times else COVER_BANDS[bi][1]
                lon0 = grid.lon0 + c * grid.cell_m / mlon
                lat0 = grid.lat0 + r * grid.cell_m / mlat
                p0 = proj(lon0, lat0)
                p1 = proj(lon0 + grid.cell_m / mlon, lat0 + grid.cell_m / mlat)
                od.rectangle([p0[0], p1[1], p1[0], p0[1]], fill=fill)
        base.paste(Image.alpha_composite(base.crop((0, 0, size, size)), overlay), (0, 0))
    draw = ImageDraw.Draw(base)

    # 2) 河道
    river = ds.meta.get("river") or []
    if len(river) >= 2:
        pts = [proj(p[0], p[1]) for p in river]
        w = max(8, int(proj.m(26)))
        draw.line(pts, fill=(150, 200, 226), width=w)
        draw.line(pts, fill=(118, 178, 214), width=max(2, w // 5))

    # 3) 路网
    coord = {n.id: (n.lon, n.lat) for n in ds.nodes}
    for kind in ("footway", "residential", "service", "primary", "bridge"):
        color, width = ROAD_STYLE[kind]
        px_width = max(1, int(round(width * proj.scale * 1.5)))
        for e in ds.edges:
            if e.kind != kind:
                continue
            cu, cv = coord.get(e.u), coord.get(e.v)
            if not cu or not cv:
                continue
            draw.line([proj(*cu), proj(*cv)], fill=color, width=px_width)

    label_font, cjk = _load_font(16)
    small_font, _ = _load_font(14)
    metro_font, _ = _load_font(11)

    # 3.5) 分析范围框：抓取范围外扩 1200 m（只补路网与设施），底图上框外仍有住宅区与
    #      设施 POI。画出分析边界，读者才知道色块与统计口径到哪里为止。
    frame = draw_analysis_frame(draw, proj, ds, width=2)
    if frame:
        e = ds.community["extent_m"]
        _label_plate(draw, (frame[0] + 9, frame[1] + 8),
                     f"分析范围 {2 * e['half_width'] / 1000:.2f}×{2 * e['half_height'] / 1000:.2f} km"
                     "（框外不参与体检）", small_font, FRAME_COLOR)

    # 4) 设施：语义图标（与前端同一套：菜篮/十字/碗筷/胶囊/气球/书/音符/哑铃/树/公交/M/店铺）
    for f in ds.facilities:
        x, y = proj(f.lon, f.lat)
        _icon(draw, f.element, x, y, 8.0, ELEMENT_COLORS.get(f.element, (90, 90, 90)), metro_font)

    # 5) 居住组团：房子图标（绿=要素齐备，红=存在短板），大小随户数
    rp_map = {r.id: r for r in ds.residential}
    for p in diag["points"]:
        rp = rp_map.get(p["id"])
        if rp is None:
            continue
        x, y = proj(rp.lon, rp.lat)
        rad = max(7.0, min(13.0, 7 + p["households"] / 500.0))
        fill = (46, 160, 67) if p["compliance_score"] >= 0.999 else (214, 45, 32)
        _icon(draw, "residential", x, y, rad, fill)

    # 6) 一天动线（可选）：沿路网的折线 + 序号停靠点 + 出发点
    if route and route.get("stops"):
        segs = [s.get("leg_coords") for s in route["stops"]]
        if route.get("home_leg"):
            segs.append(route["home_leg"].get("leg_coords"))
        color = (46, 160, 67) if route.get("mode") == "after" else (31, 119, 180)
        for coords in segs:
            if not coords or len(coords) < 2:
                continue
            pts = [proj(c[0], c[1]) for c in coords]
            draw.line(pts, fill=(255, 255, 255), width=6, joint="curve")
            draw.line(pts, fill=color, width=3, joint="curve")
        home = proj(route["point"]["lon"], route["point"]["lat"])
        _icon(draw, "residential", home[0], home[1], 10.0, (11, 46, 89))
        for s in route["stops"]:
            x, y = proj(s["lon"], s["lat"])
            draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill=color, outline=(255, 255, 255), width=2)
            draw.text((x, y), str(s["seq"]), font=metro_font, fill=(255, 255, 255), anchor="mm")

    # 7) 推荐新增点位：定位图钉
    if opt and opt.get("sites"):
        for i, s in enumerate(opt["sites"], start=1):
            x, y = proj(s["lon"], s["lat"])
            _icon(draw, "site", x, y - 3, 11.0, (255, 145, 0))
            draw.text((x + 13, y - 6), f"P{i}", font=label_font, fill=(140, 60, 0))

    # 8) 风圈刻度（最上层）：圈上标签、理想圆参照、被切断的方向、家
    if focus:
        annot = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ad = ImageDraw.Draw(annot)
        _draw_focus_annot(ad, proj, ds, focus, label_font, small_font, cjk)
        base.paste(Image.alpha_composite(base.crop((0, 0, size, size)), annot), (0, 0))
        draw = ImageDraw.Draw(base)

    _draw_side_panel(draw, ds, diag, opt, label_font, small_font, cjk, size,
                     raster=raster, route=route, focus=focus)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out, format="PNG", optimize=True)
    return out


def render_overlay_png(diag: Dict[str, Any], ds: Dataset, out_path: str | Path,
                       raster: str = "cover", px_per_cell: int = 10) -> Tuple[Path, Dict[str, Any]]:
    """只画"色块"的透明底 PNG + 地理范围 —— 给 JSAPI 底图当 GroundOverlay 用。

    为什么这么做：GL 地图上没法直接铺 1600 个多边形格子（会卡），但用户可以期望
    看到和"体检底图"一样的服务覆盖色块。把同一份栅格在服务端渲染成一张透明 PNG，
    按分析范围贴到地图上，既便宜又和 canvas 模式一致。
    """
    grid_meta = diag.get("grid_meta") or {}
    gc = diag.get("grid_counts") or {}
    gt = diag.get("grid_times") or {}
    counts = gc.get("counts")
    times = gt.get("times")
    use_times = raster == "isochrone" and times
    cells = times if use_times else counts
    nrows, ncols = int(grid_meta.get("nrows", 0)), int(grid_meta.get("ncols", 0))
    if not cells or not nrows or not ncols:
        raise RuntimeError("该社区没有栅格数据，无法生成覆盖图层")
    total_req = gc.get("required_total") or 0
    img = Image.new("RGBA", (ncols * px_per_cell, nrows * px_per_cell), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for r in range(nrows):
        for c in range(ncols):
            bi = (time_band_index(cells[r][c]) if use_times
                  else band_index(cells[r][c], total_req))
            if bi < 0:
                continue
            fill = ISOCHRONE_BANDS[bi][1] if use_times else COVER_BANDS[bi][1]
            # 注意：图像 y 轴向下、栅格 r 从南往北，所以这里要翻转
            y0 = (nrows - 1 - r) * px_per_cell
            d.rectangle([c * px_per_cell, y0, c * px_per_cell + px_per_cell - 1,
                         y0 + px_per_cell - 1], fill=fill)
    # 分析范围框：叠加图正好铺满分析范围，框线贴边会被裁掉一半 → 内缩 2 px
    _dashed_rect(d, (2, 2, img.width - 3, img.height - 3), FRAME_COLOR, width=2)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG", optimize=True)

    cell_m = float(grid_meta.get("cell_m", 40.0))
    lon0, lat0 = float(grid_meta["lon0"]), float(grid_meta["lat0"])
    mlon = m_per_deg_lon(lat0)
    sw = [lon0, lat0]
    ne = [lon0 + ncols * cell_m / mlon, lat0 + nrows * cell_m / M_PER_DEG_LAT]
    return out, {
        "bounds_wgs84": {"sw": [round(v, 8) for v in sw], "ne": [round(v, 8) for v in ne]},
        "raster": "isochrone" if use_times else "cover",
        "px": [img.width, img.height],
        "legend": ([{"label": b[2], "color": "#%02x%02x%02x" % b[1][:3]} for b in ISOCHRONE_BANDS]
                   if use_times else
                   [{"label": b[2], "color": "#%02x%02x%02x" % b[1][:3]} for b in COVER_BANDS]),
        "note": "透明 PNG，按分析范围贴合到地图上；色块口径与体检底图完全一致。",
    }


def render_route_png(ds: Dataset, diag: Dict[str, Any], route: Dict[str, Any],
                     out_path: str | Path, size: int = 1100, margin: int = 70) -> Path:
    """单独出一张"一天动线"图（报告图 3 / 前端导出）。

    用与体检图同一套投影与图标，只是底图换成等时圈（更贴合"走了多远"的直觉）。
    """
    return render_map_png(ds, diag, out_path, opt=None, size=size, margin=margin,
                          raster="isochrone", route=route)


def _draw_side_panel(draw: ImageDraw.ImageDraw, ds: Dataset, diag: Dict[str, Any],
                     opt: Optional[Dict[str, Any]], font: Any, small: Any, cjk: bool,
                     size: int, raster: str = "cover",
                     route: Optional[Dict[str, Any]] = None,
                     focus: Optional[Dict[str, Any]] = None) -> None:
    x0 = size + 14
    draw.line([(size + 4, 10), (size + 4, size - 10)], fill=(210, 210, 210), width=2)
    y = 26
    draw.text((x0, y), "生活圈体检图" if cjk else "Community check-up map",
              font=font, fill=(20, 20, 20))
    y += 26
    if focus:
        p = focus["point"]
        draw.text((x0, y), f"风圈：以「{p['name']}」为圆心" if cjk else "isochrone rings",
                  font=small, fill=(90, 90, 90))
        y += 24
        for i, b in enumerate(focus["bands"]):
            draw.rectangle([x0, y + 2, x0 + 18, y + 16], fill=FOCUS_FILLS[i][:3],
                           outline=FOCUS_LINES[i])
            txt = (f"{b['label']} r={b['radius_m']}m · {b['area_km2']}km² · "
                   f"{b['elements_count']}/{b['elements_total']}项") if cjk else \
                  (f"{b['max_s'] // 60}min {b['area_km2']}km2")
            draw.text((x0 + 26, y), txt, font=small, fill=(45, 45, 45))
            y += 22
        cp = focus["compactness"]
        draw.rectangle([x0, y + 2, x0 + 18, y + 16], fill=LOST_FILL[:3], outline=(150, 150, 150))
        draw.text((x0 + 26, y), f"受路网折减 {cp['lost_km2']} km²" if cjk
                  else f"lost {cp['lost_km2']} km2", font=small, fill=(90, 90, 90))
        y += 22
        draw.text((x0, y), (f"可达面积比 {cp['ratio'] * 100:.0f}%（理想圆 {cp['ideal_km2']} km²，"
                            f"步速 {focus['walk_speed_mps']} m/s）") if cjk
                  else f"compactness {cp['ratio'] * 100:.0f}%", font=small, fill=(45, 45, 45))
        y += 20
        bl = focus.get("blind_directions") or []
        if bl:
            names = "、".join(f"{b['label']} {b['reach_m']}m" for b in bl[:3])
            draw.text((x0, y), (f"被切断的方向：{names}" if cjk else "cut-off directions"),
                      font=small, fill=(170, 30, 20))
            y += 20
        if cjk:
            draw.text((x0, y), "（虚线圆=同半径理想圆；红箭头=走不通的方向）",
                      font=small, fill=(120, 120, 120))
        y += 28
    elif raster == "isochrone":
        draw.text((x0, y), "底图：步行时间分带（任一组团出发）" if cjk else "walk-time isochrones",
                  font=small, fill=(90, 90, 90))
        y += 26
        for upper, color, zh in ISOCHRONE_BANDS:
            draw.rectangle([x0, y + 2, x0 + 18, y + 16], fill=color[:3], outline=(140, 140, 140))
            draw.text((x0 + 26, y), f"{zh}内" if cjk else f"<={upper // 60} min",
                      font=small, fill=(45, 45, 45))
            y += 22
        if cjk:
            draw.text((x0, y), "（未着色 = 15 分钟内不可达）", font=small, fill=(120, 120, 120))
        y += 30
    else:
        draw.text((x0, y), "底图：15 分钟可达必需要素数" if cjk else "elements reachable in 15 min",
                  font=small, fill=(90, 90, 90))
        y += 26
        total = (diag.get("grid_counts") or {}).get("required_total", 0)
        for upper, color, zh, en in COVER_BANDS:
            draw.rectangle([x0, y + 2, x0 + 18, y + 16], fill=color[:3], outline=(140, 140, 140))
            label = zh if cjk else en
            draw.text((x0 + 26, y), label, font=small, fill=(45, 45, 45))
            y += 22
        if cjk:
            draw.text((x0, y), f"（共 {total} 项必需要素）", font=small, fill=(120, 120, 120))
        y += 30

    markers = [((46, 160, 67), "residential", "达标居住组团", "compliant block"),
               ((214, 45, 32), "residential", "存在短板组团", "block with gaps")]
    if any(p.get("assessable") is False for p in diag.get("points", [])):
        markers.append(((154, 160, 166), "residential", "路网未连通（无法评估）", "not assessable"))
    if route and route.get("stops"):
        markers.append(((31, 119, 180), "site", "一天动线（序号=到访顺序）", "day route"))
    if opt and opt.get("sites"):
        markers.append(((255, 145, 0), "site", "推荐新增点位", "recommended site"))
    for color, icon_key, zh, en in markers:
        _icon(draw, icon_key, x0 + 11, y + 11, 10.0, color)
        draw.text((x0 + 28, y), zh if cjk else en, font=small, fill=(45, 45, 45))
        y += 24
    y += 12

    draw.text((x0, y), "设施类型" if cjk else "facility types", font=font, fill=(20, 20, 20))
    y += 24
    for kind in ("primary", "residential", "footway", "bridge"):
        color, width = ROAD_STYLE[kind]
        draw.line([(x0 + 2, y + 8), (x0 + 20, y + 8)], fill=color, width=max(1, width))
        zh = {"primary": "主干路", "residential": "支路", "footway": "内部步道", "bridge": "桥梁"}[kind]
        en = {"primary": "primary", "residential": "local street",
              "footway": "footpath", "bridge": "bridge"}[kind]
        draw.text((x0 + 26, y), zh if cjk else en, font=small, fill=(45, 45, 45))
        y += 20
    if ds.meta.get("river"):
        draw.line([(x0 + 2, y + 8), (x0 + 20, y + 8)], fill=(140, 195, 224), width=6)
        draw.text((x0 + 26, y), "生活性河道" if cjk else "canal", font=small, fill=(45, 45, 45))
        y += 20
    y += 8

    key_by_name = {e["key"]: e["name"] for e in diag["elements"]}
    metro_font, _ = _load_font(10)
    for key in sorted({f.element for f in ds.facilities}):
        _icon(draw, key, x0 + 10, y + 10, 9.0, ELEMENT_COLORS.get(key, (90, 90, 90)), metro_font)
        name = key_by_name.get(key, key) if cjk else key
        if len(name) > 13:
            name = name[:12] + "…"
        draw.text((x0 + 24, y + 1), name, font=small, fill=(45, 45, 45))
        y += 22

    s = diag["summary"]
    y = size - 96
    for line in [
        f"综合得分 {s['composite_score']}（{s['grade']}）" if cjk else f"score {s['composite_score']}",
        f"{s['residential_points']} 个组团 / {s['households']:,} 户 / {s['population']:,} 人" if cjk
        else f"{s['households']} households",
        f"15 分钟生活圈 {s['circle_area_km2']} km²",
        f"数据源：{ds.meta.get('source', '?')}" if cjk else f"source: {ds.meta.get('source', '?')}",
    ]:
        draw.text((x0, y), line, font=small, fill=(70, 70, 70))
        y += 20

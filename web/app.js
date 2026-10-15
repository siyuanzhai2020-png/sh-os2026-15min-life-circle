/* 社区生活圈体检 Demo —— 纯前端渲染，不依赖外部 CDN（离线可用）。
   数据全部来自 /api/bundle；百度地图底图为可选增强（需要 Web 端 AK）。 */

/* 设施配色：同类目内尽量拉开色相；再叠加形状编码（基础保障=圆 / 便利服务=方 / 品质提升=三角） */
const ELEMENT_COLORS = {
  // 基础保障（圆）
  market: '#d62728', community_health: '#ff7f0e', canteen: '#e377c2',
  kindergarten: '#bcbd22', primary_school: '#17becf', bus_stop: '#1f77b4',
  // 便利服务（方）
  pharmacy: '#8c564b', convenience: '#9aa0a6',
  // 品质提升（三角）
  culture_center: '#9467bd', fitness: '#2ca02c', park: '#00b3a4', metro: '#0b2e59',
};
const SHAPE_BY_CATEGORY = { '基础保障': 'circle', '便利服务': 'square', '品质提升': 'triangle' };

/* ------------------------------------------------------------------
   语义图标：每个要素一个可识别的图形（24×24 坐标系内绘制，白色线条/填充）。
   地图标记与图例共用同一套图标 —— 图例里看到什么，地图上就是什么。
   ------------------------------------------------------------------ */
const ICON_GLYPHS = {
  // 菜市场：购物篮（提手 + 梯形篮身 + 两道横线）
  market(c) {
    c.beginPath(); c.arc(12, 9.5, 4.2, Math.PI, 0); c.stroke();          // 提手
    c.beginPath(); c.moveTo(4.2, 10.5); c.lineTo(19.8, 10.5);
    c.lineTo(17.4, 20); c.lineTo(6.6, 20); c.closePath(); c.fill();       // 篮身
    c.strokeStyle = 'rgba(0,0,0,.35)'; c.lineWidth = 1.1;
    c.beginPath(); c.moveTo(7.6, 14); c.lineTo(16.6, 14);
    c.moveTo(8.2, 17); c.lineTo(16, 17); c.stroke();
  },
  // 社区卫生服务站：医疗十字
  community_health(c) {
    c.fillRect(10.2, 4.5, 3.6, 15);
    c.fillRect(4.5, 10.2, 15, 3.6);
  },
  // 社区食堂：碗 + 筷子
  canteen(c) {
    c.beginPath(); c.moveTo(4, 12.5); c.lineTo(20, 12.5);
    c.arc(12, 12.5, 8, 0, Math.PI); c.closePath(); c.fill();
    c.lineWidth = 1.6; c.beginPath();
    c.moveTo(13.6, 4.4); c.lineTo(19.4, 10.6);                            // 筷子
    c.moveTo(16.4, 3.2); c.lineTo(21, 8.2); c.stroke();
  },
  // 药店：胶囊
  pharmacy(c) {
    c.save(); c.translate(12, 12); c.rotate(-Math.PI / 4);
    const r = 3.4;
    c.beginPath();
    c.moveTo(-7.4, -r); c.lineTo(7.4, -r); c.arc(7.4, 0, r, -Math.PI / 2, Math.PI / 2);
    c.lineTo(-7.4, r); c.arc(-7.4, 0, r, Math.PI / 2, -Math.PI / 2); c.closePath(); c.fill();
    c.strokeStyle = 'rgba(0,0,0,.35)'; c.lineWidth = 1.1;
    c.beginPath(); c.moveTo(0, -r); c.lineTo(0, r); c.stroke();
    c.restore();
  },
  // 幼儿园：气球
  kindergarten(c) {
    c.beginPath(); c.ellipse(11.4, 9.6, 5.4, 6.2, 0, 0, Math.PI * 2); c.fill();
    c.lineWidth = 1.4; c.beginPath();
    c.moveTo(11.4, 15.6); c.quadraticCurveTo(13.6, 18.4, 10.8, 20.6); c.stroke();
  },
  // 小学：翻开的书
  primary_school(c) {
    c.beginPath(); c.moveTo(3.5, 6.5); c.lineTo(11, 8); c.lineTo(11, 19.5);
    c.lineTo(3.5, 18); c.closePath(); c.fill();
    c.beginPath(); c.moveTo(20.5, 6.5); c.lineTo(13, 8); c.lineTo(13, 19.5);
    c.lineTo(20.5, 18); c.closePath(); c.fill();
  },
  // 社区文化活动中心：音符
  culture_center(c) {
    c.beginPath(); c.ellipse(8.6, 17.4, 4, 3.1, -0.35, 0, Math.PI * 2); c.fill();
    c.lineWidth = 1.7; c.beginPath();
    c.moveTo(12.4, 17.2); c.lineTo(12.4, 4.8); c.lineTo(19.4, 7.2); c.stroke();
  },
  // 健身点/步道/球场：哑铃
  fitness(c) {
    c.fillRect(3.6, 8, 3.6, 8);
    c.fillRect(16.8, 8, 3.6, 8);
    c.fillRect(6.6, 10.6, 10.8, 2.8);
  },
  // 公园绿地：树
  park(c) {
    c.beginPath(); c.arc(12, 9.8, 6.2, 0, Math.PI * 2); c.fill();
    c.fillRect(10.8, 14.4, 2.4, 5.6);
  },
  // 公交站点：公交车
  bus_stop(c) {
    c.beginPath(); c.roundRect(4.4, 5.4, 15.2, 12.2, 2); c.fill();
    c.fillStyle = 'rgba(0,0,0,.35)';
    c.fillRect(6.6, 8, 4.6, 4); c.fillRect(12.8, 8, 4.6, 4);
    c.fillStyle = '#fff';
    c.beginPath(); c.arc(8.2, 18.6, 1.8, 0, Math.PI * 2);
    c.arc(15.8, 18.6, 1.8, 0, Math.PI * 2); c.fill();
  },
  // 轨道交通：M 标（地铁站通用标识）
  metro(c) {
    c.font = '700 17px system-ui, sans-serif';
    c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillText('M', 12, 13);
  },
  // 便利店：店铺门面（雨棚 + 门）
  convenience(c) {
    c.beginPath(); c.moveTo(3.8, 6); c.lineTo(20.2, 6); c.lineTo(18.6, 11);
    c.lineTo(5.4, 11); c.closePath(); c.fill();                            // 雨棚
    c.fillRect(5.6, 12, 12.8, 8);                                          // 店面
    c.fillStyle = 'rgba(0,0,0,.35)'; c.fillRect(10.4, 15, 3.2, 5);          // 门
  },
  // 居住组团：房子（用户点名要的）
  residential(c) {
    c.beginPath(); c.moveTo(12, 3.6); c.lineTo(21, 11.4); c.lineTo(18.6, 11.4);
    c.lineTo(18.6, 20.2); c.lineTo(5.4, 20.2); c.lineTo(5.4, 11.4);
    c.lineTo(3, 11.4); c.closePath(); c.fill();
    c.fillStyle = 'rgba(0,0,0,.3)'; c.fillRect(10.2, 14.6, 3.6, 5.6);
  },
  // 推荐点位：定位图钉
  site(c) {
    c.beginPath();
    c.moveTo(12, 21.5);
    c.bezierCurveTo(4.6, 13.2, 5.6, 3.5, 12, 3.5);
    c.bezierCurveTo(18.4, 3.5, 19.4, 13.2, 12, 21.5);
    c.closePath(); c.fill();
    c.fillStyle = 'rgba(0,0,0,.30)';
    c.beginPath(); c.arc(12, 10, 3.2, 0, Math.PI * 2); c.fill();
  },
};

const iconCache = new Map();

/** 生成（并缓存）某个图标的位图：彩色圆底 + 白色语义图形。 */
function iconCanvas(key, color, px = 48) {
  const cacheKey = `${key}|${color}|${px}`;
  if (iconCache.has(cacheKey)) return iconCache.get(cacheKey);
  const cv = document.createElement('canvas');
  cv.width = cv.height = px;
  const c = cv.getContext('2d');
  const s = px / 24;
  c.scale(s, s);
  c.beginPath(); c.arc(12, 12, 11, 0, Math.PI * 2);
  c.fillStyle = color; c.fill();
  c.lineWidth = 1.4; c.strokeStyle = 'rgba(255,255,255,.95)'; c.stroke();
  c.fillStyle = '#fff'; c.strokeStyle = '#fff';
  c.lineWidth = 1.8; c.lineCap = 'round'; c.lineJoin = 'round';
  const glyph = ICON_GLYPHS[key];
  if (glyph) glyph(c);
  else { c.beginPath(); c.arc(12, 12, 4, 0, Math.PI * 2); c.fill(); }
  iconCache.set(cacheKey, cv);
  return cv;
}

const iconDataUrl = (key, color, px = 34) => iconCanvas(key, color, px).toDataURL();

/* 可达要素数量分带：红→橙→黄→青绿→深绿，色相拉满，避免"浅绿/深绿"分不清 */
const COVER_BANDS = [
  { max: 0.30, fill: 'rgba(178,24,43,0.66)', css: '#b2182b', label: '0–3 项' },
  { max: 0.50, fill: 'rgba(239,108,26,0.62)', css: '#ef6c1a', label: '4–5 项' },
  { max: 0.70, fill: 'rgba(247,210,62,0.58)', css: '#f7d23e', label: '6–7 项' },
  { max: 0.86, fill: 'rgba(53,176,160,0.55)', css: '#35b0a0', label: '8–9 项' },
  { max: 1.01, fill: 'rgba(26,127,55,0.52)', css: '#1a7f37', label: '10–11 项' },
];
const ROAD_STYLE = {
  footway: { color: '#b9b9b9', width: 1 },
  residential: { color: '#8f8f8f', width: 1.6 },
  service: { color: '#9c9c9c', width: 1.6 },
  primary: { color: '#5f5f5f', width: 2.6 },
  bridge: { color: '#4a4a4a', width: 3.4 },
};
const ROAD_ORDER = ['footway', 'residential', 'service', 'primary', 'bridge'];

/* 步行等时圈分带（与 lifecycle_metrics/raster.py::ISOCHRONE_BANDS 保持一致） */
const ISO_BANDS = [
  { max: 300, fill: 'rgba(26,127,55,0.55)', css: '#1a7f37', label: '5 分钟内' },
  { max: 600, fill: 'rgba(53,176,160,0.52)', css: '#35b0a0', label: '10 分钟内' },
  { max: 900, fill: 'rgba(247,210,62,0.50)', css: '#f7d23e', label: '15 分钟内' },
];
const isoBand = (sec) => {
  if (sec == null || sec < 0) return -1;
  for (let i = 0; i < ISO_BANDS.length; i++) if (sec <= ISO_BANDS[i].max) return i;
  return -1;
};

function drawMarker(ctx, x, y, shape, color, size) {
  ctx.fillStyle = color;
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  if (shape === 'square') {
    ctx.rect(x - size, y - size, size * 2, size * 2);
  } else if (shape === 'triangle') {
    ctx.moveTo(x, y - size * 1.25);
    ctx.lineTo(x + size * 1.15, y + size * 0.9);
    ctx.lineTo(x - size * 1.15, y + size * 0.9);
    ctx.closePath();
  } else {
    ctx.arc(x, y, size, 0, Math.PI * 2);
  }
  ctx.fill();
  ctx.stroke();
}

function shapeOf(key, elementsCfg) {
  const el = ((elementsCfg || {}).elements || []).find((e) => e.key === key);
  return SHAPE_BY_CATEGORY[(el || {}).category] || 'circle';
}

const state = {
  bundle: null, opt: null, ak: null, mode: 'canvas', bmap: null,
  communities: [],        // /api/communities 的原始列表（含 ready：有没有本地数据集）
  layers: { basemap: true, cover: true, roads: true, footway: false, poi: true, resid: true, sites: true },
  basemap: null,          // {img, center, mpp, size} 服务端静态底图
  view: { k: 1, tx: 0, ty: 0 },   // canvas 缩放/平移
  coverMode: 'before',    // 覆盖图层：现状 / 优化后
  raster: 'cover',        // 底图口径：可达要素数量 / 步行等时圈
  hit: [], hoverKey: null,
  route: null,            // 一天动线：{pointId, data, mode, order, elements, stay, start, playing, t}
  panelMode: 'checkup',   // 面板模式：体检 / 规划（"地图上做决策，报告里做论证"）
  personas: [],           // 典型住户画像（来自 /api/config）
  personaNotes: {},
  persona: 'elder_active',// 当前画像 key
  iso: null,              // 单组团等时圈（风圈）：{pointId, data}
  isoLoading: null,
  canvasSize: { w: 900, h: 900 },
};

/** 风圈配色（与 lifecycle_metrics/raster.py::FOCUS_FILLS 保持一致） */
const ISO_FILLS = ['rgba(26,127,55,0.62)', 'rgba(53,176,160,0.56)', 'rgba(247,210,62,0.50)'];
const ISO_LINES = ['#14602a', '#208074', '#b09214'];
const LOST_FILL = 'rgba(110,110,116,0.26)';

const personaOf = (key) => (state.personas || []).find((p) => p.key === key) || null;

/** 体检 / 规划 双模式：切模式时整组卡片换一套，省掉"十几张卡一起滚"。 */
function applyPanelMode() {
  document.querySelectorAll('.panel-modes button').forEach((b) =>
    b.classList.toggle('on', b.dataset.pmode === state.panelMode));
  document.querySelectorAll('.panel [data-pg]').forEach((el) => {
    el.hidden = el.dataset.pg !== state.panelMode;
  });
  const plan = state.panelMode === 'plan';
  $('mapHint').textContent = plan
    ? '规划：① 选一类住户 → ② 点地图上的房子，看他的 15 分钟圈和一天动线'
    : '';
}

/** 当前生效的诊断结果：优化后模式下用 after_diag（把推荐点位算进去）。 */
function activeDiag() {
  const b = state.bundle;
  if (!b) return null;
  const after = state.opt && state.opt.after_diag;
  return (state.coverMode === 'after' && after) ? after : b.diag;
}

/* WGS-84 → BD-09（百度坐标系）。数据集统一 WGS-84，画到百度地图上必须先换算。 */
const X_PI = (Math.PI * 3000) / 180;
const PI = Math.PI, AA = 6378245.0, EE = 0.00669342162296594323;
function outOfChina(lon, lat) { return !(lon > 73.66 && lon < 135.05 && lat > 3.86 && lat < 53.55); }
function transformLat(x, y) {
  let r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  r += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
  r += (20 * Math.sin(y * PI) + 40 * Math.sin((y / 3) * PI)) * 2 / 3;
  r += (160 * Math.sin((y / 12) * PI) + 320 * Math.sin((y * PI) / 30)) * 2 / 3;
  return r;
}
function transformLon(x, y) {
  let r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  r += (20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2 / 3;
  r += (20 * Math.sin(x * PI) + 40 * Math.sin((x / 3) * PI)) * 2 / 3;
  r += (150 * Math.sin((x / 12) * PI) + 300 * Math.sin((x / 30) * PI)) * 2 / 3;
  return r;
}
function wgs84ToGcj02(lon, lat) {
  if (outOfChina(lon, lat)) return [lon, lat];
  let dLat = transformLat(lon - 105, lat - 35), dLon = transformLon(lon - 105, lat - 35);
  const radLat = (lat / 180) * PI;
  let magic = Math.sin(radLat); magic = 1 - EE * magic * magic;
  const sqrtMagic = Math.sqrt(magic);
  dLat = (dLat * 180) / (((AA * (1 - EE)) / (magic * sqrtMagic)) * PI);
  dLon = (dLon * 180) / ((AA / sqrtMagic) * Math.cos(radLat) * PI);
  return [lon + dLon, lat + dLat];
}
function wgs84ToBd09(lon, lat) {
  const g = wgs84ToGcj02(lon, lat);
  const z = Math.sqrt(g[0] * g[0] + g[1] * g[1]) + 0.00002 * Math.sin(g[1] * X_PI);
  const theta = Math.atan2(g[1], g[0]) + 0.000003 * Math.cos(g[0] * X_PI);
  return [z * Math.cos(theta) + 0.0065, z * Math.sin(theta) + 0.006];
}
const bdPoint = (BMapGL, lon, lat) => {
  const p = wgs84ToBd09(lon, lat);
  return new BMapGL.Point(p[0], p[1]);
};

const $ = (id) => document.getElementById(id);
const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' };
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ESC_MAP[c]);
const pct = (v) => (v === null || v === undefined ? '—' : (v * 100).toFixed(1) + '%');
const bandIndex = (count, total) => {
  if (count < 0 || !total) return -1;
  const r = count / total;
  for (let i = 0; i < COVER_BANDS.length; i++) if (r < COVER_BANDS[i].max) return i;
  return COVER_BANDS.length - 1;
};

function makeProjector(bundle, w, h) {
  const c = bundle.community.center, e = bundle.community.extent_m;
  const mlon = 111320 * Math.cos((c.lat * Math.PI) / 180), mlat = 111320;
  const margin = 24;
  const scale = Math.min((w - 2 * margin) / (2 * e.half_width), (h - 2 * margin) / (2 * e.half_height));
  const proj = (lon, lat) => ({
    x: margin + ((lon - c.lon) * mlon + e.half_width) * scale,
    y: h - margin - ((lat - c.lat) * mlat + e.half_height) * scale,
  });
  proj.scale = scale;
  proj.cell = (m) => m * scale;
  return proj;
}

// 分析范围的四角（WGS-84）—— 抓取范围比它大（外扩 1200 m），底图上框外仍有住宅区与设施
function analysisRing(bundle) {
  const c = bundle.community.center, e = bundle.community.extent_m;
  const mlon = 111320 * Math.cos((c.lat * Math.PI) / 180), mlat = 111320;
  const dx = e.half_width / mlon, dy = e.half_height / mlat;
  return [[c.lon - dx, c.lat - dy], [c.lon + dx, c.lat - dy],
          [c.lon + dx, c.lat + dy], [c.lon - dx, c.lat + dy]];
}

// 虚线分析范围框（canvas 模式）。线宽/虚线长度按 1/k 反向缩放，屏幕上尺寸恒定。
function drawAnalysisFrame(ctx, proj, inv, bundle) {
  if (!bundle) return;
  const ring = analysisRing(bundle).map((p) => proj(p[0], p[1]));
  ctx.save();
  ctx.strokeStyle = 'rgba(28,42,74,.92)';
  ctx.lineWidth = 2 * inv;
  if (ctx.setLineDash) ctx.setLineDash([9 * inv, 6 * inv]);
  ctx.beginPath();
  ring.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.closePath();
  ctx.stroke();
  if (ctx.setLineDash) ctx.setLineDash([]);
  ctx.restore();
}

/* ---------------------------------------------------------------- 服务端静态底图 */
async function loadBasemap() {
  if (state.basemap) return state.basemap;
  const cid = $('community').value;
  const meta = await fetch(`/api/basemap.json?community=${encodeURIComponent(cid)}&zoom=16&size=1024`)
    .then((r) => r.json());
  if (!meta.ok) throw new Error('底图元数据不可用');
  const img = await new Promise((res, rej) => {
    const i = new Image();
    i.onload = () => res(i);
    i.onerror = () => rej(new Error('底图图片加载失败（AK 可能未开通静态图服务）'));
    i.src = meta.image_url;
  });
  state.basemap = { ...meta, img };
  return state.basemap;
}

/* ---------------------------------------------------------------- 现状/优化后 覆盖图层切换 */
function updateCoverSwitch() {
  const opt = state.opt;
  const has = !!(opt && opt.after_diag);
  $('coverAfter').disabled = !has;
  if (!has && state.coverMode === 'after') state.coverMode = 'before';
  $('coverBefore').classList.toggle('on', state.coverMode === 'before');
  $('coverAfter').classList.toggle('on', state.coverMode === 'after');
  const d = $('coverDelta');
  if (!has) { d.textContent = '（先点「运行选址优化」，即可查看新增设施后的覆盖图）'; return; }
  const beforeScore = state.bundle.diag.summary.composite_score;
  const afterScore = opt.after_diag.summary.composite_score;
  const delta = (afterScore - beforeScore).toFixed(1);
  d.innerHTML = state.coverMode === 'after'
    ? `当前显示：<b>优化后</b>（含 P1–P${opt.sites.length}）｜综合得分 ${beforeScore} → `
      + `<b>${afterScore}</b>（+${delta}）｜${esc(opt.element_name)}覆盖率 `
      + `${pct(opt.before.coverage_rate)} → <b>${pct(opt.after.coverage_rate)}</b>`
    : `综合得分 ${beforeScore} → 优化后 ${afterScore}（+${delta}），点右侧切换查看覆盖图`;
}

function bindCoverSwitch() {
  $('coverBefore').addEventListener('click', () => {
    state.coverMode = 'before'; updateCoverSwitch(); renderCanvas(); renderLegend();
    if (state.mode === 'bmap') renderBMapOverlays();
  });
  $('coverAfter').addEventListener('click', () => {
    if ($('coverAfter').disabled) return;
    state.coverMode = 'after'; updateCoverSwitch(); renderCanvas(); renderLegend();
    if (state.mode === 'bmap') renderBMapOverlays();
  });
}

/* ---------------------------------------------------------------- canvas */
function renderCanvas() {
  const b = state.bundle;
  const cv = $('map');
  const wrap = $('canvasWrap');
  const cssW = Math.max(360, wrap.clientWidth || 640);
  // 宽屏时把画布限制在视口内：地图 + 图例要能整屏看完，否则"看右侧卡片"就得把地图滚走。
  // 画布保持正方形（投影用统一比例，压扁会把距离画错），宽度用不完就居中留白。
  // 其它元素占的高度（标签页/图层行/图例）是**实测**的，不写死 —— 图例行数会随
  // 图层与动线变化，写死要么留白要么把图例挤出可视区。
  const wide = window.innerWidth > 1180;
  const card = document.querySelector('.map-card');
  const mainBox = document.querySelector('main');
  const prevH = (state.canvasSize || {}).h || 0;
  const chromeH = (card && prevH) ? Math.max(120, card.scrollHeight - prevH) : 215;
  const room = mainBox ? mainBox.clientHeight : window.innerHeight;
  const cssH = wide ? Math.max(340, Math.min(cssW, room - chromeH - 34)) : cssW;
  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(cssH * dpr);
  cv.height = Math.round(cssH * dpr);
  cv.style.width = cssH + 'px';
  cv.style.height = cssH + 'px';
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssH, cssH);
  ctx.fillStyle = '#f7f7f4';
  ctx.fillRect(0, 0, cssH, cssH);
  state.canvasSize = { w: cssH, h: cssH };      // 风圈标注要夹回画布内，需要知道画布多大

  const proj = makeProjector(b, cssH, cssH);
  const ds = b.dataset, diag = activeDiag() || b.diag;
  const v = state.view;
  const toScreen = (p) => ({ x: p.x * v.k + v.tx, y: p.y * v.k + v.ty });
  state.hit = [];

  // 缩放/平移：所有图层都在基础坐标系里绘制，最后统一施加视图变换
  ctx.save();
  ctx.translate(v.tx, v.ty);
  ctx.scale(v.k, v.k);
  // 标记、线宽、文字按 1/k 反向缩放 → 屏幕上尺寸恒定（地图应用的标准做法，
  // 否则放大后点位和字会一起变成巨无霸）
  const inv = 1 / v.k;

  // 0) 百度静态底图（服务端取图，不受浏览器拦截第三方脚本影响）
  if (state.layers.basemap && !state.basemap && !state.basemapLoading) {
    state.basemapLoading = true;
    loadBasemap()
      .then(() => { state.basemapLoading = false; renderCanvas(); renderLegend(); })
      .catch((err) => {
        state.basemapLoading = false;
        state.layers.basemap = false;
        $('ly_basemap').checked = false;
        $('mapHint').textContent = '百度静态底图不可用：' + err.message + '（已回退示意底图）';
        renderCanvas();
      });
  }
  if (state.layers.basemap && state.basemap) {
    const bm = state.basemap;
    const mlo = 111320 * Math.cos((bm.center_wgs84[1] * Math.PI) / 180), mla = 111320;
    const hw = bm.span_m.width / 2, hh = bm.span_m.height / 2;
    const p0 = proj(bm.center_wgs84[0] - hw / mlo, bm.center_wgs84[1] + hh / mla);
    const p1 = proj(bm.center_wgs84[0] + hw / mlo, bm.center_wgs84[1] - hh / mla);
    ctx.globalAlpha = 0.9;
    ctx.drawImage(bm.img, p0.x, p0.y, p1.x - p0.x, p1.y - p0.y);
    ctx.globalAlpha = 1;
  }

  // 1) 底图色块：可达要素数量（默认）/ 步行等时圈，两种口径同一套栅格
  const gm = diag.grid_meta;
  const gc = diag.grid_counts || {};
  const counts = gc.counts || [];
  const total = gc.required_total || 0;
  const times = ((diag.grid_times || {}).times) || [];
  // 选了某个组团看风圈时，不要再铺"全社区并集"分带 —— 两套圈含义不同，叠着看只会糊
  const hasFocus = !!(state.iso && state.iso.data);
  const useIso = state.raster === 'isochrone' && times.length && !hasFocus;
  if (state.layers.cover && gm) {
    const mlon = 111320 * Math.cos((gm.lat0 * Math.PI) / 180), mlat = 111320;
      // 与地图底图叠加时适当降低覆盖层不透明度，避免盖住路名
      ctx.globalAlpha = state.basemap && state.layers.basemap ? 0.5 : 1;
    for (let r = 0; r < gm.nrows; r++) {
      for (let c = 0; c < gm.ncols; c++) {
        let fill = null;
        if (useIso) {
          const bi = isoBand(times[r] ? times[r][c] : -1);
          if (bi >= 0) fill = ISO_BANDS[bi].fill;
        } else {
          const bi = bandIndex(counts[r] ? counts[r][c] : -1, total);
          if (bi >= 0) fill = COVER_BANDS[bi].fill;
        }
        if (!fill) continue;
        const lon0 = gm.lon0 + (c * gm.cell_m) / mlon, lat0 = gm.lat0 + (r * gm.cell_m) / mlat;
        const p0 = proj(lon0, lat0), p1 = proj(lon0 + gm.cell_m / mlon, lat0 + gm.cell_m / mlat);
        ctx.fillStyle = fill;
        ctx.fillRect(p0.x, p1.y, Math.max(1, p1.x - p0.x), Math.max(1, p0.y - p1.y));
      }
    }
    ctx.globalAlpha = 1;
  }

  // 1.5) 单组团风圈（点了房子以后）：同心分带垫在路网下面
  if (state.iso && state.iso.data) drawIso(ctx, proj, inv, 'fill');

  // 1.6) 分析范围框：抓取范围比分析范围大（路网与设施外扩 1200 m），底图上框外仍有
  //      住宅区与设施。不画这条边界，读者无法判断色块到哪里为止。
  drawAnalysisFrame(ctx, proj, inv, b);

  // 2) 河道
  const river = (ds.meta && ds.meta.river) || [];
  if (river.length >= 2) {
    ctx.save();
    ctx.strokeStyle = '#96c8e2';
    ctx.lineWidth = Math.max(6, proj.cell(26)) * inv;
    ctx.lineCap = 'round';
    ctx.beginPath();
    river.forEach((p, i) => (i ? ctx.lineTo(proj(p[0], p[1]).x, proj(p[0], p[1]).y)
                              : ctx.moveTo(proj(p[0], p[1]).x, proj(p[0], p[1]).y)));
    ctx.stroke();
    ctx.restore();
  }

  // 3) 路网：支路/主干路默认显示；内部步道单独一层（默认关闭，因为百度底图不画这类小路，
  //    叠上去会显得"路网和底图对不上"）
  if (state.layers.roads) {
    const coord = {};
    ds.nodes.forEach((n) => (coord[n.id] = n));
    const kinds = state.layers.footway ? ROAD_ORDER : ROAD_ORDER.filter((k) => k !== 'footway');
    ctx.save();
    kinds.forEach((kind) => {
      const st = ROAD_STYLE[kind];
      ctx.strokeStyle = st.color;
      ctx.globalAlpha = kind === 'footway' ? 0.55 : 0.8;
      ctx.lineWidth = Math.max(1, proj.cell(st.width * 1.6)) * inv;
      ctx.beginPath();
      ds.edges.forEach((e) => {
        if (e.kind !== kind) return;
        const u = coord[e.u], v = coord[e.v];
        if (!u || !v) return;
        const a = proj(u.lon, u.lat), b2 = proj(v.lon, v.lat);
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b2.x, b2.y);
      });
      ctx.stroke();
    });
    ctx.restore();
  }

  // 4) 设施（语义图标：菜市场=菜篮、便利店=店铺、卫生站=十字…）
  const elemName = {};
  (b.elements_config.elements || []).forEach((e) => (elemName[e.key] = e.name));
  // 图标屏幕尺寸随缩放微调：缩得很小时小一点，避免糊成一片
  const iconPx = v.k < 1.4 ? 15 : v.k < 2.5 ? 18 : 21;
  if (state.layers.poi) {
    const R = (iconPx / 2) * inv;
    ds.facilities.forEach((f) => {
      const p = proj(f.lon, f.lat);
      const icon = iconCanvas(f.element, ELEMENT_COLORS[f.element] || '#555');
      ctx.drawImage(icon, p.x - R, p.y - R, R * 2, R * 2);
      state.hit.push({ ...toScreen(p), type: 'poi', data: f });
    });
  }

  // 5) 居住组团：房子图标（绿=要素齐备 / 红=存在短板 / 灰=路网未连通无法评估），大小随户数
  if (state.layers.resid) {
    diag.points.forEach((pt) => {
      const rp = ds.residential.find((r) => r.id === pt.id);
      if (!rp) return;
      const p = proj(rp.lon, rp.lat);
      const R = Math.max(6, Math.min(13, 6 + pt.households / 500)) * inv * (v.k < 1.4 ? 0.85 : 1);
      const color = pt.assessable === false ? '#9aa0a6'
        : (pt.compliance_score >= 0.999 ? '#2ea043' : '#d62d20');
      ctx.drawImage(iconCanvas('residential', color), p.x - R, p.y - R, R * 2, R * 2);
      state.hit.push({ ...toScreen(p), type: 'resid', data: { rp, pt } });
    });
  }

  // 6) 一天动线（点击居住组团生成，画在最上层）
  if (state.route && state.route.data) drawRoute(ctx, proj, inv);

  // 6.5) 风圈刻度（最上层）：理想圆 / 圈上标签 / 被切断的方向
  if (state.iso && state.iso.data) drawIso(ctx, proj, inv, 'annot');

  // 7) 推荐点位：定位图钉
  if (state.layers.sites && state.opt && state.opt.sites) {
    state.opt.sites.forEach((s, i) => {
      const p = proj(s.lon, s.lat), R = 12 * inv;
      ctx.drawImage(iconCanvas('site', '#ff9100'), p.x - R, p.y - R - 3 * inv, R * 2, R * 2);
      ctx.fillStyle = '#8a4200';
      ctx.font = `700 ${Math.round(13 * inv)}px system-ui, sans-serif`;
      ctx.fillText('P' + (i + 1), p.x + R + 2 * inv, p.y + 4 * inv);
      state.hit.push({ ...toScreen(p), type: 'site', data: { site: s, index: i + 1 } });
    });
  }

  ctx.restore();   // 结束视图变换：比例尺与标注用屏幕坐标绘制

  // 7) 比例尺（随缩放变化，画在屏幕坐标系里）
  const barM = 200, barPx = proj.cell(barM) * v.k;
  ctx.strokeStyle = '#222';
  ctx.lineWidth = 1.8;
  const bx = Math.max(20, cssH - barPx - 26), by = cssH - 26;
  ctx.beginPath();
  ctx.moveTo(bx, by); ctx.lineTo(bx + barPx, by);
  ctx.moveTo(bx, by - 5); ctx.lineTo(bx, by + 5);
  ctx.moveTo(bx + barPx, by - 5); ctx.lineTo(bx + barPx, by + 5);
  ctx.stroke();
  ctx.fillStyle = '#222';
  ctx.font = '12px system-ui, sans-serif';
  ctx.fillText(`${barM} m`, bx + barPx / 2 - 14, by - 9);
  ctx.fillText(`×${v.k.toFixed(1)}`, 14, cssH - 26);
}

function shapeSvg(shape, color) {
  if (shape === 'square') {
    return `<svg width="14" height="14"><rect x="2" y="2" width="10" height="10"`
      + ` fill="${color}" stroke="#fff"/></svg>`;
  }
  if (shape === 'triangle') {
    return `<svg width="14" height="14"><polygon points="7,1.5 12.5,12 1.5,12"`
      + ` fill="${color}" stroke="#fff"/></svg>`;
  }
  return `<svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="${color}" stroke="#fff"/></svg>`;
}

function renderLegend() {
  const b = state.bundle;
  const gc = b.diag.grid_counts || {};
  const total = gc.required_total || 0;
  const d = activeDiag() || b.diag;
  const mask = ((d.grid_counts || {}).mask || {});
  const buf = mask.resid_buffer_m || 150;
  const useIso = state.raster === 'isochrone';
  const parts = (useIso
    ? ISO_BANDS.map((band) => `<span><i style="background:${band.css}"></i>${band.label}</span>`)
      .concat(['<span class="muted">（未着色：15 分钟阈值内不可达）</span>'])
    : COVER_BANDS.map((band) =>
        `<span><i style="background:${band.css}"></i>${band.label}</span>`)).join('');
  const icon = (key, color) =>
    `<img class="lg-ico" src="${iconDataUrl(key, color)}" alt="">`;
  const rows = [
    `<span><b>${useIso ? '步行时间分带（自任一居住组团出发）' : '15 分钟可达必需要素'}</b>`
    + `${useIso ? '' : `（共 ${total} 项）`}：</span>`, parts,
    `<span class="muted">色块仅覆盖居住组团周边 ${buf} m 范围（居民实际居住区域），`
    + `水体及无居住用地不着色</span>`,
    `<span><i class="frame"></i><b>分析范围</b>（框外不参与体检）</span>`,
    `<span class="muted">居住组团、设施、指标与色块均只统计框内；框外路网与设施仅在步行`
    + `可达性计算中作为通路使用，因此底图上框外仍可见住宅区标注</span>`,
    `<span>${icon('residential', '#2ea043')}达标居住组团</span>`,
    `<span>${icon('residential', '#d62d20')}存在要素缺口的组团</span>`,
    `<span>${icon('residential', '#9aa0a6')}路网未连通（无法评估）</span>`,
    `<span>${icon('site', '#ff9100')}推荐新增点位</span>`,
  ];
  const cat = {};
  (b.elements_config.elements || []).forEach((e) => (cat[e.key] = e.category));
  const used = [...new Set(b.dataset.facilities.map((f) => f.element))].sort();
  const nameOf = {};
  (b.elements_config.elements || []).forEach((e) => (nameOf[e.key] = e.name));
  used.forEach((k) => rows.push(
    `<span title="${esc(cat[k] || '')}">${icon(k, ELEMENT_COLORS[k] || '#555')}`
    + `${esc(nameOf[k] || k)}</span>`));
  if (state.iso && state.iso.data) {
    const iso = state.iso.data;
    rows.splice(2, 0,
      `<span><b>同心等时圈（圆心：${esc(iso.point.name)}）</b>：</span>`,
      ...iso.bands.map((b, i) =>
        `<span><i style="background:${ISO_FILLS[i]};border-color:${ISO_LINES[i]}"></i>`
        + `${b.label} · 圈内可达 ${b.elements_count}/${b.elements_total} 项</span>`),
      `<span><i style="background:${LOST_FILL};border-color:#969696"></i>理想圆范围内不可达（受路网折减）</span>`,
      `<span class="muted">虚线圆为同半径理想圆，用于对照可达面积比</span>`);
  }
  if (state.route && state.route.data) {
    const rs = ROUTE_STYLE[state.route.data.mode] || ROUTE_STYLE.before;
    const pn = (state.route.data.persona || {}).name;
    rows.push(`<span><i style="background:${rs.line}"></i>一天动线`
      + `${pn ? `（${esc(pn)}）` : ''}（${esc(state.route.data.mode_label)}）`
      + `　虚线=设施/住户到路网的最后一小截</span>`);
  }
  if (state.basemap) rows.push(`<span class="muted">${esc(state.basemap.attribution || '')}</span>`);
  $('legend').innerHTML = rows.join('');
}

/* ---------------------------------------------------------------- 右侧面板 */

/** 得分 → 颜色（面板、雷达条、处方对比共用同一套阈值，避免三处各写一遍） */
function scoreColor(v) {
  const x = v > 1 ? v / 100 : v;                 // 兼容 0–1 与 0–100 两种入参
  if (x >= 0.85) return '#2ca02c';
  if (x >= 0.70) return '#1f77b4';
  if (x >= 0.55) return '#ff7f0e';
  return '#d62728';
}

function renderPanel() {
  const b = state.bundle, s = b.diag.summary, diag = b.diag;
  $('scoreNum').textContent = s.composite_score;
  $('scoreNum').style.color = scoreColor(s.composite_score);
  $('scoreGrade').textContent = '综合体检等级：' + s.grade;
  $('scoreMeta').innerHTML = `${s.residential_points} 个居住组团 · ${s.households.toLocaleString()} 户 · `
    + `${s.population.toLocaleString()} 人 · 设施 ${s.facilities} 处<br>`
    + `15 分钟生活圈 ${s.circle_area_km2} km² · 组团间 Gini ${s.gini}`;

  $('dims').innerHTML = Object.values(diag.dimensions).map((d) => `
    <div class="dim" title="${esc(d.detail)}">
      <span>${esc(d.label)}</span>
      <span class="track"><span class="fill" style="width:${(d.score * 100).toFixed(1)}%;
        background:${scoreColor(d.score)}"></span></span>
      <span class="val">${(d.score * 100).toFixed(1)}</span>
    </div>`).join('');

  const head = '<thead><tr><th>要素</th><th>必需</th><th>设施</th><th>15 分钟覆盖</th><th>单一依赖</th><th>缺口</th></tr></thead>';
  $('elemTable').innerHTML = head + '<tbody>' + diag.elements.map((e) => `
    <tr title="${esc(e.source || '')}">
      <td>${esc(e.name)}</td><td>${e.required ? '✔' : '—'}</td><td>${e.facility_count}</td>
      <td class="${e.coverage_rate >= 0.95 ? 'ok' : 'bad'}">${pct(e.coverage_rate)}</td>
      <td>${pct(e.single_dependency_rate)}</td><td>${e.gap_index.toFixed(2)}</td>
    </tr>`).join('') + '</tbody>';

  $('problems').innerHTML = diag.problems.length ? diag.problems.map((p) => `
    <div class="problem ${p.level === '高' ? 'high' : ''}">
      <div class="t">[${p.level}] ${esc(p.title)}</div>
      <div class="d">${esc(p.detail)}</div>
      <div class="i">影响约 ${p.affected_households.toLocaleString()} 户 / ${p.affected_population.toLocaleString()} 人</div>
    </div>`).join('') : '<div class="muted">未发现必需要素缺口。</div>';

  const meta = b.dataset.meta || {};
  const prov = b.household_provenance || {};
  const cal = prov.calibration || {};
  const ov = prov.overrides || {};
  const dq = b.diag.data_quality || {};
  const netq = dq.network || {};
  const hhLine = ov.applied
    ? `户数：<b>实测覆盖 ${ov.matched}/${ov.rows} 条</b>（${esc(ov.source)}）`
    : cal.applied
      ? `户数：<b>总量校准 ×${cal.factor}</b>（${esc(cal.basis)}）<br>`
        + `<span class="muted">校准前 ${(cal.population_before || 0).toLocaleString()} 人 → `
        + `校准后 ${(cal.population_after || 0).toLocaleString()} 人</span>`
      : '户数：数据源原始估算值';
  $('meta').innerHTML = `数据源：<b>${esc(meta.source)}</b>${meta.seed ? `（种子 ${meta.seed}）` : ''}<br>`
    + `${hhLine}<br>`
    + `路网：${meta.node_count || b.dataset.nodes.length} 节点 / ${meta.edge_count || b.dataset.edges.length} 边`
    + `${meta.bridges ? ` · 河道打断 ${meta.removed_crossings} 处、保留 ${meta.bridges} 座桥` : ''}<br>`
    + `<span class="muted">${esc(b.community.data_disclaimer || meta.note || '')}</span>`
    + (meta.residential_baidu_added
       ? `<br><span class="muted">居住组团：OSM 建筑覆盖不足，已用百度「住宅区/小区」POI `
         + `补空白 ${meta.residential_baidu_added} 处（共 ${meta.residential_count} 个组团）</span>` : '')
    + (netq.components
       ? `<br><span class="muted">路网连通性：${netq.components} 个连通块`
         + (netq.stitched_edges ? `，已补连断头路 ${netq.stitched_edges} 处` : '')
         + (dq.unassessable_households
            ? `；<b class="warn">${dq.unassessable_points.length} 个组团（`
              + `${dq.unassessable_households.toLocaleString()} 户）路网未连通、按无法评估处理</b>`
            : '，全部组团可评估')
         + `</span>` : '')
    + (cal.sources && cal.sources.length
       ? `<br><span class="muted">校准依据：${esc(cal.sources.join('；'))}</span>` : '');
  renderOpt();
}

function renderOpt() {
  const opt = state.opt;
  if (!opt) { $('opt').innerHTML = '<span class="muted">点击「运行选址优化」生成建议。</span>'; return; }
  const basis = opt.siting_basis || {};
  const counts = basis.carrier_counts || {};
  const rej = opt.rejected || {};
  // 优化后整体体检（把推荐点位加进去重算的结果）
  const ad = opt.after_diag;
  const beforeSum = (state.bundle && state.bundle.diag && state.bundle.diag.summary) || {};
  let afterBlock = '';
  if (ad && ad.summary) {
    const ds0 = beforeSum.composite_score, ds1 = ad.summary.composite_score;
    const dim = (k) => [((state.bundle.diag.dimensions || {})[k] || {}).score,
                        ((ad.dimensions || {})[k] || {}).score];
    const row = (label, a, b) => `<div class="kv"><span>${label}</span><span>${a} → <b>${b}</b></span></div>`;
    afterBlock = `
      <div class="after-block">
        <div class="after-title">新增设施后（含 P1–P${opt.sites.length}）</div>
        ${row('综合体检得分', ds0, `<span class="up">${ds1}</span>`)}
        ${row('15 分钟可达覆盖率', (dim('coverage')[0] * 100).toFixed(1) + '%',
              `<span class="up">${(dim('coverage')[1] * 100).toFixed(1)}%</span>`)}
        ${row('要素达标率', (dim('compliance')[0] * 100).toFixed(1) + '%',
              `<span class="up">${(dim('compliance')[1] * 100).toFixed(1)}%</span>`)}
        ${row('冗余度（抗风险）', (dim('redundancy')[0] * 100).toFixed(1) + '%',
              `<span class="up">${(dim('redundancy')[1] * 100).toFixed(1)}%</span>`)}
        ${row(`不可达人口（${esc(opt.element_name)}）`,
              opt.before.uncovered_population.toLocaleString(),
              `<span class="up">${opt.after.uncovered_population.toLocaleString()}</span>`)}
        ${row('合计新增覆盖（并集口径）', '—',
              `<span class="up">${Math.round((opt.before.uncovered_population
                - opt.after.uncovered_population) / 2.6).toLocaleString()} 户</span>`)}
        <button id="showAfter" class="ghost after-btn">在地图上查看优化后覆盖范围</button>
      </div>`;
  }
  const sites = (opt.sites || []).map((s, i) => {
    const carrier = s.carrier || {};
    const near = (s.constraints || {}).nearest_existing_same_m;
    const rel = carrier.relation === '合署' ? '可合署载体' : '邻近载体';
    return `
    <div class="site">
      <div class="site-head">
        <b>P${i + 1}</b>
        <span class="tier-badge">第 ${s.tier || 0} 梯队 · ${esc(s.tier_name || '—')}</span>
      </div>
      <div class="kv"><span title="第 1/4 梯队为『邻近』（借客流，设施在其影响范围以外）；第 2/3 梯队为『可合署』（同一栋楼或同一院落）"
        >${rel}</span><span>${esc(carrier.name || '—')}
        <span class="muted">（${(carrier.distance_m || 0).toFixed(0)} m）</span></span></div>
      <div class="kv"><span title="单点新增覆盖 = 假如只建这一处时它能新覆盖的户数；
        多个点位会覆盖同一组团，所以各项相加会重复计数，合计请看上面的「并集口径」">
        单点新增覆盖户数</span><span>${s.newly_covered_households.toLocaleString()} 户 /
        ${s.newly_covered_points.length} 个组团</span></div>
      <div class="kv"><span>加权得分</span><span>${(s.score || 0).toLocaleString()}
        <span class="muted">（×${s.tier_coef || 1}）</span></span></div>
      <div class="kv"><span>同类最近既有</span><span>${near == null ? '—' : near + ' m'}
        <span class="muted">（要求 ≥${(s.constraints || {}).same_kind_gap_m || 0} m）</span></span></div>
      <div class="muted site-reason">${esc(s.reason || '')}</div>
      <div class="muted">${s.lon.toFixed(6)}, ${s.lat.toFixed(6)}</div>
      ${siteProfileHtml(s.profile, i === 0)}
    </div>`;
  }).join('');
  $('opt').innerHTML = `
    <div><b>${esc(opt.element_name)}</b>（可社区选址：${opt.sitable ? '是' : '否'}）</div>
    ${afterBlock}
    <div class="kv"><span>候选来源</span><span>${opt.candidate_mode === 'carriers'
      ? `载体梯队环带（${basis.carriers_available || 0} 处载体）` : '均匀网格（无载体信息）'}</span></div>
    <div class="kv"><span>已排除</span><span>水体/河道 ${basis.water_features || 0} 处 ·
      禁建区 ${basis.exclusions_available || 0} 处</span></div>
    <div class="kv"><span>候选筛除</span><span>水体内 ${rej.water || 0} · 禁建区 ${rej.exclusion || 0} ·
      同类过近 ${rej.same_kind_gap || 0}</span></div>
    <div class="kv"><span>可用候选</span><span>${opt.candidates_considered} 个</span></div>
    <div class="kv"><span>15 分钟可达覆盖率</span>
      <span>${pct(opt.before.coverage_rate)} → <b>${pct(opt.after.coverage_rate)}</b></span></div>
    <div class="kv"><span>不可达人口</span>
      <span>${opt.before.uncovered_population.toLocaleString()} → ${
        opt.after.uncovered_population.toLocaleString()}</span></div>
    <div class="muted" style="margin-top:6px">${esc(opt.method)}</div>
    ${sites}`;
  const btn = $('showAfter');
  if (btn) btn.addEventListener('click', () => {
    state.coverMode = 'after';
    updateCoverSwitch();
    renderCanvas();
    renderLegend();
    document.getElementById('canvasWrap').scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
}

/** 点位画像：建设形态 / 受益面 / 容量核对 / 可合署 —— "这个点具体怎么建"。 */
function siteProfileHtml(p, openFirst) {
  if (!p) return '';
  const ws = p.walk_stats || {};
  const cap = p.capacity || {};
  const b = p.build || {};
  const groups = (p.groups || []).map((g) =>
    `<span class="pf-group" title="${esc(g.name)}：${g.households} 户 / ${g.population} 人">`
    + `${esc(g.name)} <b>${g.walk_min}′</b></span>`).join('');
  return `
    <details class="pf"${openFirst ? ' open' : ''}>
      <summary>点位画像（建设形式、规模、受益范围、合署对象）</summary>
      <div class="pf-body">
        <div class="pf-row"><span>受益面</span><span><b>${(p.service_population || 0).toLocaleString()}</b> 人 /
          ${p.benefit_count} 个组团 · 步行中位 <b>${ws.median_min ?? '—'}</b> 分钟、最长 <b>${ws.max_min ?? '—'}</b> 分钟
          ${ws.within_10min_share != null ? `· ${(ws.within_10min_share * 100).toFixed(0)}% 在 10 分钟内` : ''}</span></div>
        ${b.form ? `<div class="pf-row"><span>建设形态</span><span>${esc(b.form)}</span></div>` : ''}
        ${b.space ? `<div class="pf-row"><span>规模建议</span><span>${esc(b.space)}</span></div>` : ''}
        ${b.approval ? `<div class="pf-row"><span>审批要点</span><span>${esc(b.approval)}</span></div>` : ''}
        ${b.carrier_note ? `<div class="pf-row"><span>载体约束</span><span>${esc(b.carrier_note)}</span></div>` : ''}
        ${cap.verdict ? `<div class="pf-row"><span>容量核对</span><span>${esc(cap.verdict)}
          <span class="muted">（按 ${cap.assumed_service_pop_per_unit?.toLocaleString()} 人/处的工程假设，
          相当于 ${cap.units_implied} 处）</span></span></div>` : ''}
        ${(p.complement || []).length ? `<div class="pf-row"><span>可合署</span><span>${
          p.complement.map((c) => `${esc(c.element_name)}（${esc(c.facility)}，${c.distance_m} m）`).join('；')
        }</span></div>` : ''}
        <div class="pf-row"><span>受益组团</span><span class="pf-groups">${groups}
          ${p.groups_total > (p.groups || []).length
            ? `<span class="muted">…共 ${p.groups_total} 个</span>` : ''}</span></div>
        ${b.caveat ? `<div class="muted pf-caveat">${esc(b.caveat)}</div>` : ''}
      </div>
    </details>`;
}

/* ---------------------------------------------------------------- 一天动线
   点击居住组团 → 调 /api/route → 沿路网画出"把必需要素各走一遍"的动线，
   并给出逐段时间、缺口要素与情景假设（停留时长/出发时刻可调）。
   ------------------------------------------------------------------ */
const ROUTE_STYLE = {
  before: { line: '#1f77b4', badge: '#1f77b4', label: '现状设施' },
  after: { line: '#2ea043', badge: '#2ea043', label: '优化后（含新增点位）' },
};

function requiredElements() {
  return ((state.bundle && state.bundle.elements_config.elements) || [])
    .filter((e) => e.required).map((e) => e.key);
}

function routeQuery(r) {
  const q = new URLSearchParams({
    community: $('community').value, point: r.pointId, mode: r.mode, order: r.order,
    k: $('k').value,
  });
  // 出发时刻：没显式改过就不传，让服务端用画像的默认值（每位住户的作息不同）
  if (r.start) q.set('start', r.start);
  if (r.persona) q.set('persona', r.persona);
  if (r.speed) q.set('speed', r.speed);
  // 画像出行链由服务端决定；只有"全要素"模式才把 11 项必需全塞进去
  if (!r.persona) {
    const els = r.elements && r.elements.length ? r.elements : requiredElements();
    if (els.length) q.set('elements', els.join(','));
  } else if (r.elements && r.elements.length) {
    q.set('elements', r.elements.join(','));
  }
  const stay = Object.entries(r.stay || {}).filter(([, v]) => v !== '' && v != null)
    .map(([k, v]) => `${k}:${v}`).join(',');
  if (stay) q.set('stay', stay);
  return q;
}

/** 典型住户选择条：先选"哪一类人"，再点地图上的房子看"他的一天"。 */
function renderPersonaBar() {
  const bar = $('personaBar');
  if (!bar) return;
  const chips = [''].concat((state.personas || []).map((p) => p.key));
  bar.innerHTML = chips.map((k) => {
    const p = personaOf(k);
    const on = state.persona === k ? 'on' : '';
    const color = p ? p.color : '#5b6470';
    const style = state.persona === k ? `background:${color};border-color:${color}` : '';
    const label = p ? `${p.emoji} ${p.name}` : '🧭 全要素';
    const title = p ? `${p.who}｜步速 ${p.walk_speed_mps} m/s，${p.start_clock} 出发｜${p.story}`
      : '不分人群：把 11 项必需要素按通用生活动线各走一遍（体检口径，成人 1.25 m/s）';
    return `<button class="persona-chip ${on}" style="${style}" data-persona="${k}"`
      + ` title="${esc(title)}"><em>${label}</em></button>`;
  }).join('');
  const p = personaOf(state.persona);
  $('personaBar').dataset.who = p ? p.key : '';
  const who = document.getElementById('personaWho');
  if (who) {
    who.innerHTML = p
      ? `<b>${esc(p.name)}</b>：${esc(p.who)}<br>${esc(p.story)}<br>`
        + `<span class="muted">步速 ${p.walk_speed_mps} m/s → 15 分钟能走 `
        + `${Math.round(p.walk_speed_mps * 900)} m（成人 1.25 m/s 是 1125 m）；`
        + `单段超过 ${p.max_leg_min} 分钟会标黄。${esc(p.window_source || '')}</span>`
      : '<b>全要素出行链</b>：不区分人群，按通用生活动线依次到访全部 11 项必需要素。';
  }
  bar.querySelectorAll('[data-persona]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.persona = btn.dataset.persona;
      renderPersonaBar();
      if (state.route && state.route.pointId) {
        // 换人 = 换作息：出发时刻与步速都回到这位住户的默认值
        loadRoute(state.route.pointId, { persona: state.persona, start: null, speed: null });
      }
      if (state.iso && state.iso.pointId) loadIsochrone(state.iso.pointId);
    });
  });
}

async function loadRoute(pointId, patch) {
  const prev = state.route || {};
  state.route = {
    pointId, mode: 'before', order: 'day', stay: {}, start: null, speed: null,
    persona: state.persona || '', data: prev.data,
    ...prev, ...(patch || {}), pointId: pointId || prev.pointId,
    playing: false, t: 0,
  };
  const r = state.route;
  $('routeBody').innerHTML = '<span class="muted">正在沿路网算这条动线…</span>';
  try {
    const res = await fetch('/api/route?' + routeQuery(r).toString());
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '动线计算失败');
    r.data = data;
    r.mode = data.mode;
    renderRoute();
    renderCanvas();
    drawGLRoute(true);                    // GL 底图模式下把折线画到百度地图上
  } catch (err) {
    $('routeBody').innerHTML = `<span class="err">动线计算失败：${esc(err.message)}</span>`;
  }
}

function clearRoute() {
  state.route = null;
  renderRoute();
  renderCanvas();
  drawGLRoute();
}

/** 逐段折线 + 序号停靠点 + 起点（家）。画在视图变换之内，线宽用 1/k 保持屏幕恒定。 */
function drawRoute(ctx, proj, inv) {
  const r = state.route;
  if (!r || !r.data || !r.data.stops.length) return;
  const st = ROUTE_STYLE[r.data.mode] || ROUTE_STYLE.before;
  const segs = r.data.stops.concat(r.data.home_leg ? [{ leg_coords: r.data.home_leg.leg_coords,
                                                         stub_from: r.data.home_leg.stub_from }] : []);
  ctx.save();
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  // 1) 先画"离路段"（虚线）：设施/住户离路网的最后一小截，直连会显得在穿墙
  ctx.setLineDash([3 * inv, 3 * inv]);
  ctx.lineWidth = 1.6 * inv;
  ctx.strokeStyle = 'rgba(60,60,60,.65)';
  segs.forEach((s) => {
    if (!s.stub_from || !s.leg_coords || !s.leg_coords.length) return;
    const a = proj(s.stub_from[0], s.stub_from[1]);
    const b = proj(s.leg_coords[0][0], s.leg_coords[0][1]);
    if (Math.hypot(a.x - b.x, a.y - b.y) < 0.5) return;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  });
  ctx.setLineDash([]);
  // 2) 路网上的实线（带白色描边，压在底图上看得清）
  ctx.lineWidth = 5 * inv;
  ctx.strokeStyle = 'rgba(255,255,255,.85)';
  segs.forEach((s) => strokeLeg(ctx, proj, s.leg_coords));
  ctx.lineWidth = 3 * inv;
  ctx.strokeStyle = st.line;
  segs.forEach((s) => strokeLeg(ctx, proj, s.leg_coords));
  // 3) 起点（家）
  const home = proj(r.data.point.lon, r.data.point.lat);
  ctx.drawImage(iconCanvas('residential', '#0b2e59'), home.x - 9 * inv, home.y - 9 * inv,
                18 * inv, 18 * inv);
  // 4) 停靠点序号
  r.data.stops.forEach((s) => {
    const p = proj(s.lon, s.lat);
    const R = 8.5 * inv;
    ctx.beginPath(); ctx.arc(p.x, p.y, R, 0, Math.PI * 2);
    ctx.fillStyle = st.badge; ctx.fill();
    ctx.lineWidth = 1.4 * inv; ctx.strokeStyle = '#fff'; ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.font = `700 ${Math.round(10 * inv)}px system-ui, sans-serif`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(String(s.seq), p.x, p.y + 0.5 * inv);
  });
  ctx.textAlign = 'start'; ctx.textBaseline = 'alphabetic';
  // 5) 播放中的小圆点（沿折线累计距离推进）
  if (r.playing && r.data) {
    const pts = routePolyline(r.data);
    if (pts.length > 1) {
      const total = pts[pts.length - 1].d;
      const target = total * Math.min(1, Math.max(0, r.t));
      const at = pts.find((p) => p.d >= target) || pts[pts.length - 1];
      const q = proj(at.lon, at.lat);
      ctx.beginPath(); ctx.arc(q.x, q.y, 6 * inv, 0, Math.PI * 2);
      ctx.fillStyle = '#ff9100'; ctx.fill();
      ctx.lineWidth = 2 * inv; ctx.strokeStyle = '#fff'; ctx.stroke();
    }
  }
  ctx.restore();
}

function strokeLeg(ctx, proj, coords) {
  if (!coords || coords.length < 2) return;
  ctx.beginPath();
  coords.forEach((c, i) => {
    const p = proj(c[0], c[1]);
    if (i) ctx.lineTo(p.x, p.y); else ctx.moveTo(p.x, p.y);
  });
  ctx.stroke();
}

/** 把动线（含回家段）拉平成一条带累计距离的折线，供播放动画使用。 */
function routePolyline(data) {
  const out = [];
  let d = 0;
  const push = (lon, lat) => {
    if (out.length) {
      const p = out[out.length - 1];
      d += Math.hypot((lon - p.lon) * 95000, (lat - p.lat) * 111000);
    }
    out.push({ lon, lat, d });
  };
  push(data.point.lon, data.point.lat);
  const legs = data.stops.map((s) => s.leg_coords)
    .concat(data.home_leg ? [data.home_leg.leg_coords] : []);
  legs.forEach((coords) => (coords || []).forEach((c) => push(c[0], c[1])));
  return out;
}

function playRoute() {
  const r = state.route;
  if (!r || !r.data) return;
  if (r.playing) { r.playing = false; renderRoute(); renderCanvas(); return; }
  r.playing = true; r.t = 0;
  renderRoute();
  if (glState.routeDot) glState.routeDot.show && glState.routeDot.show();
  const t0 = performance.now();
  const DUR = 9000;
  const step = (now) => {
    if (!state.route || !state.route.playing) return;
    state.route.t = Math.min(1, (now - t0) / DUR);
    renderCanvas();
    moveGLRouteDot(state.route.t);        // GL 模式下让小圆点沿折线走
    if (state.route.t < 1) requestAnimationFrame(step);
    else {
      state.route.playing = false;
      renderRoute();
      renderCanvas();
      if (glState.routeDot) glState.routeDot.hide && glState.routeDot.hide();
    }
  };
  requestAnimationFrame(step);
}

/** 播放：把 GL 上的小圆点移动到折线的 t（0→1）位置。 */
function moveGLRouteDot(t) {
  const B = glState.B, dot = glState.routeDot, r = state.route;
  if (!B || !dot || !r || !r.data) return;
  const pts = routePolyline(r.data);
  if (pts.length < 2) return;
  const total = pts[pts.length - 1].d;
  const target = total * Math.min(1, Math.max(0, t));
  const at = pts.find((p) => p.d >= target) || pts[pts.length - 1];
  dot.setPosition(bdPoint(B, at.lon, at.lat));
}

function renderRoute() {
  const r = state.route;
  const box = $('routeBody');
  if (!r || !r.data) {
    box.innerHTML = '<span class="muted">先选择住户类别，再点击地图上的<b>居住组团</b>。'
      + '系统沿步行路网重现该住户的一日出行链，逐段给出时间，并按设施开放时段校验'
      + '（菜市场 12:00 收市、社区卫生服务站午间停诊、幼儿园 8:30 截止入园）；'
      + '超出时段的到访记为服务时段冲突，并给出调整建议。</span>';
    $('routeHead').hidden = true;
    return;
  }
  const d = r.data, t = d.totals;
  const p = d.persona || null;
  $('routeHead').hidden = false;
  const modeBtn = (m, label, cls) =>
    `<button class="mini ${r.mode === m ? 'on' : ''} ${cls || ''}" data-route-mode="${m}">${label}</button>`;
  const stopRows = d.stops.map((s) => {
    const c = s.conflict;
    const ctext = c
      ? (c.kind === 'closed'
         ? `<span class="warn"> ⚠ 到达时已结束（服务到 ${c.closes}）</span>`
         : `<span class="rt-wait"> ⚠ 还没开门（${c.opens} 才开）</span>`)
      : (s.wait_min > 0 ? `<span class="rt-wait">· 就地等了 ${s.wait_min} 分钟</span>` : '');
    return `
    <div class="rt-stop">
      <span class="rt-seq" style="background:${(ROUTE_STYLE[d.mode] || {}).badge}">${s.seq}</span>
      <span class="rt-main">
        <b>${esc(s.facility_name)}</b>
        <span class="muted">${esc(s.element_name)}${s.address ? ' · ' + esc(s.address) : ''}</span>
        <span class="rt-walk">${s.arrive} 到 · 步行 ${s.walk_min} 分钟 / ${s.walk_m} m
          ${s.within_15min ? '' : '<b class="warn">（本段超过 15 分钟阈值）</b>'}
          ${s.over_leg_limit ? `<b class="warn">（超过该画像可接受的 ${s.leg_limit_min} 分钟）</b>` : ''}
          ${ctext}</span>
        ${s.window ? `<span class="muted" style="font-size:11px">开放时段 ${s.window}</span>` : ''}
      </span>
      <label class="rt-stay" title="停留时长（情景假设，可改）">
        <input type="number" min="0" max="180" step="5" value="${s.stay_min}"
               data-stay="${s.element}">分
      </label>
    </div>`;
  }).join('');
  const missingRows = d.missing.length ? `
    <div class="rt-missing">
      <b>本次出行不可达的必需服务（${d.missing.length} 项）</b>
      ${d.missing.map((m) => `<div>· <b>${esc(m.name)}</b>：${esc(m.reason)}
        ${m.nearest_min ? `<span class="muted">（最近一处 ${m.nearest_min} 分钟）</span>` : ''}</div>`).join('')}
      <div class="muted">上述要素在 15 分钟阈值内不可达，构成该组团的服务缺口。</div>
    </div>` : '<div class="rt-ok">出行链要素均在 15 分钟可达范围内。</div>';
  const recRows = (d.recommendations || []).length ? `
    <div class="rt-recs">
      ${d.recommendations.map((x) => `<div class="rt-rec lv-${esc(x.level)}">
        <span class="t">[${esc(x.level)}] ${esc(x.title)}</span><br>${esc(x.detail)}</div>`).join('')}
    </div>` : '';
  const alt = d.alternatives || {};
  const save = (alt.day_order_min != null && alt.nearest_order_min != null)
    ? Math.round((alt.day_order_min - alt.nearest_order_min) * 10) / 10 : null;
  if (d.network_status && d.network_status !== 'ok') {
    box.innerHTML = `
      <div class="rt-point"><b>${esc(d.point.name)}</b>
        <span class="muted">${d.point.households.toLocaleString()} 户 /
          ${d.point.population.toLocaleString()} 人</span></div>
      <div class="rt-missing"><b>这个组团无法评估（不是没有设施）</b>
        <div>它所在的小路在 OpenStreetMap 里没有接到大马路上，程序算不出通路。</div>
        <div class="muted">${esc(d.assumptions.note)}</div>
      </div>`;
    return;
  }
  box.innerHTML = `
    <div class="rt-point">
      <b>${esc(d.point.name)}</b>
      <span class="muted">${d.point.households.toLocaleString()} 户 /
        ${d.point.population.toLocaleString()} 人 · ${d.mode_label}</span>
      ${p ? `<span class="muted">${p.emoji || ''} <b>${esc(p.name)}</b>的一天
        （步速 ${d.assumptions.walk_speed_mps} m/s${p.window_source ? '，' + esc(p.window_source) : ''}）</span>`
          : '<span class="muted">全要素出行链（不分人群）</span>'}
    </div>
    <div class="rt-tools">
      ${modeBtn('before', '现状')} ${modeBtn('after', '优化后（含新增点位）')}
      <span class="rt-sep"></span>
      <button class="mini ${r.order === 'day' ? 'on' : ''}" data-route-order="day">按住户出行链</button>
      <button class="mini ${r.order === 'nearest' ? 'on' : ''}" data-route-order="nearest">就近串点序</button>
      <span class="rt-sep"></span>
      <button class="mini" id="routePlay">${r.playing ? '⏸ 暂停' : '▶ 播放'}</button>
      <button class="mini" id="routePng">导出 PNG</button>
      <button class="mini" id="routeClear">清空</button>
    </div>
    <div class="rt-sum">
      <span><b>${t.stops}</b> 处停靠</span>
      <span>步行 <b>${t.walk_min}</b> 分钟 / ${t.walk_m.toLocaleString()} m</span>
      ${t.wait_min > 0 ? `<span>等待 <b>${t.wait_min}</b> 分钟</span>` : ''}
      <span>全程 <b>${t.span_min}</b> 分钟（含停留 ${t.stay_min}）</span>
      <span>${t.start_clock} – ${t.end_clock}</span>
      ${t.conflict_count ? `<span class="warn"><b>服务时段冲突 ${t.conflict_count}</b> 处</span>` : ''}
    </div>
    ${save != null && save > 0
      ? `<div class="muted rt-tip">同一组设施改用最短路序串联，需步行 ${alt.nearest_order_min} 分钟，
           较出行链序减少 ${save} 分钟。差值即日常出行顺序产生的额外步行成本。</div>` : ''}
    ${recRows}
    ${missingRows}
    <div class="rt-list">${stopRows}</div>
    ${d.home_leg ? `<div class="rt-home">回家 ${d.home_leg.leave} · 步行 ${d.home_leg.walk_min} 分钟 /
      ${d.home_leg.walk_m} m</div>` : ''}
    <details class="rt-assume">
      <summary>情景假设（可调）</summary>
      <div class="rt-assume-body">
        <label>出发时刻 <input type="time" id="routeStart" value="${d.assumptions.start_clock}"></label>
        <label>步行速度 <input type="range" id="routeSpeed" min="0.4" max="1.6" step="0.05"
          value="${d.assumptions.walk_speed_mps}"> <b id="routeSpeedVal">${d.assumptions.walk_speed_mps}</b> m/s
          <span class="muted">（步速决定"15 分钟能走多远"：1.25 m/s → 1125 m，0.85 m/s → 765 m）</span>
        </label>
        <div class="muted">判定阈值 ${d.assumptions.cutoff_min} 分钟；可接受的单段步行
          ${d.assumptions.max_leg_min == null ? '不限' : d.assumptions.max_leg_min + ' 分钟'}。
          停留时长可在上面每处直接改，改完自动重算。</div>
        <div class="muted">${esc(d.assumptions.note)}</div>
        <div class="muted">出发点用居住组团质心（组团是 120 m 网格聚合出的分析单元），
          不是某一栋楼；路线为步行最短路，未建模过街等待与坡度。
          开放时段来自设施的常规营业时间，不是实采数据。</div>
      </div>
    </details>`;
  bindRouteControls();
}

/* ---------------------------------------------------------------- 等时圈（风圈）
   台风圈是理想圆，这里是真实路网上的等时圈：同样 15 分钟，隔一条河、一条快速路，
   形状完全不同。所以借的是风圈的视觉语言（同心分带 + 等时线 + 圈上刻度 + 可达面积比），
   而不是圆本身。口径由 /api/isochrone 给出（与报告图 2、GL 叠加图同一份栅格）。 */
function isoAxes(iso, proj) {
  const g = iso.grid;
  const mlon = 111320 * Math.cos((g.lat0 * Math.PI) / 180), mlat = 111320;
  const px = [], py = [];
  for (let c = 0; c <= g.ncols; c++) px.push(proj(g.lon0 + (c * g.cell_m) / mlon, g.lat0).x);
  for (let r = 0; r <= g.nrows; r++) py.push(proj(g.lon0, g.lat0 + (r * g.cell_m) / mlat).y);
  return { px, py };
}

/** 每圈在北缘的标注位置 —— canvas 与 GL 共用同一套算法，保证两种底图上标签落在同一处。 */
function isoBandLabels(iso) {
  const g = iso.grid, out = [];
  const mlon = 111320 * Math.cos((g.lat0 * Math.PI) / 180), mlat = 111320;
  for (let b = 0; b < iso.bands.length; b++) {
    let topR = -1;
    for (let r = 0; r < g.nrows; r++) if (g.rows[r].includes(String(b))) topR = r;
    if (topR < 0) continue;
    const cols = [];
    for (let c = 0; c < g.ncols; c++) if (g.rows[topR][c] === String(b)) cols.push(c);
    if (!cols.length) continue;
    const c = cols[Math.floor(cols.length / 2)];
    out.push({ band: b, lon: g.lon0 + (c + 0.5) * g.cell_m / mlon,
               lat: g.lat0 + (topR + 1) * g.cell_m / mlat });
  }
  return out;
}

/** 把风圈画在 canvas 上（视图变换之内，线宽/字号用 inv 反向缩放保持屏幕恒定）。
    phase='fill'：分层填充 + 等时线（要垫在路网下面）；
    phase='annot'：理想圆 + 圈上刻度 + 被切断的方向（要在最上层）。 */
function drawIso(ctx, proj, inv, phase) {
  const iso = state.iso && state.iso.data;
  if (!iso) return;
  const g = iso.grid, ax = isoAxes(iso, proj);
  const mlon = 111320 * Math.cos((g.lat0 * Math.PI) / 180), mlat = 111320;
  const hx = (iso.point.lon - g.lon0) * mlon, hy = (iso.point.lat - g.lat0) * mlat;
  const rIdeal = iso.compactness.radius_15_m;
  const home = proj(iso.point.lon, iso.point.lat);
  const cx = home.x, cy = home.y;
  ctx.save();

  if (phase === 'fill') {
    // 1) 分层填充；理想圆内却走不到的格子 = 被河道/快速路/断头路吃掉的空间
    //    "被吃掉"的格子单独一遍、压得更淡，免得把底图糊住
    for (let r = 0; r < g.nrows; r++) {
      const row = g.rows[r];
      const y = ax.py[r + 1], h = Math.max(1, ax.py[r] - y);
      for (let c = 0; c < g.ncols; c++) {
        if (row[c] !== '3') continue;
        const dx = (c + 0.5) * g.cell_m - hx, dy = (r + 0.5) * g.cell_m - hy;
        if (dx * dx + dy * dy <= rIdeal * rIdeal) {
          ctx.fillStyle = LOST_FILL;
          ctx.fillRect(ax.px[c], y, Math.max(1, ax.px[c + 1] - ax.px[c]), h);
        }
      }
    }
    ctx.globalAlpha = 0.72;
    for (let r = 0; r < g.nrows; r++) {
      const row = g.rows[r];
      const y = ax.py[r + 1], h = Math.max(1, ax.py[r] - y);
      for (let c = 0; c < g.ncols; c++) {
        if (row[c] === '3') continue;
        ctx.fillStyle = ISO_FILLS[+row[c]];
        ctx.fillRect(ax.px[c], y, Math.max(1, ax.px[c + 1] - ax.px[c]), h);
      }
    }
    ctx.globalAlpha = 1;

    // 2) 等时线：只在"由内向外"的格边上描，同一条线不画两遍
    const chAt = (r, c) => (r < 0 || r >= g.nrows || c < 0 || c >= g.ncols ? '3' : g.rows[r][c]);
    ctx.lineWidth = 1.8 * inv;
    for (let b = 0; b < 3; b++) {
      ctx.strokeStyle = ISO_LINES[b];
      ctx.beginPath();
      for (let r = 0; r < g.nrows; r++) {
        for (let c = 0; c < g.ncols; c++) {
          if (g.rows[r][c] !== String(b)) continue;
          const x0 = ax.px[c], x1 = ax.px[c + 1], y0 = ax.py[r], y1 = ax.py[r + 1];
          if (chAt(r - 1, c) !== String(b)) { ctx.moveTo(x0, y0); ctx.lineTo(x1, y0); }
          if (chAt(r + 1, c) !== String(b)) { ctx.moveTo(x0, y1); ctx.lineTo(x1, y1); }
          if (chAt(r, c - 1) !== String(b)) { ctx.moveTo(x0, y0); ctx.lineTo(x0, y1); }
          if (chAt(r, c + 1) !== String(b)) { ctx.moveTo(x1, y0); ctx.lineTo(x1, y1); }
        }
      }
      ctx.stroke();
    }
    ctx.restore();
    return;
  }

  // 3) 理想圆（虚线）：半径 = 步速 × 15 分钟。实际圈比它小多少 = 可达面积比
  //    半径常比画布还大，所以标签位置要夹回画布内
  const rp = proj.cell(rIdeal);
  ctx.setLineDash([6 * inv, 5 * inv]);
  ctx.lineWidth = 1.6 * inv;
  ctx.strokeStyle = 'rgba(90,90,90,.85)';
  ctx.beginPath();
  ctx.arc(cx, cy, rp, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = 'rgba(70,70,70,.95)';
  ctx.font = `600 ${Math.round(11 * inv)}px system-ui, sans-serif`;
  ctx.textAlign = 'center';
  const lim = state.canvasSize || { w: 900, h: 900 };
  ctx.fillText(`理想圆 r=${rIdeal} m`,
               Math.min(Math.max(cx + rp * 0.66, 80 * inv), lim.w * inv - 90 * inv),
               Math.min(Math.max(cy - rp * 0.66, 18 * inv), lim.h * inv - 20 * inv));

  // 4) 圈上刻度：每圈在北缘打一个标签（半径 / 面积 / 圈内凑齐几项服务）
  ctx.textBaseline = 'middle';
  isoBandLabels(iso).forEach((pt) => {
    const band = iso.bands[pt.band];
    const label = `${band.label} ${band.radius_m}m · ${band.area_km2}km² · `
      + `${band.elements_count}/${band.elements_total} 项`;
    ctx.font = `600 ${Math.round(11 * inv)}px system-ui, sans-serif`;
    const tw = ctx.measureText(label).width;
    const bx = proj(pt.lon, pt.lat).x, by = proj(pt.lon, pt.lat).y;
    ctx.globalAlpha = 0.92;
    ctx.fillStyle = '#fff';
    ctx.fillRect(bx - tw / 2 - 5 * inv, by - 17 * inv, tw + 10 * inv, 15 * inv);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = ISO_LINES[pt.band];
    ctx.lineWidth = 1.2 * inv;
    ctx.strokeRect(bx - tw / 2 - 5 * inv, by - 17 * inv, tw + 10 * inv, 15 * inv);
    ctx.fillStyle = '#1f2937';
    ctx.fillText(label, bx, by - 9.5 * inv);
  });

  // 5) 被切断的方向：从家往外画红箭头（河/快速路/断头路把这一侧切掉了）
  ctx.font = `600 ${Math.round(10.5 * inv)}px system-ui, sans-serif`;
  (iso.blind_directions || []).slice(0, 3).forEach((d) => {
    const ang = (d.deg * Math.PI) / 180;
    const ux = Math.sin(ang), uy = Math.cos(ang);
    const rr = Math.max(proj.cell(d.reach_m), 7 * inv);
    const ex = cx + ux * rr, ey = cy - uy * rr;
    ctx.strokeStyle = 'rgba(200,40,30,.9)';
    ctx.lineWidth = 2.5 * inv;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(ex, ey); ctx.stroke();
    ctx.fillStyle = '#c8281e';
    ctx.beginPath(); ctx.arc(ex, ey, 5 * inv, 0, Math.PI * 2); ctx.fill();
    ctx.lineWidth = 1.6 * inv; ctx.strokeStyle = '#fff'; ctx.stroke();
    ctx.fillStyle = '#a01f14';
    ctx.textAlign = 'left';
    ctx.fillText(`${d.label} ${d.reach_m}m`, ex + 7 * inv, ey - 7 * inv);
    ctx.textAlign = 'center';
  });
  ctx.restore();
  ctx.textAlign = 'start';
  ctx.textBaseline = 'alphabetic';
}

async function loadIsochrone(pointId) {
  const cid = $('community').value;
  state.isoLoading = pointId;
  try {
    const res = await fetch(`/api/isochrone?community=${encodeURIComponent(cid)}`
      + `&point=${encodeURIComponent(pointId)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '等时圈计算失败');
    state.iso = { pointId, data };
    renderIso();
    renderCanvas();
    renderLegend();
    if (state.mode === 'bmap') renderBMapOverlays();
  } catch (err) {
    state.iso = { pointId, error: err.message };
    renderIso();
    renderCanvas();
  } finally {
    state.isoLoading = null;
  }
}

function clearIso() {
  state.iso = null;
  renderIso();
  renderCanvas();
  renderLegend();
  if (state.mode === 'bmap') renderBMapOverlays();
}

function renderIso() {
  const box = $('isoBody');
  const who = $('isoWho');
  const iso = state.iso && state.iso.data;
  if (!iso) {
    who.textContent = '点击居住组团后显示';
    box.innerHTML = '<span class="muted">15 分钟生活圈并非几何圆。选择地图上任一居住组团，'
      + '即以该组团质心为圆心生成 5 / 10 / 15 分钟步行等时圈，并标注：'
      + '各圈半径与实际可达面积、圈内可达的必需服务数量、'
      + '被河道 / 快速路 / 断头路截断的方向。</span>';
    return;
  }
  if (state.iso.error) {
    box.innerHTML = `<span class="err">${esc(state.iso.error)}</span>`;
    return;
  }
  who.textContent = `以「${iso.point.name}」为圆心 · 步速 ${iso.walk_speed_mps} m/s`;
  const cp = iso.compactness;
  const bandRows = iso.bands.map((b, i) => `
    <div class="iso-band" title="圈内可达的必需要素：${esc(b.elements.map(nameOfElement).join('、') || '无')}">
      <i style="background:${ISO_FILLS[i]};border:1px solid ${ISO_LINES[i]}"></i>
      <span>${b.label} <span class="muted">半径 ${b.radius_m} m</span></span>
      <span class="n">${b.area_km2} km² · <b>${b.elements_count}/${b.elements_total}</b> 项</span>
    </div>`).join('');
  const last = iso.bands[iso.bands.length - 1];
  const blind = (iso.blind_directions || []).slice(0, 3);
  box.innerHTML = `
    <div class="iso-head">
      <b>${iso.point.name}</b>
      <span class="muted">${iso.point.households.toLocaleString()} 户 /
        ${iso.point.population.toLocaleString()} 人</span>
    </div>
    ${bandRows}
    <div class="iso-cp" title="可达面积比 = 实际可达面积 ÷ 同半径理想圆面积（同一张栅格上取并集，可比）">
      <span class="muted">可达面积比</span>
      <span class="bar"><span style="width:${Math.min(100, cp.ratio * 100).toFixed(0)}%"></span></span>
      <span class="v">${(cp.ratio * 100).toFixed(0)}%</span>
    </div>
    <div class="muted" style="font-size:12px;line-height:1.6">
      15 分钟实际可达 <b>${cp.actual_km2} km²</b>，同半径理想圆 <b>${cp.ideal_km2} km²</b>
      ，受路网折减 <b>${cp.lost_km2} km²</b>。
    </div>
    <div class="muted" style="font-size:12px;line-height:1.6;margin-top:4px">
      15 分钟圈内可达必需服务 <b>${last.elements_count}/${last.elements_total}</b> 项；
      ${last.elements_count >= last.elements_total
        ? '全部可达。'
        : `尚缺 ${last.elements_total - last.elements_count} 项，详见问题清单与规划建议。`}
    </div>
    ${blind.length ? `<div class="iso-blind"><b>被切断的方向：</b>`
      + blind.map((d) => `${d.label} 只能走 ${d.reach_m} m`).join('、')
      + `<div class="muted">其余方向平均可达 ${iso.mean_reach_m} m（最大 ${iso.max_reach_m} m）。`
      + `同一段步行时间，这几个方向被河道 / 快速路 / 封闭小区 / 断头路截断了 —— `
      + `新增设施若位于这些方向，受益范围将受到限制。</div></div>` : ''}
    ${(iso.extent_limited_directions || []).length ? `<div class="muted" style="font-size:12px">`
      + `${iso.extent_limited_directions.join('、')} 方向的可达半径由<b>分析范围边界</b>截断`
      + `（不是被路网切断），已排除在「被切断的方向」之外。</div>` : ''}
    <div class="iso-note">口径：以组团质心为圆心，沿真实步行路网算最短路并计入两端离路距离
      （未建模过街等待与坡度）；虚线圆为同半径理想圆，用于对照；面积与可达半径均只在
      虚线分析范围内统计。底图切换为「步行等时圈」时，该等时圈将叠加显示在地图上。</div>
    <div class="rt-tools" style="margin-top:8px">
      <button class="mini" id="isoRaster">叠加到地图</button>
      <button class="mini" id="isoClear">清除</button>
    </div>`;
  const btn = $('isoRaster');
  if (btn) btn.addEventListener('click', () => {
    state.raster = 'isochrone';
    $('rsCover').classList.remove('on');
    $('rsIso').classList.add('on');
    renderCanvas();
    renderLegend();
    if (state.mode === 'bmap') renderBMapOverlays();
  });
  $('isoClear').addEventListener('click', clearIso);
}

/* ---------------------------------------------------------------- 自选社区 */
function bindAddCommunity() {
  const panel = $('addPanel');
  let picked = null;                 // {name, lon, lat, address}

  $('addCommunity').addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) $('ncSearch').focus();
  });
  $('ncCancel').addEventListener('click', () => { panel.hidden = true; });

  const renderPicked = () => {
    const box = $('ncPicked');
    if (!picked) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `已选定中心：<b>${esc(picked.name)}</b>`
      + (picked.address ? `（${esc(picked.address)}）` : '')
      + `<br><span class="muted">WGS-84 ${picked.lon.toFixed(6)}, ${picked.lat.toFixed(6)}`
      + `　—— 分析范围以该点为中心</span>`;
  };

  const doSearch = async () => {
    const q = $('ncSearch').value.trim();
    const status = $('ncStatus');
    if (!q) { status.textContent = '请输入要搜索的街道/地点名称'; status.className = 'add-status error'; return; }
    status.className = 'add-status muted';
    status.textContent = `正在搜索「${q}」…`;
    $('ncResults').innerHTML = '';
    try {
      const r = await fetch(`/api/geocode/search?q=${encodeURIComponent(q)}`
        + `&city=${encodeURIComponent($('ncCity').value.trim() || '上海市')}`);
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || '搜索失败');
      if (!data.results.length) {
        status.className = 'add-status error';
        status.textContent = '没找到匹配的地点，换个关键词（如去掉"街道"两字、或加上区名）再试';
        return;
      }
      status.textContent = `找到 ${data.count} 个候选，点选一个作为分析中心`;
      $('ncResults').innerHTML = data.results.map((p, i) => `
        <button class="nc-item" data-i="${i}">
          <span><b>${esc(p.name)}</b><br><span class="nc-addr">${esc(p.address || '—')}</span></span>
          ${p.looks_like_subdistrict ? '<span class="nc-tag">像街道/社区</span>' : ''}
          ${p.is_government ? '<span class="nc-tag">办事机构</span>' : ''}
          <span class="nc-tag">${p.lon.toFixed(5)}, ${p.lat.toFixed(5)}</span>
        </button>`).join('');
      $('ncResults').querySelectorAll('.nc-item').forEach((el) => {
        el.addEventListener('click', () => {
          picked = data.results[Number(el.dataset.i)];
          if (!$('ncName').value.trim()) $('ncName').value = picked.name;
          renderPicked();
          status.className = 'add-status ok';
          status.textContent = '已选定，点「② 开始分析」拉取数据（约 20–60 秒）';
        });
      });
    } catch (err) {
      status.className = 'add-status error';
      status.textContent = '失败：' + err.message;
    }
  };
  $('ncSearchBtn').addEventListener('click', doSearch);
  $('ncSearch').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });

  $('ncSubmit').addEventListener('click', async () => {
    const status = $('ncStatus');
    status.className = 'add-status muted';
    if (!picked) {
      status.className = 'add-status error';
      status.textContent = '请先搜索并选定一个地点（第 ① 步）';
      return;
    }
    const body = {
      name: $('ncName').value.trim() || picked.name,
      lon: picked.lon, lat: picked.lat,          // 直接用选定的坐标，不再重复地理编码
      half_width: parseInt($('ncHalf').value || '800', 10),
      population: $('ncPop').value ? parseFloat($('ncPop').value) : undefined,
      area_km2: $('ncArea').value ? parseFloat($('ncArea').value) : undefined,
    };
    const t0 = Date.now();
    $('ncSubmit').disabled = true;
    const tick = setInterval(() => {
      status.textContent = `正在拉取真实数据（百度 POI + OSM 路网），已用 `
        + `${((Date.now() - t0) / 1000).toFixed(0)} 秒…`;
    }, 500);
    status.textContent = '正在拉取真实数据…';
    try {
      const res = await fetch('/api/communities', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '创建失败');
      clearInterval(tick);
      if (data.reused) {
        // 同一个社区重复添加：服务端直接复用已有的那份，不重建数据集
        status.className = 'add-status ok';
        status.textContent = data.detail;
        await loadCommunities(data.id);
        $('community').value = data.id;
        await loadBundle();
        setTimeout(() => { panel.hidden = true; }, 1500);
        return;
      }
      status.className = 'add-status ok';
      status.textContent = `完成：${data.community.name}（${data.residential} 个居住组团 / `
        + `${data.households.toLocaleString()} 户 / ${data.facilities} 处设施，`
        + `${((Date.now() - t0) / 1000).toFixed(0)} 秒）`;
      if (data.warning) {
        status.textContent += ' ⚠ ' + data.warning;
        status.className = 'add-status error';
      }
      await loadCommunities(data.community.id);
      $('community').value = data.community.id;
      await loadBundle();
      setTimeout(() => { panel.hidden = true; }, 1200);
    } catch (err) {
      clearInterval(tick);
      status.className = 'add-status error';
      status.textContent = '失败：' + err.message;
    } finally {
      $('ncSubmit').disabled = false;
    }
  });
}

function bindRouteControls() {
  const r = state.route;
  if (!r) return;
  document.querySelectorAll('[data-route-mode]').forEach((btn) => btn.addEventListener('click', () => {
    if (btn.dataset.routeMode === r.mode) return;
    loadRoute(r.pointId, { mode: btn.dataset.routeMode });
  }));
  document.querySelectorAll('[data-route-order]').forEach((btn) => btn.addEventListener('click', () => {
    if (btn.dataset.routeOrder === r.order) return;
    loadRoute(r.pointId, { order: btn.dataset.routeOrder });
  }));
  document.querySelectorAll('[data-stay]').forEach((inp) => inp.addEventListener('change', () => {
    const stay = { ...(r.stay || {}) };
    stay[inp.dataset.stay] = inp.value;
    loadRoute(r.pointId, { stay });
  }));
  const play = $('routePlay');
  if (play) play.addEventListener('click', playRoute);
  const png = $('routePng');
  if (png) png.addEventListener('click', () => {
    // 服务端渲染同一套动线（等时圈底图 + 折线 + 序号），新标签页打开后可直接另存
    window.open('/api/route.png?' + routeQuery(r).toString(), '_blank');
  });
  const clear = $('routeClear');
  if (clear) clear.addEventListener('click', clearRoute);
  const start = $('routeStart');
  if (start) start.addEventListener('change', () => loadRoute(r.pointId, { start: start.value }));
  // 步速滑杆：直接决定"15 分钟能走多远"，是最直观的一个可调参数
  const sp = $('routeSpeed');
  if (sp) {
    sp.addEventListener('input', () => { $('routeSpeedVal').textContent = (+sp.value).toFixed(2); });
    sp.addEventListener('change', () => loadRoute(r.pointId, { speed: sp.value }));
  }
}

/* ---------------------------------------------------------------- 数据加载 */
async function loadCommunities(selectId) {
  const res = await fetch('/api/communities');
  const data = await res.json();
  const list = data.communities || [];
  // 仓库不带真实数据（第三方 POI 再分发问题），克隆下来只有离线合成社区能直接出结果。
  // 没数据的社区照样列出来（说明这个社区跑过、怎么跑），但标注出来并默认不选它，
  // 免得一进来就弹 5 个"加载失败"。
  const want = selectId || $('community').value;
  const ready = list.filter((c) => c.ready);
  const pick = (want && list.some((c) => c.id === want && c.ready)) ? want
    : (ready.some((c) => c.id === data.active) ? data.active
      : (ready[0] || {}).id || data.active);
  state.communities = list;
  $('community').innerHTML = list.map((c) =>
    `<option value="${c.id}"${c.id === pick ? ' selected' : ''}>`
    + `${esc(c.name)}${c.custom ? '（自选）' : ''}`
    + `${c.ready ? '' : '（未生成数据）'}</option>`).join('');
}

/** 选中的社区没本地数据时，给一句能照做的提示，而不是抛原始网络错误。 */
function communityHint(cid) {
  const c = (state.communities || []).find((x) => x.id === cid);
  if (!c || c.ready) return '';
  return `「${c.name}」还没有本地数据集。仓库不含真实数据（第三方 POI 不宜再分发）：`
    + '先在根目录建 .env.local 填 BAIDU_SERVER_AK（见 .env.local.example），'
    + `再跑 python tools/add_community.py --name "…" --address "…"，或直接用内置的合成社区 `
    + 'caoyang-demo（无需 AK）。';
}

async function loadBundle() {
  const cid = $('community').value;
  $('mapHint').textContent = '计算中…';
  state.basemap = null;      // 换社区要重新取静态底图（中心不同）
  state.view = { k: 1, tx: 0, ty: 0 };
  // 没本地数据的社区先把"怎么补数据"说清楚，比抛一个原始网络错误有用得多
  const hint = communityHint(cid);
  if (hint) $('mapHint').textContent = hint;
  const res = await fetch(`/api/bundle?community=${encodeURIComponent(cid)}&k=${$('k').value}`);
  state.bundle = await res.json();
  if (!res.ok || state.bundle.detail) {
    $('mapHint').textContent = (state.bundle.detail || '加载失败') + (hint ? `　${hint}` : '');
    return;
  }
  state.opt = state.bundle.optimize;
  // 服务端在 bundle 顶层给了"优化后体检"（after_diag），挂到 opt 上统一使用
  if (state.opt && !state.opt.after_diag && state.bundle.after_diag) {
    state.opt.after_diag = state.bundle.after_diag;
  }
  const cfg = state.bundle.elements_config;
  const sitable = (cfg.elements || []).filter((e) => e.required && e.sitable);
  $('element').innerHTML = '<option value="">自动（缺口最大的可落地要素）</option>'
    + sitable.map((e) => `<option value="${e.key}">${esc(e.name)}</option>`).join('');
  // 报告入口走后端：自选社区首次打开会现生成一份（约 5–10 秒），避免直接访问静态文件撞 404
  const link = $('reportLink');
  link.href = `/api/report?community=${encodeURIComponent(cid)}`;
  link.title = '自选社区首次打开会在服务端现生成报告（约 5–10 秒），之后秒开';
  $('mapHint').textContent = `计算于 ${state.bundle.generated_at}`;
  state.coverMode = 'before';
  state.route = null;                 // 换社区后旧动线不再适用
  state.iso = null;                   // 风圈也一样（组团 id 只在本社区内有意义）
  renderPanel();
  renderRoute();
  renderIso();
  renderPersonaBar();
  renderCanvas();
  renderLegend();
  updateCoverSwitch();
  if (state.mode === 'bmap' && state.ak) initBMap(state.ak);
}

async function runOptimize() {
  const cid = $('community').value, k = $('k').value, el = $('element').value;
  $('opt').innerHTML = '<span class="muted">优化中…（会代入选址重算一次体检，约 3–6 秒）</span>';
  const res = await fetch(`/api/optimize?community=${encodeURIComponent(cid)}&k=${k}`
    + (el ? `&element=${encodeURIComponent(el)}` : ''));
  state.opt = await res.json();
  if (state.opt.after_diag) state.coverMode = 'after';   // 自动切到"优化后"覆盖图
  renderOpt();
  renderCanvas();
  renderLegend();
  updateCoverSwitch();
}

/* ---------------------------------------------------------------- 缩放与平移 */
function zoomAt(mx, my, factor) {
  const v = state.view;
  const k2 = Math.max(0.6, Math.min(10, v.k * factor));
  v.tx = mx - (mx - v.tx) * (k2 / v.k);
  v.ty = my - (my - v.ty) * (k2 / v.k);
  v.k = k2;
  renderCanvas();
}

function bindView() {
  const cv = $('map');
  cv.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const rect = cv.getBoundingClientRect();
    zoomAt(ev.clientX - rect.left, ev.clientY - rect.top, ev.deltaY < 0 ? 1.15 : 1 / 1.15);
  }, { passive: false });

  let dragging = false, last = null, moved = false;
  cv.style.cursor = 'grab';
  cv.addEventListener('mousedown', (ev) => {
    dragging = true; moved = false;
    last = { x: ev.clientX, y: ev.clientY };
    cv.style.cursor = 'grabbing';
  });
  window.addEventListener('mousemove', (ev) => {
    if (!dragging) return;
    const dx = ev.clientX - last.x, dy = ev.clientY - last.y;
    if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
    last = { x: ev.clientX, y: ev.clientY };
    state.view.tx += dx;
    state.view.ty += dy;
    renderCanvas();
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    cv.style.cursor = 'grab';
    if (moved) $('tooltip').hidden = true;
  });
  cv.addEventListener('dblclick', () => {
    const rect = cv.getBoundingClientRect();
    zoomAt(rect.width / 2, rect.height / 2, 1.5);
  });
  // 触摸：单指平移、双指缩放
  let pinch = null;
  cv.addEventListener('touchstart', (ev) => {
    if (ev.touches.length === 1) {
      last = { x: ev.touches[0].clientX, y: ev.touches[0].clientY };
    } else if (ev.touches.length === 2) {
      pinch = Math.hypot(ev.touches[0].clientX - ev.touches[1].clientX,
                         ev.touches[0].clientY - ev.touches[1].clientY);
    }
  }, { passive: true });
  cv.addEventListener('touchmove', (ev) => {
    const rect = cv.getBoundingClientRect();
    if (ev.touches.length === 1 && last) {
      state.view.tx += ev.touches[0].clientX - last.x;
      state.view.ty += ev.touches[0].clientY - last.y;
      last = { x: ev.touches[0].clientX, y: ev.touches[0].clientY };
      renderCanvas();
    } else if (ev.touches.length === 2 && pinch) {
      const d = Math.hypot(ev.touches[0].clientX - ev.touches[1].clientX,
                           ev.touches[0].clientY - ev.touches[1].clientY);
      const cx = (ev.touches[0].clientX + ev.touches[1].clientX) / 2 - rect.left;
      const cy = (ev.touches[0].clientY + ev.touches[1].clientY) / 2 - rect.top;
      zoomAt(cx, cy, d / pinch);
      pinch = d;
    }
  }, { passive: true });
  cv.addEventListener('touchend', () => { last = null; pinch = null; }, { passive: true });

  $('zoomIn').addEventListener('click', () => {
    const rect = cv.getBoundingClientRect();
    zoomAt(rect.width / 2, rect.height / 2, 1.3);
  });
  $('zoomOut').addEventListener('click', () => {
    const rect = cv.getBoundingClientRect();
    zoomAt(rect.width / 2, rect.height / 2, 1 / 1.3);
  });
  $('zoomReset').addEventListener('click', () => {
    state.view = { k: 1, tx: 0, ty: 0 };
    renderCanvas();
  });
}

/* ---------------------------------------------------------------- 悬浮提示 */
function hitTest(mx, my) {
  let best = null, bestD = 12;
  state.hit.forEach((h) => {
    const d = Math.hypot(h.x - mx, h.y - my);
    if (d < bestD) { bestD = d; best = h; }
  });
  return best;
}

function bindClick() {
  const cv = $('map');
  let downAt = null;
  let clickTimer = null;
  cv.addEventListener('mousedown', (ev) => { downAt = { x: ev.clientX, y: ev.clientY }; });
  cv.addEventListener('mouseup', (ev) => {
    if (!downAt) return;
    const moved = Math.hypot(ev.clientX - downAt.x, ev.clientY - downAt.y);
    downAt = null;
    if (moved > 4) return;                    // 拖拽平移不算点击
    const rect = cv.getBoundingClientRect();
    const hit = hitTest(ev.clientX - rect.left, ev.clientY - rect.top);
    if (!hit) return;
    // 双击是"放大"，不要顺手生成动线：等一小会儿确认不是双击再执行
    if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; return; }
    clickTimer = setTimeout(() => { clickTimer = null; handleClick(hit); }, 220);
  });
  cv.addEventListener('touchend', (ev) => {
    // 触摸端没有 hover，点一下就出信息（与鼠标点击同一条路径）
    const t = ev.changedTouches && ev.changedTouches[0];
    if (!t) return;
    const rect = cv.getBoundingClientRect();
    const hit = hitTest(t.clientX - rect.left, t.clientY - rect.top);
    if (hit) handleClick(hit);
  }, { passive: true });
}

function handleClick(hit) {
  if (hit.type === 'resid') {
    // 点一个住户：既给"他家"的 15 分钟风圈，也给"他的一天"
    loadIsochrone(hit.data.rp.id);
    loadRoute(hit.data.rp.id);
  } else if (hit.type === 'site') {
    const card = document.querySelectorAll('#opt .site')[hit.data.index - 1];
    if (card) {
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.add('flash');
      setTimeout(() => card.classList.remove('flash'), 1600);
    }
  }
}

function bindHover() {
  const cv = $('map'), tip = $('tooltip');
  cv.addEventListener('mousemove', (ev) => {
    const rect = cv.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    const best = hitTest(mx, my);
    if (!best) { tip.hidden = true; return; }
    tip.hidden = false;
    tip.style.left = mx + 'px';
    tip.style.top = my + 'px';
    if (best.type === 'poi') {
      const ic = iconDataUrl(best.data.element, ELEMENT_COLORS[best.data.element] || '#555', 30);
      tip.innerHTML = `<img class="tip-ico" src="${ic}"><b>${esc(best.data.name)}</b>`
        + `<br>${esc(nameOfElement(best.data.element))}`;
    } else if (best.type === 'resid') {
      const { rp, pt } = best.data;
      const iso = pt.assessable === false;
      const ic = iconDataUrl('residential', iso ? '#9aa0a6'
        : (pt.compliance_score >= 0.999 ? '#2ea043' : '#d62d20'), 30);
      tip.innerHTML = `<img class="tip-ico" src="${ic}"><b>${esc(rp.name)}</b>`
        + `<br>${rp.households} 户 / ${pt.population} 人`
        + (iso
           ? '<br><b>路网未连通（OSM 数据缺口）</b><br>该组团周边存在设施，但其吸附路段未与城市道路连通，'
              + '按「无法评估」处理，不计入覆盖率'
           : `<br>达标得分 ${(pt.compliance_score * 100).toFixed(0)}`
             + (pt.missing.length ? `<br>缺口：${esc(pt.missing.map(nameOfElement).join('、'))}` : '<br>要素齐备')
             + '<br><b class="tip-cta">点击 → 生成这个组团的一天动线</b>');
    } else {
      const ic = iconDataUrl('site', '#ff9100', 30);
      tip.innerHTML = `<img class="tip-ico" src="${ic}"><b>推荐点位 P${best.data.index}</b>`
        + `<br>新增覆盖 ${best.data.site.newly_covered_households} 户`;
    }
  });
  cv.addEventListener('mouseleave', () => { tip.hidden = true; });
}

function nameOfElement(key) {
  const el = ((state.bundle.elements_config || {}).elements || []).find((e) => e.key === key);
  return el ? el.name : key;
}

/* ---------------------------------------------------------------- 百度底图 */
function loadBMapScript(ak) {
  return new Promise((resolve, reject) => {
    if (window.BMapGL && window.BMapGL.Map) return resolve(window.BMapGL);
    const timer = setTimeout(() => reject(new Error(
      '加载超时（15 秒）：网络/代理或浏览器扩展拦截了 api.map.baidu.com，已改用服务端静态底图')), 15000);
    window.__dshBMapReady = () => {
      clearTimeout(timer);
      if (window.BMapGL && window.BMapGL.Map) resolve(window.BMapGL);
      else reject(new Error('脚本已返回但 BMapGL 未就绪：多为 AK 白名单/配额问题，已改用服务端静态底图'));
    };
    const s = document.createElement('script');
    s.src = `https://api.map.baidu.com/api?type=webgl&v=1.0&ak=${encodeURIComponent(ak)}&callback=__dshBMapReady`;
    s.onerror = () => { clearTimeout(timer); reject(new Error(
      '脚本请求被拒绝（网络/代理/广告拦截插件常见），已自动改用服务端静态底图')); };
    document.head.appendChild(s);
  });
}

function ringsOf(geo) {
  if (!geo) return [];
  if (geo.type === 'Polygon') return [geo.coordinates[0]];
  if (geo.type === 'MultiPolygon') return geo.coordinates.map((p) => p[0]);
  return [];
}

/** GL 模式的图标：用同一套语义图标，尺寸固定为屏幕像素（不再随缩放变大）。 */
function bmapIcon(BMapGL, key, color, size, title) {
  const icon = new BMapGL.Icon(iconCanvas(key, color, 64).toDataURL(),
                               new BMapGL.Size(size, size),
                               { anchor: new BMapGL.Size(size / 2, size / 2) });
  return { icon, title };
}

/** 推荐点位图标：图钉 + 右上角编号徽标，**画进同一张位图**。
    早期用 BMapGL.Label 单独挂编号，拖动地图时标签会与图钉脱节（各走各的），
    现在编号是图标的一部分，锚点与图钉完全一致。 */
function siteBadgeIcon(BMapGL, index) {
  const size = 30, px = 76;
  const cv = document.createElement('canvas');
  cv.width = cv.height = px;
  const c = cv.getContext('2d');
  const pin = iconCanvas('site', '#ff9100', 64);
  c.drawImage(pin, (px - 56) / 2 - 2, 12, 56, 56);
  const r = 15, cx = px - r - 1, cy = r + 1;
  c.beginPath(); c.arc(cx, cy, r, 0, Math.PI * 2);
  c.fillStyle = '#ffd166'; c.fill();
  c.lineWidth = 2.5; c.strokeStyle = '#7a3d00'; c.stroke();
  c.fillStyle = '#5c2d00';
  c.font = '700 18px system-ui, sans-serif';
  c.textAlign = 'center'; c.textBaseline = 'middle';
  c.fillText('P' + index, cx, cy + 1);
  return new BMapGL.Icon(cv.toDataURL(), new BMapGL.Size(size, size),
                         { anchor: new BMapGL.Size(size / 2, size * 0.62) });
}

const glState = { B: null, map: null, routeOverlays: [], routeDot: null };

/** 动线停靠点图标：要素图标 + 右上角序号（与画布模式一致的表达）。 */
function routeStopIcon(BMapGL, element, seq, color) {
  const size = 26, px = 72;
  const cv = document.createElement('canvas');
  cv.width = cv.height = px;
  const c = cv.getContext('2d');
  c.drawImage(iconCanvas(element, color || '#1f77b4', 64), 6, 10, 52, 52);
  const r = 15, cx = px - r - 1, cy = r + 1;
  c.beginPath(); c.arc(cx, cy, r, 0, Math.PI * 2);
  c.fillStyle = color || '#1f77b4'; c.fill();
  c.lineWidth = 2.5; c.strokeStyle = '#fff'; c.stroke();
  c.fillStyle = '#fff';
  c.font = '700 18px system-ui, sans-serif';
  c.textAlign = 'center'; c.textBaseline = 'middle';
  c.fillText(String(seq), cx, cy + 1);
  return new BMapGL.Icon(cv.toDataURL(), new BMapGL.Size(size, size),
                         { anchor: new BMapGL.Size(size / 2, size * 0.72) });
}

/** 把"一天动线"画到 JSAPI GL 地图上：实线走段 + 虚线离路段 + 序号停靠点 + 出发点。
    GL 模式下 canvas 是隐藏的，所以必须单独画一遍，否则只有面板时刻表、地图上什么都没有。 */
function drawGLRoute(refit = false) {
  const B = glState.B, map = glState.map;
  if (!B || !map) return;
  glState.routeOverlays.forEach((ov) => map.removeOverlay(ov));
  glState.routeOverlays = [];
  glState.routeDot = null;
  const r = state.route;
  if (!r || !r.data || !r.data.stops.length) return;
  const st = ROUTE_STYLE[r.data.mode] || ROUTE_STYLE.before;
  const push = (ov) => { map.addOverlay(ov); glState.routeOverlays.push(ov); return ov; };

  // 1) 离路段（虚线）：设施/住户到路网的最后几十米，直连会显得在穿墙
  const segs = r.data.stops.map((s) => ({ coords: s.leg_coords, stub: s.stub_from }));
  if (r.data.home_leg) {
    segs.push({ coords: r.data.home_leg.leg_coords, stub: r.data.home_leg.stub_from });
  }
  segs.forEach((s) => {
    if (!s.stub || !s.coords || !s.coords.length) return;
    push(new B.Polyline([
      bdPoint(B, s.stub[0], s.stub[1]),
      bdPoint(B, s.coords[0][0], s.coords[0][1]),
    ], { strokeColor: '#3b3b3b', strokeWeight: 2, strokeOpacity: 0.75, strokeStyle: 'dashed' }));
  });

  // 2) 路网上的实线（先白描边再上色，压在底图上看得清）
  segs.forEach((s) => {
    if (!s.coords || s.coords.length < 2) return;
    const pts = s.coords.map((c) => bdPoint(B, c[0], c[1]));
    push(new B.Polyline(pts, { strokeColor: '#ffffff', strokeWeight: 8, strokeOpacity: 0.85 }));
    push(new B.Polyline(pts, { strokeColor: st.line, strokeWeight: 4, strokeOpacity: 0.95 }));
  });

  // 3) 出发点（家）
  const homeIcon = new B.Icon(iconCanvas('residential', '#0b2e59', 64).toDataURL(),
                              new B.Size(22, 22), { anchor: new B.Size(11, 11) });
  const homeMk = new B.Marker(bdPoint(B, r.data.point.lon, r.data.point.lat), { icon: homeIcon });
  homeMk.setTitle(`出发点：${r.data.point.name}（${r.data.point.households} 户）`);
  push(homeMk);

  // 4) 序号停靠点
  r.data.stops.forEach((s) => {
    const mk = new B.Marker(bdPoint(B, s.lon, s.lat), {
      icon: routeStopIcon(B, s.element, s.seq, st.badge) });
    mk.setTitle(`第 ${s.seq} 站｜${s.facility_name}（${s.element_name}）｜`
      + `${s.arrive}–${s.leave}｜步行 ${s.walk_min} 分钟 / ${s.walk_m} m`);
    push(mk);
  });

  // 5) 播放用的小圆点
  const dotIcon = new B.Icon(iconCanvas('site', '#ff9100', 64).toDataURL(),
                             new B.Size(16, 16), { anchor: new B.Size(8, 8) });
  glState.routeDot = push(new B.Marker(bdPoint(B, r.data.point.lon, r.data.point.lat),
                                       { icon: dotIcon }));
  glState.routeDot.setTitle('一天动线播放中');
  glState.routeDot.hide && glState.routeDot.hide();

  // 6) 把视野调整到能看全动线（只在刚生成动线时做，避免和用户的缩放打架）
  const all = segs.flatMap((s) => s.coords || []);
  if (refit && all.length > 1) {
    const lons = all.map((c) => c[0]), lats = all.map((c) => c[1]);
    map.setViewport([
      bdPoint(B, Math.min(...lons), Math.min(...lats)),
      bdPoint(B, Math.max(...lons), Math.max(...lats)),
    ]);
  }
  const status = $('mapStatus');
  if (status) {
    status.textContent = `一天动线：${r.data.point.name} · ${r.data.totals.stops} 处停靠 · `
      + `步行 ${r.data.totals.walk_min} 分钟 · ${r.data.totals.start_clock}–${r.data.totals.end_clock}`
      + `｜缺口 ${r.data.totals.missing_count} 项`;
  }
}

/** 是否落在**分析范围**内（可外扩 margin）。
    抓取范围外扩 1200 m 是为了算得准，但不该把框外的路网/设施画到地图上——又乱又误导。 */
function inAnalysisBox(lon, lat, marginM = 0) {
  const c = state.bundle.community.center, e = state.bundle.community.extent_m;
  const mlon = 111320 * Math.cos((c.lat * Math.PI) / 180), mlat = 111320;
  return Math.abs((lon - c.lon) * mlon) <= e.half_width + marginM
      && Math.abs((lat - c.lat) * mlat) <= e.half_height + marginM;
}

/** 按"图层开关 + 缩放级别 + 分析范围"重建 GL 叠加层（拖动/缩放不会重建，只有切换才重建）。 */
async function renderBMapOverlays() {
  const B = glState.B, map = glState.map, b = state.bundle;
  if (!B || !map || !b) return;
  const status = $('mapStatus');
  const zoom = map.getZoom();
  map.clearOverlays();
  glState.routeOverlays = [];        // clearOverlays 会把动线一起清掉，最后重画一遍

  // 1) 覆盖色块：服务端渲的透明 PNG，按分析范围贴上去（口径与"体检底图"完全一致）
  //    已有风圈时不再贴并集分带（两套圈含义不同，叠着看只会糊）
  const hasFocus = !!(state.iso && state.iso.data);
  let overlayNote = '未加载覆盖色块';
  const wantUnion = state.layers.cover && !(hasFocus && state.raster === 'isochrone');
  if (wantUnion) {
    try {
      const pm = state.coverMode === 'after' ? 'after' : 'before';
      const meta = await fetch(`/api/overlay.json?community=${encodeURIComponent($('community').value)}`
        + `&raster=${state.raster === 'isochrone' ? 'isochrone' : 'cover'}&mode=${pm}&k=${$('k').value}`)
        .then((r) => r.json());
      if (meta.ok) {
        const sw = wgs84ToBd09(meta.bounds_wgs84.sw[0], meta.bounds_wgs84.sw[1]);
        const ne = wgs84ToBd09(meta.bounds_wgs84.ne[0], meta.bounds_wgs84.ne[1]);
        const bounds = new B.Bounds(new B.Point(sw[0], sw[1]), new B.Point(ne[0], ne[1]));
        map.addOverlay(new B.GroundOverlay(bounds, {
          imageURL: meta.image_url, opacity: 0.62,
          displayOnMinLevel: 11, displayOnMaxLevel: 20,
        }));
        overlayNote = `覆盖色块（${meta.raster === 'isochrone' ? '步行等时圈' : '可达要素数量'}`
          + `${pm === 'after' ? '·优化后' : ''}）`;
      }
    } catch (err) {
      overlayNote = '覆盖色块加载失败：' + err.message;
    }
  }

  // 1.5) 分析范围框（虚线）：抓取范围外扩 1200 m（只补路网与设施），底图上框外仍有
  //      住宅区与设施 POI。画出分析边界，读者才知道色块与统计口径到哪里为止。
  {
    const ring = analysisRing(b);
    map.addOverlay(new B.Polygon(ring.map((p) => bdPoint(B, p[0], p[1])), {
      strokeColor: '#1c2a4a', strokeWeight: 1.5, strokeOpacity: 0.95,
      strokeStyle: 'dashed', fillColor: '#ffffff', fillOpacity: 0,
    }));
    const lb = new B.Label('<div class="gl-frame-label">分析范围（框外不参与体检）</div>',
      { offset: new B.Size(6, 8) });
    lb.setPosition(bdPoint(B, ring[3][0], ring[3][1]));      // 西北角
    lb.setStyle({ border: '0', background: 'transparent', padding: '0' });
    map.addOverlay(lb);
  }

  // 2) 15 分钟生活圈范围（并集多边形）；有风圈时让位给风圈，避免两套圈叠着看
  if (b.circle_geojson && !(state.iso && state.iso.data)) {
    ringsOf(b.circle_geojson).forEach((ring) => {
      const pts = ring.map((p) => bdPoint(B, p[0], p[1]));
      map.addOverlay(new B.Polygon(pts, {
        strokeColor: '#1f77b4', strokeWeight: 2, strokeOpacity: 0.9,
        fillColor: '#3fa7d6', fillOpacity: 0.12,
      }));
    });
  }

  // 2.5) 单组团风圈：服务端渲的透明 PNG（含分层、等时线、理想圆），
  //      与 canvas 模式共用同一份 iso["grid"]，所以两种底图上的圈完全一致。
  //      圈上的刻度另外用 GL 标签画一遍 —— 位图里的字一缩放就糊，标签始终清晰。
  if (state.iso && state.iso.data) {
    try {
      const meta = await fetch(`/api/isochrone.json?community=${encodeURIComponent($('community').value)}`
        + `&point=${encodeURIComponent(state.iso.pointId)}`).then((r) => r.json());
      if (meta.ok) {
        const sw = wgs84ToBd09(meta.bounds_wgs84.sw[0], meta.bounds_wgs84.sw[1]);
        const ne = wgs84ToBd09(meta.bounds_wgs84.ne[0], meta.bounds_wgs84.ne[1]);
        map.addOverlay(new B.GroundOverlay(
          new B.Bounds(new B.Point(sw[0], sw[1]), new B.Point(ne[0], ne[1])),
          { imageURL: meta.image_url, opacity: 0.86,
            displayOnMinLevel: 12, displayOnMaxLevel: 20 }));
        isoBandLabels(state.iso.data).forEach((pt) => {
          const band = state.iso.data.bands[pt.band];
          const lb = new B.Label(
            `<div class="gl-iso-label" style="border-color:${ISO_LINES[pt.band]}">`
            + `${band.label} ${band.radius_m}m · ${band.area_km2}km² · `
            + `${band.elements_count}/${band.elements_total} 项</div>`,
            // 标签默认从锚点往右铺，贴到视口右缘就会被切掉 —— 整体左移，让它落在圈内
            { offset: new B.Size(-96, -20) });
          lb.setPosition(bdPoint(B, pt.lon, pt.lat));
          lb.setStyle({ border: '0', background: 'transparent', padding: '0' });
          map.addOverlay(lb);
        });
        overlayNote += ` ＋ 风圈（${state.iso.data.point.name}）`;
      }
    } catch (err) {
      overlayNote += '（风圈叠加失败：' + err.message + '）';
    }
  }

  // 3) 三类点要素：遵守图层开关 + 只在分析范围内画 + 低缩小时少画
  let nPoi = 0, nResid = 0, nSite = 0;
  const addMarker = (lon, lat, icon, size, title, onClick) => {
    const mk = new B.Marker(bdPoint(B, lon, lat), { icon: icon(icon, size) });
    if (title) mk.setTitle(title);
    if (onClick) mk.addEventListener('click', onClick);
    map.addOverlay(mk);
    return mk;
  };

  if (state.layers.sites && state.opt && state.opt.sites) {
    state.opt.sites.forEach((s, i) => {
      const icon = siteBadgeIcon(B, i + 1);
      const mk = new B.Marker(bdPoint(B, s.lon, s.lat), { icon });
      mk.setTitle(`推荐点位 P${i + 1}｜单点新增覆盖 ${s.newly_covered_households.toLocaleString()} 户`
        + `｜${(s.carrier || {}).name || ''}`);
      mk.addEventListener('click', () => handleClick({ type: 'site', data: { index: i + 1 } }));
      map.addOverlay(mk);
      nSite += 1;
    });
  }

  if (state.layers.resid) {
    (activeDiag() || b.diag).points.forEach((pt) => {
      const rp = b.dataset.residential.find((r) => r.id === pt.id);
      if (!rp || !inAnalysisBox(rp.lon, rp.lat)) return;
      const size = Math.round(Math.max(18, Math.min(26, 16 + pt.households / 900)));
      const color = pt.assessable === false ? '#9aa0a6'
        : (pt.compliance_score >= 0.999 ? '#2ea043' : '#d62d20');
      const missing = pt.assessable === false ? '路网未连通（无法评估）'
        : (pt.missing.length ? `缺口：${pt.missing.map(nameOfElement).join('、')}` : '要素齐备');
      const mk = new B.Marker(bdPoint(B, rp.lon, rp.lat), {
        icon: bmapIcon(B, 'residential', color, size).icon });
      mk.setTitle(`${rp.name}｜${rp.households} 户 / ${pt.population} 人｜${missing}`);
      mk.addEventListener('click', () => loadRoute(rp.id));      // 与 canvas 模式一致：点组团出一天动线
      map.addOverlay(mk);
      nResid += 1;
    });
  }

  // 设施点多（曹杨 100+ 处），GL 底图本身已带大量地名标注，全画会糊成一片：
  // 只在放大到 16 级、且图层开关打开时才画
  const showPoi = state.layers.poi && zoom >= 16;
  if (showPoi) {
    b.dataset.facilities.forEach((f) => {
      if (!inAnalysisBox(f.lon, f.lat)) return;
      addMarker(f.lon, f.lat, (key, size) => bmapIcon(B, key, ELEMENT_COLORS[f.element] || '#555',
                                                     size).icon, 16,
                `${f.name}（${nameOfElement(f.element)}）`);
      nPoi += 1;
    });
  }

  // 视野自适应到分析范围
  const c0 = b.community.center, ex = b.community.extent_m;
  const dLat = ex.half_height / 111320;
  const dLon = ex.half_width / (111320 * Math.cos((c0.lat * Math.PI) / 180));
  map.setViewport([bdPoint(B, c0.lon - dLon, c0.lat - dLat),
                   bdPoint(B, c0.lon + dLon, c0.lat + dLat)]);

  // 已有"一天动线"的话重画一遍（clearOverlays 会把它清掉），但不要再次调整视野
  const hadRoute = !!(state.route && state.route.data);
  drawGLRoute(false);

  if (!hadRoute) status.textContent = `${overlayNote} · 组团 ${nResid} 个（绿=达标 / 红=有短板 / 灰=路网未连通） · `
    + `推荐点位 ${nSite} 处 · `
    + (showPoi ? `设施 ${nPoi} 处`
               : `设施：${state.layers.poi ? '放大到 16 级显示' : '已关闭（可在下方勾选）'}`)
    + `　|　点房子可生成一天动线`;
}

async function initBMap(ak) {
  const b = state.bundle;
  const status = $('mapStatus');
  try {
    const BMapGL = await loadBMapScript(ak);
    glState.B = BMapGL;
    if (!glState.map) {
      glState.map = new BMapGL.Map('bmapWrap');
      glState.map.centerAndZoom(bdPoint(BMapGL, b.community.center.lon, b.community.center.lat), 15);
      glState.map.enableScrollWheelZoom(true);
      glState.map.addEventListener('zoomend', () => renderBMapOverlays());
    }
    await renderBMapOverlays();
  } catch (err) {
    status.textContent = '百度底图不可用：' + err.message + '（已切回服务端静态底图，底图仍为真实地图）';
    // 自动回退：用服务端静态底图，保证 Demo 一定有真地图可看
    document.querySelector('.tabs button[data-mode="canvas"]').click();
    state.layers.basemap = true;
    $('ly_basemap').checked = true;
    state.basemap = null;
    renderCanvas();
  }
}

/* ---------------------------------------------------------------- 初始化 */
/** 底图口径切换：可达要素数量 / 步行等时圈（图层开关、演示脚本都走这里）。 */
function setRaster(mode) {
  state.raster = mode === 'isochrone' ? 'isochrone' : 'cover';
  $('rsCover').classList.toggle('on', state.raster === 'cover');
  $('rsIso').classList.toggle('on', state.raster === 'isochrone');
  renderCanvas();
  renderLegend();
  if (state.mode === 'bmap') renderBMapOverlays();
}

/* ---------------------------------------------------------------- 一键演示
   自动播放一遍完整故事：体检发现问题 → 换成人看圈 → 切规划 → 出处方 → 看覆盖 →
   看典型住户的一天 → 换个人对比 → 贴到百度地图。
   设计要点：
   - 字幕里的数字**实时取自当前数据**，不写死，换社区/换数据也不会说错话；
   - 单步失败只记一条"已跳过"，不中断整场演示（GL 那一步在没有 AK 时会走到这里）；
   - 回退/跳转靠"从第 0 步快速重放"实现 —— 各步会改全局状态，撤销比重放更容易出错；
   - 演示中用户碰地图会自动暂停，不跟人抢操作。
   ------------------------------------------------------------------ */
const demo = {
  on: false, paused: false, i: 0, mode: 'quick', token: 0, notes: [], flash: [],
  spotEl: null,               // 当前高亮的目标元素（scroll / resize 时要重算位置）
  spotEl2: null,              // 第二处同时高亮的目标（如第 ⑨ 步的地图）
  spotBox: null,              // 高亮框（视口坐标，已含外扩）
  spotBox2: null,
  spotTag: '',                // 第二处高亮的角标文字
  cardSide: 'right',
  holdStart: 0, holdTotal: 0,  // 用于指向卡的迷你进度条
  curBody: '',                 // 本步正文（暂停/恢复时回填）
  replaying: false,            // 正在快速重放前置步骤（跳过等人的等待）
  debug: false,                // ?demodebug=1：把几何数字写进卡片，便于无头截图核对
  ro: null,                    // 目标元素的 ResizeObserver
  ro2: null,
  roTarget: null,              // 观察器当前盯着的元素（只建一次，换目标才 unobserve/observe）
  ro2Target: null,
  lastKey: '',                 // 上一次摆放时的目标矩形键（没变就不重摆，防无限帧循环）
  raf: 0,
};

const dg = () => state.bundle && state.bundle.diag;
const dsum = () => (dg() || {}).summary || {};
const dEl = (sel) => document.querySelector(sel);
const dPct = (v) => (v * 100).toFixed(1) + '%';

/** 演示默认讲这个组团（有代表性、要素齐备、离社区中部近）；取不到就现挑一个。 */
function demoPoint() {
  const pts = ((dg() || {}).points || []).filter((p) => p.assessable !== false);
  if (!pts.length) return null;
  const want = pts.find((p) => p.id === 'CR029');
  if (want) return want.id;
  const good = pts.filter((p) => (p.missing || []).length <= 1);
  (good.length ? good : pts).sort((a, b) => b.households - a.households);
  return good.length ? good[0].id : pts[0].id;
}

function demoClearMarks() {
  demo.flash.forEach((el) => el.classList.remove('demo-flash', 'demo-point'));
  demo.flash = [];
}

function demoMark(sel, cls) {
  const el = typeof sel === 'string' ? dEl(sel) : sel;
  if (!el) return null;
  el.classList.add(cls);
  demo.flash.push(el);
  return el;
}

/** 把某张卡片滑到面板可视区中间。
    直接算 panel.scrollTop 而不用 scrollIntoView —— 后者会连**整个页面**一起滚，
    地图就被推出屏幕了（演示时实测踩到：地图被推到 y=-1375）。 */
function demoScrollTo(sel, opts = {}) {
  const el = typeof sel === 'string' ? dEl(sel) : sel;
  if (!el) return;
  const card = el.closest('.card, details, .score-card') || el;
  const panel = dEl('.panel');
  if (panel && panel.scrollHeight > panel.clientHeight + 4) {
    const pr = panel.getBoundingClientRect(), cr = card.getBoundingClientRect();
    // 卡片比面板还高时**不能居中**：居中项的偏移是负的，会把卡片顶部推到面板上方，
    // 目标元素跟着落到视口外 —— 高亮框于是量不到矩形、整个消失（第 ⑨⑩ 步）。
    // 这种情况改成顶对齐，留一点边距。
    const offset = cr.height >= panel.clientHeight - 8
      ? 12 : (panel.clientHeight - cr.height) / 2;
    const top = Math.max(0, panel.scrollTop + (cr.top - pr.top) - offset);
    // instant：平滑滚动会缓入缓出、还可能被下一次 scrollTo 打断，量位置时不能用它
    if (opts.instant) panel.scrollTop = top;
    else panel.scrollTo({ top, behavior: 'smooth' });
  } else if (opts.instant) {
    card.scrollIntoView({ block: 'start' });
  } else {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

/** 目标是否有足够一块露在可视区里（顶部条以下、视口底以上）。 */
function demoInView(el) {
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return false;
  const visTop = Math.max(demoBarH(), r.top);
  const visBottom = Math.min(window.innerHeight, r.bottom);
  return visBottom - visTop >= 24;
}

/**
 * 可中断、可暂停的等待：token 变了就立刻返回 'abort'。
 * 传了 total 时顺便驱动指向卡里的迷你进度条与倒计时读秒。
 *
 * **用 setTimeout 而不是 requestAnimationFrame**。踩过三次同一类坑：
 * 无头浏览器里 rAF 会因为页面隐藏/被冻结/虚拟时钟而完全停摆，而演示里所有
 * "等一会儿再走下一步"都挂在这上面 —— 结果是页面看着还活着，演示却永远停在原地
 * （逐步自检跑两步就 TIMEOUT）。定时器不会这样：最坏情况被节流到 1 s 一次，
 * 但一定会来。进度条本来也不需要每帧刷新。
 */
function demoWait(ms, t0, total) {
  return new Promise((resolve) => {
    const tok = demo.token;
    let acc = 0, last = performance.now();
    const tick = () => {
      if (tok !== demo.token) return resolve('abort');
      const now = performance.now();
      if (!demo.paused) acc += now - last;
      last = now;
      const bar = $('dcProg');
      const timer = $('dcTimer');
      if (bar && total) {
        const done = Math.max(0, total - ms) + acc;
        bar.style.width = Math.min(100, (done / total) * 100).toFixed(1) + '%';
        if (timer) timer.textContent = Math.max(0, Math.ceil((total - done) / 1000)) + 's';
      }
      if (acc >= ms) return resolve('done');
      setTimeout(tick, 60);
    };
    setTimeout(tick, 0);
  });
}

/** 有上限的轮询等待（演示里等 GL 就绪用；不参与暂停逻辑，超时就放弃）。 */
async function demoWaitFor(pred, ms) {
  const tok = demo.token;
  const t0 = performance.now();
  while (performance.now() - t0 < ms) {
    if (tok !== demo.token) return false;
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}
/* ================= 一键演示：聚光蒙版 + 指向卡 =================
   一句话口径：**文字全部在指向卡里，顶部控制条只留 暂停 / 步数 / 退出**。

   三个刻意的取舍：
   1. 蒙版用"四块矩形"而不是 box-shadow 大扩散 —— 宽屏下 .panel 与 .map-card 各自滚动，
      高亮框在视口坐标里会随内层滚动漂移；四块矩形只要在 scroll/resize 时重算即可。
   2. 蒙版 pointer-events:none —— 蒙住的是"还没讲到的区域"，但被高亮的那颗按钮必须
      仍然可点（第 ④⑦ 步就是邀请观众自己点）。
   3. 指向线用 SVG 折线 + 三角箭头，不用 👉 —— 彩色 emoji 在 Windows / macOS 上画法差别
      很大，压在深色蒙版上录进视频不专业。👉 只作为卡片里的点缀。 */

const DEMO_CARD_W = 344;
// 指向卡与高亮框的间距：留足 56 px 而不是贴着放，否则那条折线只有十几像素长，
// 看起来像卡片的描边而不是"指向线"（实拍对比过）。
const DEMO_GAP = 56;
const DEMO_EDGE = 12;        // 指向卡距视口边缘的最小留白

function demoBarH() {
  const bar = $('demoBar');
  return bar.hidden ? 0 : (bar.offsetHeight || 38);
}

/** 指向卡正文（也用于估算停留时长）：run() 放动作，caption() 放话术。 */
function demoSetCard(step, i, bodyHTML, opts = {}) {
  const c = $('demoCard');
  const hint = opts.hint || step.hint || '';
  c.innerHTML =
    `<div class="dc-head"><span class="dc-step">第 ${i + 1} 步 / 共 ${DEMO_STEPS.length} 步</span>`
    + `<span class="dc-title">${esc(step.group || step.title)}</span></div>`
    + `<div class="dc-body">${bodyHTML}</div>`
    + (hint ? `<div class="dc-hint">👉 ${esc(hint)}</div>` : '')
    + `<div class="dc-foot"><span class="dc-track"><i id="dcProg"></i></span>`
    + `<span class="muted" id="dcTimer" style="font-size:11.5px"></span>`
    + `<button id="dcPause" title="空格">${demo.paused ? '▶ 继续' : '⏸ 暂停'}</button>`
    + `<button id="dcAgain" title="R：重播本步">↺ 重播</button></div>`
    + (demo.debug ? '<div class="muted" id="dcDbg" style="font-size:10.5px;word-break:break-all"></div>' : '');
  c.hidden = false;
  const p = $('dcPause');
  if (p) p.addEventListener('click', demoTogglePause);
  const again = $('dcAgain');
  if (again) again.addEventListener('click', demoReplayStep);
  if (opts.place !== false && demo.spotBox) demoPlaceCard(demo.spotBox);
  return c;
}

/** 只更新正文（高亮框移动时用，避免重建卡片丢掉进度条与按钮监听）。 */
function demoUpdateBody(html) {
  const b = dEl('#demoCard .dc-body');
  if (b) b.innerHTML = html;
}

/** 顶部控制条：步数 + 总进度 + 暂停态。描述文字不在这里。 */
function demoBar(i, step) {
  $('demoStep').textContent = `第 ${i + 1} / ${DEMO_STEPS.length} 步`;
  $('demoProg').style.width = ((i + 1) / DEMO_STEPS.length * 100).toFixed(1) + '%';
  $('demoBar').classList.toggle('paused', demo.paused);
  $('demoPause').textContent = demo.paused ? '▶ 继续' : '⏸ 暂停';
  const cp = $('dcPause');
  if (cp) cp.textContent = demo.paused ? '▶ 继续' : '⏸ 暂停';
}

function demoPending(text) {
  demoUpdateBody(`<span class="muted">⏳ ${esc(text)}</span>`);
}

/* ---------- 聚光：四块蒙版 + 高亮框 + 指向线 ---------- */

/* 注意：`el.hidden = false` 对 **SVG 元素无效** —— `hidden` 是 HTMLElement 的 IDL 属性，
   SVGElement 上没有它，赋值只是挂了个同名的 JS 属性，HTML 上的 hidden 属性还在，
   于是 CSS 的 [hidden]{display:none} 继续生效（实拍踩到：高亮框正常、指向线死活不出现）。
   统一走 toggleAttribute，对 HTML 与 SVG 都成立。 */
const demoShow = (el, on) => { if (el) el.toggleAttribute('hidden', !on); };

function demoHideSpot() {
  demoShow($('demoSpot'), false);
  demoShow($('demoRing'), false);
  demoShow($('demoRing2'), false);
  demoShow($('demoTag'), false);
  demoShow($('demoArrow'), false);
  demo.spotEl = null;
  demo.spotEl2 = null;
  demo.spotBox = null;
  demo.spotBox2 = null;
  demo.lastKey = '';
  demoShow($('demoSpot'), false);
  demoShow($('demoRing'), false);
  demoShow($('demoRing2'), false);
  demoShow($('demoTag'), false);
  demoShow($('demoArrow'), false);
  if (demo.ro) { demo.ro.disconnect(); demo.ro = null; demo.roTarget = null; }
  if (demo.ro2) { demo.ro2.disconnect(); demo.ro2 = null; demo.ro2Target = null; }
}

/**
 * 高亮某个元素并把指向卡摆到空间最大的一侧。
 *
 * 定位**之前**必须先把目标滚到完整可见 —— 面板里被裁掉一半的卡片会让框落在视口外，
 * 于是指向线指到空气上。等 320 ms 让平滑滚动停下再量 rect。
 */
/**
 * 等目标"停下来"再量它的位置：平滑滚动要 300–500 ms 才停，写死一个 320 ms
 * 经常在滚动中途量到越界的矩形（第 ⑩ 步的高亮框就是这么丢的）。
 * 现在改成轮询矩形直到连续两次不变，最多等 maxMs。
 */
async function demoWaitStable(el, maxMs = 1500) {
  const tok = demo.token;
  const t0 = performance.now();
  let prev = '';
  let same = 0;
  while (performance.now() - t0 < maxMs) {
    if (tok !== demo.token) return;
    const key = demoRectKey(el);
    if (key === prev) { if (++same >= 2) return; } else { same = 0; prev = key; }
    await new Promise((r) => setTimeout(r, 60));
  }
}

/**
 * 滚到目标**真的进了可视区**为止。
 *
 * 为什么不是"滚一次 + 等 320 ms"：平滑滚动会先缓入再缓出，写死的等待经常在滚动中途
 * 就返回，量到的矩形还在视口外 —— 高亮框量不到尺寸、整个消失。而且卡片高度会随数据
 * 填充变化，"第一次滚到位"不代表"量的时候还在"。
 * 这里改成：量 → 不在视口里就再滚 → 第二轮起直接硬跳（instant），最多三轮，
 * 最后再兜一次硬跳。平滑滚动只负责"好看"，收敛由硬跳保证。
 */
async function demoEnsureVisible(el, tries = 3) {
  for (let i = 0; i < tries; i++) {
    await demoWaitStable(el);
    if (demoInView(el)) return true;
    if (i === tries - 1) break;
    demoScrollTo(el, { instant: i > 0 });
  }
  demoScrollTo(el, { instant: true });
  await demoWaitStable(el, 600);
  return demoInView(el);
}

async function demoSpotlight(sel, opts = {}) {
  const el = typeof sel === 'string' ? dEl(sel) : sel;
  const el2 = typeof opts.also === 'function' ? opts.also()
    : (typeof opts.also === 'string' ? dEl(opts.also) : (opts.also || null));
  demo.spotEl = el;
  demo.spotEl2 = el2 || null;
  demo.spotTag = opts.tag || '';
  if (!el) {                       // 目标不存在：不指空气，卡片居中并写明
    demo.spotBox = null;
    demoShow($('demoSpot'), true);
    demoShow($('demoRing'), false);
    demoShow($('demoRing2'), false);
    demoShow($('demoArrow'), false);
    demoPlaceCard(null);
    return null;
  }
  demoScrollTo(el);
  // 重放/预览时不用等落定（那是给"人眼看"的）；正式播放时等目标真的进了可视区再量
  if (demo.replaying) await demoWait(0);
  else if (opts.settle !== undefined) await demoWait(opts.settle);
  else await demoEnsureVisible(el);
  return demoPlaceSpot(el, opts);
}

/**
 * 观察目标尺寸变化（面板里的卡片会因为 <details> 展开、数据填充而变高）。
 *
 * 两个必须守的规矩 —— 违反任意一条都会变成**无限帧循环**，表现为页面的 rAF 被占满、
 * 演示里所有基于 requestAnimationFrame 的等待永远不返回（第 ⑧⑨ 步就是这么卡死的）：
 * 1. **观察器只建一次**，目标变了就 unobserve 旧的、observe 新的。
 *    ResizeObserver 在 observe() 时一定会先投递一次初始回调 —— 每次重摆都新建观察器，
 *    就等于"重摆 → 触发观察 → 再重摆"的自我激励循环。
 * 2. **位置尺寸没变就不重摆**（demoQueueReplace 里的 lastKey 比较）。
 * 另外地图画布不观察：它的尺寸由窗口 resize 驱动，观察它只会平白引入反馈回路。
 */
function demoSyncObservers() {
  const skip = (el) => !el || el.id === 'canvasWrap' || el.id === 'bmapWrap';
  if (!demo.ro) demo.ro = new ResizeObserver(() => demoQueueReplace());
  if (demo.roTarget !== demo.spotEl) {
    if (demo.roTarget) demo.ro.unobserve(demo.roTarget);
    if (!skip(demo.spotEl)) demo.ro.observe(demo.spotEl);
    demo.roTarget = demo.spotEl;
  }
  if (!demo.ro2) demo.ro2 = new ResizeObserver(() => demoQueueReplace());
  if (demo.ro2Target !== demo.spotEl2) {
    if (demo.ro2Target) demo.ro2.unobserve(demo.ro2Target);
    if (!skip(demo.spotEl2)) demo.ro2.observe(demo.spotEl2);
    demo.ro2Target = demo.spotEl2;
  }
}

/** 记下目标当前的位置尺寸，返回一个可比较的键。 */
function demoRectKey(el) {
  if (!el) return '';
  const r = el.getBoundingClientRect();
  return [r.left, r.top, r.width, r.height].map((v) => Math.round(v)).join(',');
}

/** 目标矩形与可视区求交，并做三件事：判退化、给长卡片封顶、取整到整数像素。 */
function demoBoxFor(el, pad, W, H) {
  const r = el.getBoundingClientRect();
  if (!r.width && !r.height) return null;
  const it = {
    left: Math.max(0, r.left - pad), right: Math.min(W, r.right + pad),
    top: Math.max(demoBarH(), r.top - pad), bottom: Math.min(H, r.bottom + pad),
  };
  if (it.right - it.left < 12 || it.bottom - it.top < 12) return null;   // 已滚出可视区
  const maxH = Math.max(140, H - demoBarH() - 150);
  let top = it.top, h = it.bottom - it.top;
  if (h > maxH) { top = it.top + (h - maxH) / 2; h = maxH; }
  const box = {
    left: Math.round(it.left), right: Math.round(it.right),
    top: Math.round(top), bottom: Math.round(top + h),
  };
  box.w = box.right - box.left; box.h = box.bottom - box.top;
  return box;
}

/**
 * 重新量目标位置并摆放蒙版 / 框 / 指向线 / 卡片（scroll、resize、内容变化时调用）。
 *
 * 两个必须处理的边界情况（都会实打实把选框甩到屏幕外）：
 * 1. **目标被滚出可视区**：求交后 bottom 会小于 top，得到负高度。CSS 里 `height:-12px`
 *    是非法声明、会被整条丢弃 —— 高亮框于是保留上一帧的尺寸，停在一个错误的 top 上，
 *    看起来就是"选框跑到外面去了"。所以必须先判退化，退化时不画框与箭头。
 * 2. **目标比屏幕还高**（长卡片）：与视口求交后框会被夹成整屏高的一条，等于没框。
 *    这里把框高封顶，取可见部分居中，读起来才像"聚光"而不是"屏幕边框"。
 */
function demoPlaceSpot(el, opts = {}) {
  if (!el || !demo.on) return null;
  const pad = opts.pad === undefined ? 8 : opts.pad;
  const W = Math.max(window.innerWidth, document.documentElement.clientWidth);
  const H = Math.max(window.innerHeight, document.documentElement.clientHeight);
  demoShow($('demoSpot'), true);
  const box = demoBoxFor(el, pad, W, H);
  const box2 = demo.spotEl2 ? demoBoxFor(demo.spotEl2, opts.pad2 === undefined ? 6 : opts.pad2, W, H) : null;
  demo.spotBox = box;
  demo.spotBox2 = box2;
  if (!box) {
    // 目标已经滚出（或完全被顶部条挡住）：不画框与箭头，只留一层整幅蒙版
    demoDrawShade([], W, H);
    demoShow($('demoRing'), false);
    demoShow($('demoRing2'), false);
    demoShow($('demoTag'), false);
    demoShow($('demoArrow'), false);
    demoDebugLine({ mode: '已滚出可视区/退化', W, H });
    return null;
  }
  demoDrawShade(box2 ? [box, box2] : [box], W, H);
  demoDrawRing($('demoRing'), box, !!opts.pulse);
  const card = demoPlaceCard(box, true);
  if (box2) {
    demoDrawRing($('demoRing2'), box2, false);
    demoDrawTag(box2, demo.spotTag, card);
  } else {
    demoShow($('demoRing2'), false);
    demoShow($('demoTag'), false);
  }
  demoSyncObservers();
  return box;
}
/**
 * 蒙版 = 一层铺满的 SVG mask，把每个高亮框挖成洞。
 * 用 mask 而不是四块矩形拼接：拼接方案的相邻边靠小数坐标对齐，浏览器各自取整会漏出
 * 1 px 亮线（用户实拍报过"蒙版有裂缝"）；而且矩形只能加覆盖、不能减洞，
 * "同时高亮卡片与地图"这种两个洞的需求做不了。
 */
function demoDrawShade(boxes, W, H) {
  const holes = [$('dh1'), $('dh2')];
  boxes.slice(0, holes.length).forEach((b, i) => {
    const r = holes[i];
    r.setAttribute('x', b.left); r.setAttribute('y', b.top);
    r.setAttribute('width', Math.max(0, b.w)); r.setAttribute('height', Math.max(0, b.h));
  });
  for (let i = boxes.length; i < holes.length; i++) {   // 多余的洞缩成 0
    holes[i].setAttribute('x', 0); holes[i].setAttribute('y', 0);
    holes[i].setAttribute('width', 0); holes[i].setAttribute('height', 0);
  }
  const veil = dEl('.demo-veil');                        // mask 区域要跟着窗口走
  if (veil) { veil.setAttribute('width', W); veil.setAttribute('height', H); }
}

function demoDrawRing(e, b, pulse) {
  e.style.left = b.left + 'px'; e.style.top = b.top + 'px';
  e.style.width = b.w + 'px'; e.style.height = b.h + 'px';
  e.classList.toggle('pulse', !!pulse);
  demoShow(e, true);
}

/** 第二处高亮的角标：挑一个不压住指向卡的角贴上，说清"这里也在发生事情"。 */
function demoDrawTag(b, text, cardRect) {
  const e = $('demoTag');
  if (!text || !b) { demoShow(e, false); return; }
  e.textContent = text;
  demoShow(e, true);
  const w = e.offsetWidth || 140, h = e.offsetHeight || 22;
  const hit = (x, y) => cardRect && !(x + w < cardRect.left - 6 || x > cardRect.right + 6
    || y + h < cardRect.top - 6 || y > cardRect.bottom + 6);
  const spots = [
    [b.left + 6, b.top + 6],                 // 左上（框内）
    [b.left + 6, b.bottom - h - 6],          // 左下
    [b.right - w - 6, b.top + 6],            // 右上
    [b.right - w - 6, b.bottom - h - 6],     // 右下
  ].map(([x, y]) => [Math.round(Math.max(4, Math.min(x, window.innerWidth - w - 4))),
                     Math.round(Math.max(demoBarH() + 2, Math.min(y, window.innerHeight - h - 4)))]);
  const pick = spots.find(([x, y]) => !hit(x, y)) || spots[0];
  e.style.left = pick[0] + 'px';
  e.style.top = pick[1] + 'px';
}

/** 选边：取四条边外侧空间最大的一侧；若摆下会压住高亮框，则退到次优侧。
    返回摆放后的矩形，供第二处角标避开它。 */
function demoPlaceCard(b, retRect) {
  const c = $('demoCard');
  if (c.hidden) return null;
  const W = window.innerWidth, H = window.innerHeight, top0 = demoBarH() + 6;
  const cw = Math.min(DEMO_CARD_W, W - 2 * DEMO_EDGE);
  const ch = c.offsetHeight || 150;
  if (!b) {                                    // 无目标：居中偏下，不画指向线
    c.style.left = Math.round((W - cw) / 2) + 'px';
    c.style.top = Math.round(Math.max(top0, (H - ch) / 2)) + 'px';
    demoShow($('demoArrow'), false);
    demoDebugLine({ mode: '无目标', cw, ch, W, H });
    return;
  }
  const cand = {
    right: { space: W - b.right - DEMO_GAP - DEMO_EDGE, ok: W - b.right > cw + DEMO_GAP },
    left: { space: b.left - DEMO_GAP - DEMO_EDGE, ok: b.left > cw + DEMO_GAP },
    bottom: { space: H - b.bottom - DEMO_GAP - DEMO_EDGE, ok: H - b.bottom > ch + DEMO_GAP },
    top: { space: b.top - top0 - DEMO_GAP, ok: b.top - top0 > ch + DEMO_GAP },
  };
  const order = Object.keys(cand).sort((a, z) => cand[z].space - cand[a].space);
  // 侧别要稳定：滚动重算时如果换边，卡片会在屏幕上跳来跳去。
  // 上一轮选的那一侧只要还放得下就继续用。
  const side = (demo.cardSide && cand[demo.cardSide] && cand[demo.cardSide].ok)
    ? demo.cardSide : (order.find((s) => cand[s].ok) || order[0]);
  demo.cardSide = side;

  const clampX = (x) => Math.max(DEMO_EDGE, Math.min(x, W - cw - DEMO_EDGE));
  const clampY = (y) => Math.max(top0, Math.min(y, H - ch - DEMO_EDGE));
  const cx = b.left + b.w / 2, cy = b.top + b.h / 2;
  let left, top;
  if (side === 'right' || side === 'left') {
    left = side === 'right' ? b.right + DEMO_GAP : b.left - DEMO_GAP - cw;
    top = clampY(cy - ch / 2);
  } else {
    top = side === 'bottom' ? b.bottom + DEMO_GAP : b.top - DEMO_GAP - ch;
    left = clampX(cx - cw / 2);
  }
  const rect = { left: clampX(left), top: clampY(top), w: cw, h: ch };
  c.style.left = Math.round(rect.left) + 'px';
  c.style.top = Math.round(rect.top) + 'px';
  rect.right = rect.left + cw; rect.bottom = rect.top + ch;
  demoDrawArrow(b, rect, side);
  demoDebugLine({ mode: demo.spotEl ? (demo.spotEl.id || demo.spotEl.className) : '?',
                  side, box: b, card: { l: Math.round(rect.left), t: Math.round(rect.top), w: cw, h: ch },
                  cand, W, H });
  return rect;
}

/** ?demodebug=1 时把几何数字写进卡片与视口底部条 —— 无头截图里也能读到精确坐标，不用靠肉眼猜。 */
function demoDebugLine(info) {
  if (!demo.debug) return;
  const b = info.box;
  const text = `[dbg] target=${info.mode} side=${info.side || '-'} `
    + (b ? `box=${Math.round(b.left)},${Math.round(b.top)} ${Math.round(b.w)}×${Math.round(b.h)} ` : '')
    + `card=${info.card ? `${info.card.l},${info.card.t} ${info.card.w}×${info.card.h}` : '-'} `
    + `vp=${info.W}×${info.H} `
    + (info.cand ? 'space=' + Object.entries(info.cand)
      .map(([k, v]) => `${k}:${Math.round(v.space)}${v.ok ? '' : '(紧)'}`).join(' ') : '');
  const el = $('dcDbg');
  if (el) el.textContent = text;
  let bar = $('demoDbgBar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'demoDbgBar';
    bar.style.cssText = 'position:fixed;left:0;bottom:0;z-index:99;background:#fde68a;color:#111;'
      + 'font:12px/1.5 Consolas,monospace;padding:3px 8px;max-width:100vw;white-space:pre-wrap';
    document.body.appendChild(bar);
  }
  bar.textContent = text;
}

/** 折线 + 三角箭头：从卡片靠目标的那条边的中点，先走一条 L 形到目标框边缘。
    注意方向：side='right' 表示**卡片在高亮框右侧**，所以箭头要落在框的右边缘上。 */
function demoDrawArrow(b, card, side) {
  let ax, ay, tx, ty;                 // A = 卡片锚点，T = 目标框上的落点
  if (side === 'right' || side === 'left') {
    ax = side === 'right' ? card.left : card.left + card.w;
    ay = card.top + card.h / 2;
    tx = side === 'right' ? b.right + 2 : b.left - 2;
    ty = Math.max(b.top + 10, Math.min(ay, b.bottom - 10));
  } else {
    ax = card.left + card.w / 2;
    ay = side === 'bottom' ? card.top : card.top + card.h;
    ty = side === 'bottom' ? b.bottom + 2 : b.top - 2;
    tx = Math.max(b.left + 10, Math.min(ax, b.right - 10));
  }
  // L 形：横向侧先横后竖，纵向侧先竖后横
  const ex = (side === 'right' || side === 'left') ? tx : ax;
  const ey = (side === 'right' || side === 'left') ? ay : ty;
  $('daLine').setAttribute('points', `${ax},${ay} ${ex},${ey} ${tx},${ty}`);
  // 箭头方向 = 折线末段方向；末段退化（卡片中心正对目标框中心时 E 与 T 重合，
  // 这是最常见的一档）就退回用 A→T 的方向 —— 否则三角形三个点重合，箭头整个消失。
  let dx = tx - ex, dy = ty - ey;
  if (Math.hypot(dx, dy) < 1) { dx = tx - ax; dy = ty - ay; }
  const len = Math.hypot(dx, dy) || 1;
  dx /= len; dy /= len;
  const L = 10, Wd = 5.5;
  const px = -dy, py = dx;
  $('daHead').setAttribute('points',
    `${tx},${ty} ${tx - dx * L + px * Wd},${ty - dy * L + py * Wd} `
    + `${tx - dx * L - px * Wd},${ty - dy * L - py * Wd}`);
  demoShow($('demoArrow'), true);
}

/** scroll / resize 时重算位置：合并到一帧，避免抖动。 */
function demoQueueReplace() {
  if (!demo.on || (!demo.spotEl && !demo.spotEl2) || demo.raf) return;
  // 同样用定时器而不是 rAF：位置重算不需要帧级精度，但绝不能因为 rAF 停摆就永远不执行
  demo.raf = setTimeout(() => {
    demo.raf = 0;
    if (!demo.on || !demo.spotEl) return;
    // 位置尺寸都没变就不重摆。少了这道判断，"ResizeObserver 触发 -> 重摆 -> 又触发"
    // 会变成无限帧循环，把 rAF 占满 -> 演示里所有 await demoWait(...) 永远不返回
    // （第 8/9 步就是这样卡死的，而且页面看着还活着、只是不动）。
    const key = demoRectKey(demo.spotEl) + '|' + demoRectKey(demo.spotEl2);
    if (key === demo.lastKey) return;
    demo.lastKey = key;
    demoPlaceSpot(demo.spotEl);
  });
}

/** 当前可见的那块地图（canvas 与百度 GL 二选一）—— 动线/覆盖图层的变化都发生在它上面。 */
function demoMapSurface() {
  return state.mode === 'bmap' ? $('bmapWrap') : $('canvasWrap');
}

/** 停留时长：按正文长度 + 高亮框里的内容量算，录制时不用逐条手调。
    为什么要把"框有多高"算进去：第 ⑦ 步的高亮框是一整张处方卡（上千像素高），
    只按正文字数给 8 秒根本来不及看；反过来只有一句话的小按钮也不用停太久。 */
function demoHoldFor(body, step, box) {
  const scale = demo.mode === 'full' ? 1.45 : 1;
  if (step.hold) return Math.round(step.hold * scale);   // 显式指定优先（如第 ⑦ 步）
  const chars = String(body).replace(/<[^>]+>/g, '').length;
  let ms = chars * 150 + 1600;                          // 读正文
  if (box) ms += Math.min(3200, Math.max(0, box.h - 120) * 3.2);   // 框里的内容也要看
  if (step.hint) ms += 900;                             // 有交互提示要多停一下
  return Math.round(Math.max(3000, Math.min(ms, 18000)) * scale);
}

/* ---- 演示脚本 ----
   每步四个字段各司其职：
     target  要高亮的元素（定位高亮框用；scroll / resize 时重算）→ 也是自检的依据
     hint    需要观众动手时的一句交互提示（会带 👉 显示在卡片里，并给高亮框加脉冲）
     run()   只放动作
     caption() 只放话术（数字实时取；停留时长按它的字数算）
   skipNote 用于"这一步在本机做不了"时如实说明，而不是卡住或指向空气。 */
const DEMO_STEPS = [
  {
    key: 'intro', title: '开场', group: '导入', target: null,
    caption: () => '15 分钟生活圈<b>智能体检与规划助手</b>：从<b>诊断</b>走到<b>处方</b>。'
      + '演示数据为曹杨新村街道真实数据（百度 POI + OpenStreetMap 路网）。',
    run: async () => {
      state.panelMode = 'checkup'; applyPanelMode();
      clearRoute(); clearIso();
      state.coverMode = 'before'; updateCoverSwitch();
      setRaster('cover');
      state.view = { k: 1, tx: 0, ty: 0 };
      renderCanvas(); renderLegend();
      dEl('.panel').scrollTop = 0;
    },
  },
  {
    key: 'score', title: '① 智能体检 · 总分', group: '智能体检 1/4', target: '.score-card',
    caption: () => {
      const s = dsum();
      return `综合体检得分 <b>${s.composite_score}</b>（${s.grade}）· ${s.residential_points} 个居住组团 / `
        + `${s.households.toLocaleString()} 户 / ${s.population.toLocaleString()} 人<br>`
        + `总分不反映结构性短板，详见问题清单。`;
    },
    run: async () => {
      state.panelMode = 'checkup'; applyPanelMode();
      await demoSpotlight('.score-card');
    },
  },
  {
    key: 'problem', title: '② 最短板在哪', group: '智能体检 2/4', target: '#problems',
    caption: () => {
      const p = ((dg() || {}).problems || [])[0];
      if (!p) return '未发现必需要素缺口';
      return `<b>${esc(p.title)}</b><br>影响 ${p.affected_households.toLocaleString()} 户 / `
        + `${p.affected_population.toLocaleString()} 人，构成 15 分钟生活圈的主要缺口。`;
    },
    hint: '点问题卡片可展开影响范围与处置建议',
    run: async () => {
      state.panelMode = 'checkup'; applyPanelMode();
      await demoSpotlight('#problems', { pulse: true });
    },
  },
  {
    key: 'isochrone', title: '③ 生活圈不是一个圆', group: '智能体检 3/4', target: '#rsIso',
    caption: () => '底图切换为<b>步行等时圈</b>：5 / 10 / 15 分钟的真实路网可达范围（全社区并集），'
      + '未着色区域在生活圈之外。可达性由本地路网自行计算，不占用地图厂商算路配额。',
    hint: '点「步行等时圈」按钮可自己切换',
    run: async () => {
      state.panelMode = 'checkup'; applyPanelMode();
      setRaster('isochrone');
      renderCanvas(); renderLegend();
      await demoSpotlight('#rsIso', { pulse: true });
    },
  },
  {
    key: 'focus', title: '④ 换成以"他家"为圆心', group: '智能体检 4/4', target: '.map-card',
    caption: () => {
      const iso = state.iso && state.iso.data;
      if (!iso) return '同心等时圈';
      const cp = iso.compactness;
      const b = (iso.blind_directions || [])[0];
      return `以「<b>${esc(iso.point.name)}</b>」（${iso.point.households} 户）为圆心，步速 `
        + `${iso.walk_speed_mps} m/s：15 分钟实际可达 <b>${cp.actual_km2} km²</b>，`
        + `只有同半径理想圆的 <b>${(cp.ratio * 100).toFixed(0)}%</b>`
        + (b ? `；往${b.label}走 <b>${b.reach_m} m</b> 就被切断` : '');
    },
    hint: '点地图上任一居住组团，即可实时生成它自己的同心等时圈',
    run: async () => {
      state.panelMode = 'checkup'; applyPanelMode();
      await loadIsochrone(demoPoint());
      setRaster('isochrone');
      renderCanvas(); renderLegend();
      await demoSpotlight('.map-card');
    },
  },
  {
    key: 'plan', title: '⑤ 从体检切到规划', group: '规划助手 1/5', target: '.panel-modes',
    caption: () => '体检定位问题，规划给出方案：<b>在哪补、补什么、可覆盖多少人口</b>。'
      + '两个模式共用同一份数据，随时来回切。',
    hint: '点「② 规划助手」可在两种模式间来回切换',
    run: async () => {
      state.panelMode = 'plan'; applyPanelMode();
      dEl('.panel').scrollTop = 0;
      await demoSpotlight('.panel-modes', { pulse: true });
    },
  },
  {
    key: 'optimize', title: '⑥ 处方：选址优化', group: '规划助手 2/5', target: '#runOpt',
    // 这一步是全场的高潮，也是唯一一步 run() 里含 3–6 s 重计算的：不留足时间，
    // 算完只剩两三秒，处方卡根本看不完（用户实拍反馈）。
    hold: 20000,
    caption: () => {
      const o = state.opt;
      if (!o || !o.sites) return '选址优化';
      return `处方：补 <b>${o.sites.length}</b> 处<b>${esc(o.element_name)}</b> —— 覆盖率 `
        + `<b>${dPct(o.before.coverage_rate)} → ${dPct(o.after.coverage_rate)}</b>，`
        + `不可达人口 ${o.before.uncovered_population.toLocaleString()} → `
        + `${o.after.uncovered_population.toLocaleString()}`;
    },
    hint: '改「新增设施」处数后点此按钮，可自己重算一遍（本地计算，约 3–6 秒）',
    run: async () => {
      // 重放/预览时已经算过就直接复用：这一步要 3–6 秒，逐步自检连跑 12 次会白等好几分钟
      const cached = demo.replaying && state.opt && state.opt.after_diag;
      // 先指着按钮算，算完把高亮框移到结果卡片上 —— "先点这里 → 结果出现在这里"
      demo.spotEl = dEl('#runOpt');
      demoPlaceSpot(dEl('#runOpt'), { pulse: true });
      if (!cached) {
        demoPending('正在计算选址方案（本地贪心最大覆盖 + 1-opt 局部搜索，约 3–6 秒）…');
        await runOptimize();
      }
      state.panelMode = 'plan'; applyPanelMode();
      state.coverMode = 'before'; updateCoverSwitch();   // 下一步再切"优化后"，留出对比
      renderCanvas(); renderLegend();
      demo.spotEl = null;
      await demoSpotlight('#opt');
    },
  },
  {
    key: 'after', title: '⑦ 优化后的覆盖范围', group: '规划助手 3/5', target: '#coverAfter',
    caption: () => {
      const o = state.opt;
      const a = o && o.after_diag && o.after_diag.summary;
      const s = dsum();
      return a ? `覆盖图层切到<b>优化后</b>：综合得分 ${s.composite_score} → `
        + `<b>${a.composite_score}</b>，此前不可达的居住组团已纳入覆盖范围`
        : '优化后的覆盖范围';
    },
    hint: '「现状 / 优化后」可来回对比同一张底图',
    run: async () => {
      if (!(state.opt && state.opt.after_diag)) throw new Error('没有优化后体检结果');
      state.coverMode = 'after'; updateCoverSwitch();
      renderCanvas(); renderLegend();
      if (state.mode === 'bmap') renderBMapOverlays();
      // 变化发生在图上，所以两个洞：按钮 + 地图
      await demoSpotlight('#coverAfter', { pulse: true, also: demoMapSurface,
                                           tag: '覆盖图层已切到「优化后」' });
    },
  },
  {
    key: 'elder', title: '⑧ 一位老人的一天', group: '规划助手 4/5', target: '#routeBody',
    caption: () => {
      const d = state.route && state.route.data;
      if (!d || !d.persona) return '典型住户的一天';
      const t = d.totals;
      return `${d.persona.emoji || ''}<b>${esc(d.persona.name)}</b>（步速 `
        + `${d.assumptions.walk_speed_mps} m/s）在这个组团的一天：<b>${t.stops}</b> 处停靠、`
        + `步行 ${t.walk_min} 分钟，${t.start_clock}–${t.end_clock}`
        + (t.missing_count ? `；<b>${t.missing_count}</b> 项在 15 分钟阈值内不可达`
                           : '；出行链要素全部可达')
        + (t.over_leg_count ? `；另有 <b>${t.over_leg_count}</b> 段超过该画像可接受的单段步行距离` : '');
    },
    hint: '点「▶ 播放」可沿路网重放这一天；点地图上的组团可换成别家',
    run: async () => {
      state.panelMode = 'plan'; applyPanelMode();
      state.persona = 'elder_active'; renderPersonaBar();
      // 重放/预览时同样的动线不必重算（本地算路，但每次 1–2 秒 × 十余次也很可观）
      if (!(demo.replaying && state.route && state.route.data
            && (state.route.data.persona || {}).key === 'elder_active'
            && state.route.pointId === demoPoint())) {
        await loadRoute(demoPoint(), { persona: 'elder_active', start: null, speed: null });
      }
      // 重放时不放动画：playRoute 是 9 秒的 rAF 循环，每帧整张画布重绘。
      // 快速重放里连开几次这种循环会互相叠加，把帧预算吃光 —— 逐步自检跑到后面
      // 就会出现"页面还活着、但演示里的等待再也等不到"（踩过）。
      if (!demo.replaying && state.route && state.route.data && state.route.data.stops.length
          && !state.route.playing) playRoute();
      // 动线是**在地图上**播放的：只高亮右侧卡片的话，蒙版正好把要看的东西压暗了。
      // 所以这一步挖两个洞 —— 卡片 + 地图。
      await demoSpotlight('#routeBody', { pulse: true, also: demoMapSurface,
                                         tag: '动线正在地图上播放' });
    },
  },
  {
    key: 'commuter', title: '⑨ 同一栋楼，换个人', group: '规划助手 5/5', target: '#personaBar',
    caption: () => {
      const d = state.route && state.route.data;
      if (!d || !d.persona) return '换一位住户';
      const t = d.totals;
      const chain = d.stops.map((s) => s.element_name).join(' → ') || '无可达要素';
      return `换成<b>${esc(d.persona.name)}</b>：只有 <b>${t.stops}</b> 站（${esc(chain)}），`
        + `${t.start_clock}–${t.end_clock}；幼儿园 8:30 截止入园，构成刚性时间约束。`;
    },
    hint: '切换住户类别，同一栋楼的出行链立刻重算',
    run: async () => {
      if (state.route && state.route.playing) playRoute();      // 停掉上一段播放
      state.persona = 'commuter'; renderPersonaBar();
      if (!(demo.replaying && state.route && state.route.data
            && (state.route.data.persona || {}).key === 'commuter'
            && state.route.pointId === demoPoint())) {
        await loadRoute(demoPoint(), { persona: 'commuter', start: null, speed: null });
      }
      await demoSpotlight('#personaBar', { pulse: true, also: demoMapSurface,
                                           tag: '地图上的动线已换成这位住户' });
    },
  },
  {
    key: 'gl', title: '⑩ 贴到百度地图上', group: '底图能力', target: '.tabs',
    skipNote: '本机没配 AK 或当前没有网络 —— 已留在离线静态底图上，结论完全一样',
    caption: () => '同一份结论也能贴在<b>百度地图 JSAPI</b> 上：风圈、一天动线、序号停靠点都在，'
      + '坐标系按 WGS-84 → BD-09 换算对齐',
    hint: '「离线底图 / 百度地图」两种底图随时切换，叠加层完全一致',
    run: async () => {
      const ak = state.ak || localStorage.getItem('dsh_bmap_ak') || '';
      if (!ak) throw new Error('no ak');
      // 断网时百度脚本/瓦片都取不到，切过去只会得到一张白地图 —— 直接跳过，留在离线底图
      if (navigator.onLine === false) throw new Error('offline');
      await demoSpotlight('.tabs', { pulse: true });
      if (state.mode !== 'bmap' || !glState.map) {
        document.querySelector('.tabs button[data-mode="bmap"]').click();
        const ready = await demoWaitFor(() => state.mode === 'bmap' && !!glState.map, 9000);
        if (!ready) {
          // initBMap 失败时自己会退回 canvas；这里再兜一次，保证不留白屏
          document.querySelector('.tabs button[data-mode="canvas"]').click();
          throw new Error('GL not ready');
        }
        await new Promise((r) => setTimeout(r, 2600));    // 等瓦片与叠加层铺开
      }
    },
  },
  {
    key: 'outro', title: '⑪ 收尾', group: '可复现', target: '#addCommunity',
    caption: () => '更换任意街道均可复现（「＋ 自选社区」）。全部指标可追溯至<b>完整报告</b>'
      + '与结构化 JSON。演示结束。',
    hint: '「＋ 自选社区」输入任意街道地址，20–60 秒出同样的体检报告',
    run: async () => {
      demoMark('#reportLink', 'demo-point');
      if (demo.notes.length) {
        console.info('[demo] 跳过的步骤：', demo.notes);
      }
      await demoSpotlight('#addCommunity', { pulse: true });
    },
  },
];

async function demoLoop(startAt) {
  const tok = ++demo.token;
  for (let i = startAt; i < DEMO_STEPS.length; i++) {
    if (tok !== demo.token) return;
    demo.i = i;
    const st = DEMO_STEPS[i];
    demoBar(i, st);
    demoClearMarks();
    demoHideSpot();
    demoSetCard(st, i, `<span class="muted">⏳ ${esc(st.title)}…</span>`, { place: false });
    const t0 = performance.now();
    let skipped = null;
    try {
      await st.run();
    } catch (err) {
      skipped = st.skipNote || `本步失败（${err.message}），已跳过`;
      demo.notes.push(`${st.key}: ${err.message}`);
      console.warn('[demo] 步骤跳过', st.key, err);
      if (!demo.spotBox) demoPlaceCard(null);          // 目标没亮起来时卡片居中说明
    }
    if (tok !== demo.token) return;
    const body = st.caption() + (skipped ? ` <span class="skip">（${esc(skipped)}）</span>` : '');
    demo.curBody = body;
    // 卡片正文要先把长度算出来（停留时长由字数与框高决定），所以先写正文再摆放
    demoSetCard(st, i, body, { place: false });
    demoPlaceCard(demo.spotBox);
    const want = demoHoldFor(body, st, demo.spotBox);
    const spent = performance.now() - t0;
    // 下限 4 s：run() 里含重计算（如选址优化 3–6 s）时不能把阅读时间一起吃掉
    const res = await demoWait(Math.max(4000, want - spent), t0, want);
    if (res === 'abort') return;
  }
  await demoFinish();
}

/** 回退/跳转 = 从第 0 步快速重放（不等待），再从目标步正常播 —— 比逐步撤销可靠。 */
async function demoGoto(target) {
  const t = Math.max(0, Math.min(DEMO_STEPS.length - 1, target));
  const tok = ++demo.token;
  demo.paused = false;
  demoBar(0, DEMO_STEPS[0]);
  demoPending(`返回第 ${t + 1} 个环节…`);
  demo.replaying = true;
  try {
    for (let i = 0; i < t; i++) {
      if (tok !== demo.token) return;
      try { await DEMO_STEPS[i].run(); } catch (err) { /* 重放失败不影响后续 */ }
    }
  } finally {
    demo.replaying = false;
  }
  if (tok !== demo.token) return;
  demoLoop(t);
}

/**
 * 只把某一步"摆好"就停下（不进入自动播放）—— 给自动化截图 / 录屏取景 / 逐步验收用。
 * 与 demoGoto 的区别：不调用任何长等待，因此页面很快进入静止状态，无头浏览器也能截到。
 *
 * 前置步骤走"重放"模式：跳过 320 ms 的滚动落定等待，并跳过已经算过的重活
 * （选址优化、动线）—— 逐步自检要连跑 12 次，每次都重算一遍会白等好几分钟。
 */
async function demoPreviewStep(n) {
  if (!state.bundle) return;
  const idx = Math.max(0, Math.min(DEMO_STEPS.length - 1, (n || 1) - 1));
  demo.on = true; demo.paused = false; demo.mode = 'quick';
  $('demoMenu').hidden = true;
  $('demoBar').hidden = false;
  document.body.classList.add('demo-on');
  renderCanvas();
  for (let i = 0; i <= idx; i++) {
    const st = DEMO_STEPS[i];
    if (i < idx) {
      demo.replaying = true;
      try { await st.run(); } catch (err) { /* 预览时忽略前置步骤失败 */ }
      demo.replaying = false;
      continue;
    }
    demo.i = i;
    demoBar(i, st);
    demoClearMarks();
    demoHideSpot();
    demoSetCard(st, i, `<span class="muted">⏳ ${esc(st.title)}…</span>`, { place: false });
    try {
      await st.run();
    } catch (err) {
      demo.notes.push(`${st.key}: ${err.message}`);
      demoPlaceCard(null);
    }
    demo.curBody = st.caption();
    demoSetCard(st, i, demo.curBody, { place: false });
    demoPlaceCard(demo.spotBox);
  }
}

/** 重播本步：录制时某一步没录好，只重录这一段，不用整条重来。 */
function demoReplayStep() {
  if (!demo.on) return;
  demoGoto(demo.i);
}

async function demoFinish() {
  demoBar(DEMO_STEPS.length - 1, DEMO_STEPS[DEMO_STEPS.length - 1]);
  $('demoStep').textContent = `演示结束 · 共 ${DEMO_STEPS.length} 步`;
  $('demoProg').style.width = '100%';
  demo.spotEl = null;
  demo.spotEl2 = null;
  demo.spotBox = null;
  demo.spotBox2 = null;
  demoShow($('demoSpot'), true);
  demoShow($('demoRing'), false);
  demoShow($('demoRing2'), false);
  demoShow($('demoTag'), false);
  demoShow($('demoArrow'), false);
  const c = $('demoCard');
  c.innerHTML = '<div class="dc-head"><span class="dc-step">演示结束</span>'
    + '<span class="dc-title">可复现</span></div>'
    + '<div class="dc-body">感谢观看。所有指标均可追溯至完整报告与结构化 JSON；'
    + '更换任意街道地址即可复现同一套结论。</div>';
  c.hidden = false;
  demoPlaceCard(null);
  await demoWait(6000);
  demoStop();
}

function demoStop() {
  demo.token++;
  demo.on = false; demo.paused = false;
  $('demoBar').hidden = true;
  document.body.classList.remove('demo-on');
  document.body.style.paddingTop = '';
  demoHideSpot();
  demoClearMarks();
  renderCanvas();          // 视口尺寸变了，重画一次画布
}

async function demoStart(mode) {
  if (!state.bundle) return;
  demo.mode = mode === 'full' ? 'full' : 'quick';
  demo.on = true; demo.paused = false; demo.notes = [];
  $('demoMenu').hidden = true;
  $('demoBar').hidden = false;
  document.body.classList.add('demo-on');
  renderCanvas();          // 上边距变了，画布可用高度跟着变
  // 演示固定用曹杨新村街道真实数据（唯一一个有完整短板 + 处方 + 画像对比的社区）
  if ($('community').value !== 'caoyang-real'
      && [...$('community').options].some((o) => o.value === 'caoyang-real')) {
    $('community').value = 'caoyang-real';
    await loadBundle();
  }
  demoLoop(0);
}

function demoTogglePause() {
  demo.paused = !demo.paused;
  demoBar(demo.i, DEMO_STEPS[demo.i]);
  if (demo.paused) demoPending('已暂停。按空格键或点「▶ 继续」恢复播放；R 键重播本步。');
  else demoUpdateBody(demo.curBody || '');
}

/** 逐步自检：每步的 target 在页面里是否真的存在 —— 改界面后别等到录制当天才发现框不到。 */
function demoSelfcheck() {
  return DEMO_STEPS.map((st, i) => {
    const el = st.target ? dEl(st.target) : null;
    const r = el ? el.getBoundingClientRect() : null;
    return {
      i: i + 1, key: st.key, title: st.title, target: st.target || '(无，卡片居中)',
      found: !st.target || !!el,
      visible: !!(r && r.width > 0 && r.height > 0),
      hint: !!st.hint,
    };
  });
}

function bindDemo() {
  const placeMenu = () => {
    // 菜单用 fixed 定位并跟着按钮算位置：header 有 overflow（宽屏下可滚动），
    // 用 absolute 会被裁掉一截（实拍踩到：菜单下半截看不见）。
    const r = $('demoStart').getBoundingClientRect();
    const m = $('demoMenu');
    m.style.left = Math.max(8, Math.min(r.right - 272, window.innerWidth - 284)) + 'px';
    m.style.top = (r.bottom + 6) + 'px';
  };
  $('demoStart').addEventListener('click', (e) => {
    e.stopPropagation();
    $('demoMenu').hidden = !$('demoMenu').hidden;
    if (!$('demoMenu').hidden) placeMenu();
  });
  window.addEventListener('resize', () => {
    if (!$('demoMenu').hidden) placeMenu();
    demoQueueReplace();
  });
  window.addEventListener('scroll', () => {
    if (!$('demoMenu').hidden) placeMenu();
    demoQueueReplace();
  }, true);
  document.querySelectorAll('#demoMenu [data-demo]').forEach((b) => {
    b.addEventListener('click', () => demoStart(b.dataset.demo));
  });
  document.addEventListener('click', (e) => {
    if (!$('demoMenu').hidden && !e.target.closest('.demo-ctl')) $('demoMenu').hidden = true;
  });
  $('demoPause').addEventListener('click', demoTogglePause);
  $('demoNext').addEventListener('click', () => demoGoto(demo.i + 1));
  $('demoExit').addEventListener('click', demoStop);
  // 键盘：空格 暂停/继续、←/→ 上一步/下一步、R 重播本步、Esc 退出。
  // 录制时按键盘比点小按钮干净；R 只重录错的那一段。
  document.addEventListener('keydown', (e) => {
    if (!demo.on) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
    if (e.key === 'Escape') demoStop();
    else if (e.key === ' ') { e.preventDefault(); demoTogglePause(); }
    else if (e.key === 'ArrowRight') demoGoto(demo.i + 1);
    else if (e.key === 'ArrowLeft' && demo.i > 0) demoGoto(demo.i - 1);
    else if (e.key === 'r' || e.key === 'R') demoReplayStep();
  });
  // 演示脚本的自检入口：控制台里 __demo.selfcheck() 可逐步核对 target 是否存在，
  // 免得改一次界面结构，到录制当天才发现某一步框不到东西。
  window.__demo = {
    steps: () => DEMO_STEPS.map((s, i) => ({ i: i + 1, key: s.key, target: s.target })),
    selfcheck: demoSelfcheck,
    start: demoStart,
    stop: demoStop,
    goto: demoGoto,
    replay: demoReplayStep,
    preview: demoPreviewStep,
    current: () => demo.i + 1,
    side: () => demo.cardSide,
    stepEl: () => demo.spotEl,
    stepTarget: () => (DEMO_STEPS[demo.i] || {}).target || '(无)',
    spotBox: () => demo.spotBox,
    /** 当前这一步实际会停留多少秒（用真实正文与真实高亮框算，供自检与录屏对时） */
    currentHold: () => Math.round(
      demoHoldFor(demo.curBody || '', DEMO_STEPS[demo.i] || {}, demo.spotBox) / 1000),
    holds: () => DEMO_STEPS.map((s, i) => ({ i: i + 1, key: s.key,
                                             hold_s: Math.round(demoHoldFor(s.caption(), s) / 1000) })),
  };
  // 演示中碰地图 = 想自己看 → 自动暂停，不跟人抢操作
  ['canvasWrap', 'bmapWrap'].forEach((id) => {
    ['mousedown', 'wheel', 'touchstart'].forEach((ev) => {
      $(id).addEventListener(ev, () => {
        if (demo.on && !demo.paused) demoTogglePause();
      }, { passive: true });
    });
  });
}

function bindUI() {
  $('community').addEventListener('change', loadBundle);
  $('runOpt').addEventListener('click', runOptimize);
  $('k').addEventListener('input', () => ($('kval').textContent = $('k').value));
  // 体检 / 规划 双模式
  document.querySelectorAll('.panel-modes button').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.panelMode = btn.dataset.pmode;
      applyPanelMode();
    });
  });
  ['basemap', 'cover', 'roads', 'footway', 'poi', 'resid', 'sites'].forEach((key) => {
    $('ly_' + key).addEventListener('change', (e) => {
      state.layers[key] = e.target.checked;
      renderCanvas();
      if (state.mode === 'bmap') renderBMapOverlays();      // GL 模式也遵守图层开关
    });
  });
  // 底图口径切换：可达要素数量 / 步行等时圈
  $('rsCover').addEventListener('click', () => setRaster('cover'));
  $('rsIso').addEventListener('click', () => setRaster('isochrone'));
  document.querySelectorAll('.tabs button').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tabs button').forEach((b) => b.classList.remove('on'));
      btn.classList.add('on');
      state.mode = btn.dataset.mode;
      $('canvasWrap').hidden = state.mode !== 'canvas';
      $('bmapWrap').hidden = state.mode !== 'bmap';
      if (state.mode === 'bmap') {
        // GL 地图容器要有明确高度，且必须在 new BMapGL.Map() 之前定好，否则百度地图按 0 高渲染
        const headerH = (document.querySelector('header') || {}).offsetHeight || 170;
        $('bmapWrap').style.height =
          Math.max(340, Math.min(620, window.innerHeight - headerH - 200)) + 'px';
        const ak = state.ak || $('ak').value.trim() || localStorage.getItem('dsh_bmap_ak') || '';
        if (!ak) {
          $('mapStatus').textContent = '请在顶部粘贴百度地图 Web 端 AK（浏览器端类型，白名单含当前域名）';
        } else {
          state.ak = ak;
          // GL 底图自带大量地名标注，首次进入时默认关掉"设施"图层，避免糊成一片
          // （想看点设施：勾上「设施」并把地图放大到 16 级以上）
          if (!state.glTuned) {
            state.glTuned = true;
            if (state.layers.poi) {
              state.layers.poi = false;
              $('ly_poi').checked = false;
              renderCanvas();
            }
          }
          initBMap(ak);
        }
      }
    });
  });
  const useAk = () => {
    const ak = $('ak').value.trim();
    if (!ak) return;
    state.ak = ak;
    localStorage.setItem('dsh_bmap_ak', ak);
    document.querySelector('.tabs button[data-mode="bmap"]').click();
  };
  $('loadAk').addEventListener('click', useAk);
  $('ak').addEventListener('keydown', (e) => { if (e.key === 'Enter') useAk(); });
  const savedAk = localStorage.getItem('dsh_bmap_ak');
  if (savedAk) $('ak').value = savedAk;
  window.addEventListener('resize', () => state.mode === 'canvas' && renderCanvas());
}

/* 从 /api/config 取浏览器端 AK（若后端已配置 .env.local，则无需手工粘贴） */
async function loadWebAk() {
  try {
    const cfg = await fetch('/api/config').then((r) => r.json());
    const urlAk = new URLSearchParams(location.search).get('ak');
    const ak = urlAk || cfg.baidu_web_ak || localStorage.getItem('dsh_bmap_ak') || '';
    if (ak) {
      state.ak = ak;
      if (!urlAk && cfg.baidu_web_ak) {
        $('ak').value = cfg.baidu_web_ak;
        $('ak').placeholder = '已内置 AK（来自 .env.local）';
      }
    }
    state.personas = cfg.personas || [];
    state.personaNotes = cfg.persona_notes || {};
    const qp = new URLSearchParams(location.search).get('persona');
    if (qp !== null) {
      state.persona = state.personas.some((p) => p.key === qp) ? qp : '';
    }
    renderPersonaBar();
    return cfg;
  } catch (err) {
    return {};
  }
}

(async function boot() {
  bindUI();
  bindHover();
  bindClick();
  bindView();
  bindAddCommunity();
  bindCoverSwitch();
  bindDemo();
  applyPanelMode();
  try {
    await loadWebAk();
    await loadCommunities();
    // ?community=<id> 直接打开指定社区（分享视图 / 自动化截图用）
    const qs0 = new URLSearchParams(location.search);
    const wantCid = qs0.get('community');
    if (wantCid && [...$('community').options].some((o) => o.value === wantCid)) {
      $('community').value = wantCid;
    }
    await loadBundle();
    // ?zoom=2 可直接以指定缩放打开（便于分享视图与自动化截图）；必须在 loadBundle 之后，
    // 因为换社区会重置视图
    const z = parseFloat(new URLSearchParams(location.search).get('zoom') || '1');
    if (z && z !== 1) {
      state.view.k = Math.max(0.6, Math.min(10, z));
      const side = $('canvasWrap').clientWidth || 640;
      state.view.tx = (1 - state.view.k) * side / 2;
      state.view.ty = (1 - state.view.k) * side / 2;
      renderCanvas();
    }
    // ?mode=bmap 直接进百度底图；?new=1 展开"自选社区"；?cover=after 直接看优化后覆盖
    const qs = new URLSearchParams(location.search);
    if (qs.get('new') === '1') $('addCommunity').click();
    // ?route=CR050&rmode=after 直接生成"一天动线"（便于分享单户视图与自动化截图）
    const routePoint = qs.get('route');
    if (routePoint) {
      loadRoute(routePoint, { mode: qs.get('rmode') === 'after' ? 'after' : 'before',
                              order: qs.get('rorder') === 'nearest' ? 'nearest' : 'day',
                              start: qs.get('rstart') || null,
                              speed: qs.get('rspeed') || null });
    }
    // ?focus=CR050 直接给出该组团的风圈；?pmode=plan 直接进"规划助手"
    const focusPoint = qs.get('focus');
    if (focusPoint) loadIsochrone(focusPoint);
    if (qs.get('pmode') === 'plan') { state.panelMode = 'plan'; applyPanelMode(); }
    if (qs.get('cover') === 'after' && state.opt && state.opt.after_diag) {
      state.coverMode = 'after';
      updateCoverSwitch();
      renderCanvas();
      renderLegend();
    }
    // ?raster=iso 直接以"步行等时圈"底图打开（便于分享视图与自动化截图）
    if (qs.get('raster') === 'iso') {
      state.raster = 'isochrone';
      $('rsCover').classList.remove('on');
      $('rsIso').classList.add('on');
      renderCanvas();
      renderLegend();
    }
    if (new URLSearchParams(location.search).get('mode') === 'bmap') {
      document.querySelector('.tabs button[data-mode="bmap"]').click();
    }
    // ?demo=quick / ?demo=full 直接开播一键演示（录制、分享与自动截图都用它，省掉点菜单）
    const dm = qs.get('demo');
    if (dm) {
      $('demoMenu').hidden = true;
      demoStart(dm === 'full' ? 'full' : 'quick');
    }
    // ?demopreview=4 只摆好第 4 步就停（逐步验收 / 录屏取景 / 无头截图）
    const dpv = qs.get('demopreview');
    if (dpv) {
      demo.debug = qs.get('demodebug') === '1';
      await demoPreviewStep(parseInt(dpv, 10) || 1);
    }
    // ?gl=0 不自动加载 GL（默认已配置 AK 时会自动进 GL 模式，见 loadWebAk）
    if (qs.get('gl') === '0') state.noAutoGl = true;
  } catch (err) {
    $('mapHint').textContent = '加载失败：' + err.message;
  }
})();

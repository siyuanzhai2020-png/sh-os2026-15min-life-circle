# 数据模型与数据源切换

## 1. 唯一契约：`Dataset`

引擎（`graph.py` / `accessibility.py` / `metrics.py` / `optimize.py`）只认这一套结构，
因此**换数据源不需要改任何算法代码**。定义见 `lifecycle_metrics/schema.py`。

```
Dataset
├── community  : {id, name, district, center:{lon,lat}, extent_m:{half_width,half_height}, ...}
├── meta       : {source, seed?, river?[[lon,lat],...], bridges?, removed_crossings?, note}
├── nodes[]    : {id, lon, lat}
├── edges[]    : {u, v, length_m, kind, speed_mps, oneway}
│               kind ∈ footway | residential | service | primary | bridge | steps
├── residential[] : {id, name, lon, lat, households, built_year?, tags{}}
│               tags.across_river 用于"跨河片区"专项分析
└── facilities[]  : {id, name, element, lon, lat, capacity, source, tags{}}
                  element 必须能在 config/elements.json 里找到
```

数据集落盘为 `data/<community>.dataset.json`（已 gitignore，可随时重建）。

## 2. 三种 provider

| provider | 步行路网 | 设施 POI | 居住组团 | 需要 | 适用场景 |
| --- | --- | --- | --- | --- | --- |
| `synthetic`（默认） | 确定性生成（主干路+支路+内部步道，含一条只留 3 座桥的河道） | 手工布点（老城密集、跨河片区稀疏） | 构造户数 | 无 | 离线开发、回归测试、演示 |
| `osm` | Overpass 真实路网（footway/path/steps/各等级道路，按等级赋步行速度） | OSM 标签映射（覆盖弱） | 建筑轮廓 × 层数估算 | 外网 | 真实社区、无 AK |
| `baidu` | **复用 OSM 路网**（百度不提供步行路网下载） | 百度地点检索（覆盖强、含营业信息） | 住宅区 POI（户数缺省估算） | AK | 正式提交的真实数据 |

切换方式：改 `config/communities.json` 中该社区的 `provider` 字段，删除对应数据集缓存，重跑：

```bash
python tools/build_demo.py <community_id> --rebuild
```

## 3. 坐标系（硬性约定）

**数据集内部一律 WGS-84**。OSM 天然是 WGS-84；百度全系（POI / 底图 / 路径规划）是 BD-09，
上海地区两者相差约 **1.16 km**，混用会使 POI 与路网整体错位、可达性结论失效。

| 位置 | 处理方式 |
| --- | --- |
| `providers/baidu.py` 检索中心 | WGS-84 → BD-09 后再请求 |
| `providers/baidu.py` 返回的 POI | BD-09 → WGS-84 后入库（`tags.bd09` 保留原值可追溯） |
| `walking_route()`（标定） | 入参 WGS-84，内部转 BD-09 再请求 |
| `web/app.js`（JSAPI GL 叠加层） | 内置同一套换算函数，画点前 WGS-84 → BD-09 |
| 服务端静态底图 `/api/basemap.*` | 请求时中心转 BD-09；前端按 WGS-84 中心 + `mpp` 对齐 |

**分辨率也需要用百度自己的**：标准 Web Mercator 的 `156543.03·cos(lat)/2^zoom` 不适用于百度，
实测（JSAPI `getBounds`/`pointToPixel` 双口径，zoom 14–18）为 `262150·cos(lat)/2^zoom`，
差 1.675 倍。使用错误公式会使底图整体缩放：中心对齐、边缘偏差增大。实现见
`lifecycle_metrics/basemap.py::baidu_mpp`，标定工具 `tools/basemap_scale_check.py`，
核验图 `tools/align_check.py`。

**复核口径（不依赖浏览器）**：`python tools/basemap_align_probe.py [社区id]` 一次给出两个数——
① 用"两张中心相差 Δ 的静态图互相关"实测**静态图 API 自己的** mpp（唯一不依赖常数假设的
绝对口径，实测与 `262150` 差 0.6% 以内）；② 把 OSM 路网按标定 mpp 投影后与底图道路像素做
互相关，给出**残余平移**（实测 ≤ 2 px ≈ 7 m）。两项都在容差内才说明"底图可直接叠加数据集"。
路网或色块与底图不吻合时优先执行该脚本：比例尺偏差表现为边缘偏移增大，平移偏差表现为整幅均匀偏移。

换算实现见 `lifecycle_metrics/crs.py`，精度实测 < 10 m（回归测试 `TestCRS` 用真实地物点对钉住）。

## 4. 类别映射与多源融合（纯函数，可单测）

- OSM → 要素：`providers/osm.py::map_osm_tags`
  （`highway=bus_stop`→bus_stop；`amenity=marketplace`→market；`shop=greengrocer`→market；
  `railway=station`→metro；`leisure=park`→park；中学/大学不计入 primary_school）
- 百度 → 要素：`providers/baidu.py::map_baidu_tags`
  （关键词表 + 排除词表，顺序敏感：地铁站先于公交站；"华东师范大学"不会被算成小学）
- 百度字段取值：`providers/baidu.py::poi_fields` —— **`tag`/`type` 在 `detail_info` 内**，
  直接读顶层会得到空值（实测会导致公交站全部无法归类）。
- **分类闸门**：`kind_ok()`（metro/bus_stop/park 必须 tag/type 二次确认）+
  `is_auxiliary()`（停车场/充电站/出入口不作为独立设施）。仅按名称分类会把
  "XX酒店(…地铁站店)"算成地铁站、"申亚时代广场"算成公园，见 docs/metrics.md §4.2。
- **居住组团实名**：`name_clusters_from_poi()` 给只有"居住组团 N"的组团挂上 250 m 内最近
  百度「住宅区/小区」POI 的名字；存量数据集用 `tools/name_clusters.py` 就地改名
  （只改 name 与 tags，不动户数/路网/设施，指标不变）。
- 去重与合并：`merge_nearby` —— 同要素内按距离合并（地铁 250 m / 公交站 60 m / 公园 80 m /
  其它 35 m），地铁出入口先按名称正则剔除。
- **多源并集**：百度 POI 与 OSM POI 做并集（同要素 50 m 内或同名视为同一处），
  而非"百度为空才兜底"。原因：百度对健身点、社区活动室等小微设施覆盖不足，
  单一数据源会把"数据缺口"误读成"设施缺口"。`meta.poi_by_source` 记录两者各自的贡献量。

映射表是**唯一**需要随数据源维护的地方，测试覆盖在 `tests/test_engine.py::TestProviders`。

## 5. 校验要求（接入真实数据后必做）

1. **可达性对照**：抽样 30–50 个居住组团，用百度步行路径规划计算真实步行时间，
   与本引擎自算结果比对，输出中位偏差与 P90 偏差，写进报告"误差分析"章节。
   工具入口：`tools/check_baidu_ak.py`（先验证接口可用）。
2. **POI 完备性**：把百度 POI 与 OSM/实地清单对比，列出"百度有、OSM 无"的设施类型，
   说明不同数据源下覆盖率结论的敏感性。
3. **户数敏感性**：对居住组团户数做 ±30% 扰动，观察综合得分与选址建议是否稳定
   （结论稳健性优先于单点数值）。

## 6. 合规

- OSM：ODbL，需在报告与仓库注明来源；
- 百度地图开放平台：仅用于本赛题演示，原始响应只落本地 `data/cache/`（gitignore），不得转售或超范围囤积；
- 若使用街道/社区提供的实测人口数据：需确认可公开引用范围，仓库中只放聚合结果。

# tools/ —— 脚本索引

这里 31 个脚本分四类。**「校验工具」是结论的证据链**：报告里每个数字是怎么验出来的，
都能在这里找到对应脚本，所以它们留在仓库里而不是删掉。

统一约定：所有脚本都可以在仓库根目录直接跑（`python tools/xxx.py`），
不依赖网络时会走 `data/cache/` 里的缓存；需要外网的脚本在表里单独标注。

## 一、主链路（日常只用这几个）

| 脚本 | 作用 |
| --- | --- |
| `serve.py` | 起本地服务（FastAPI + 静态前端）。`--warm` 预热缓存，`--open` 就绪后自动开浏览器 |
| `build_demo.py` | 一键跑通全链路：装配数据 → 可达性 → 体检 → 选址优化 → 出报告 |
| `add_community.py` | 自选社区：按地址或经纬度新建一个分析社区（**需外网**） |
| `make_package.py` | 打交付包（ZIP，含离线数据集与底图缓存，收件人无需 AK） |
| `verify_package.py` | 把包解压到临时目录，用"陌生人环境"跑一遍 |
| `lint_style.py` | 代码规范自检（行长 / 编码 / 行尾 / BOM），零依赖 |
| `fix_ps1_bom.py` | 给 `tasks.ps1` 补回 UTF-8 BOM（编辑器常会吃掉它） |

## 二、自检与验收（进 `tasks.ps1 verify` 链）

| 脚本 | 作用 |
| --- | --- |
| `check_api.py` | 端到端 API 自检（48 项）：端点、口径、前端资源、演示 UI 结构 |
| `tour_shots.py` | 一键演示逐步自检 / 取景截图（CDP 驱动无头 Edge，实跑 12 步） |
| `robustness.py` | 稳健性检验：换权重 / 换阈值 / 换步速，看排名与短板是否稳定 |
| `marginal.py` | 边际效益曲线：第 k 处设施还能多覆盖多少户 |
| `scope_delta.py` | 分析范围口径核对：逐社区统计"框外居住组团"（`--rebuild` 出前后对照） |
| `stitch_network.py` | 断头路补连（就地重算存量数据集） |
| `fix_carriers.py` | 载体表归并（一座地铁站只保留一条）+ 刷新 `meta.carrier_meta` |
| `name_clusters.py` | 居住组团实名（用百度小区 POI，走缓存） |

## 三、出图与横向对比

| 脚本 | 作用 |
| --- | --- |
| `route_map.py` | 一天动线出图 + 单组团风圈图 + 住户画像对比（报告图 3/4 + 5.1/5.3） |
| `compare.py` | 跨社区横向对比 → `out/compare.html` |

## 四、一次性 / 诊断探针

这些是排查具体问题时写的，**保留是为了留下"当时怎么定位的"**。
多数需要外网或特定环境，平时不用跑。

| 脚本 | 当初用来查什么 |
| --- | --- |
| `ak_probe.py` | 带硬超时的连通性 + AK 分步探针，任一步失败都不阻塞 |
| `check_baidu_ak.py` | 校验 AK 是否可用：地点检索 / 地理编码 / 步行路径规划 |
| `crs_check.py` | 坐标系换算核验（BD-09 ↔ WGS-84，抽 8 组已知地物） |
| `calibrate_walk.py` | 自算路网 vs 百度步行路径规划抽样对照（30 组，得出步速标定） |
| `basemap_scale_check.py` | 百度静态底图真实分辨率标定（**权威口径**：262150·cos(lat)/2^zoom） |
| `basemap_align_probe.py` | 底图对齐核验（不依赖浏览器 JSAPI） |
| `basemap_diag.py` / `baidu_diag.py` | 浏览器端地图加载失败 / 百度检索返回异常时的分步诊断 |
| `align_check.py` | 把自建路网画到百度静态底图上，肉眼 + 指标双重确认不偏移 |
| `overpass_probe.py` / `probe_net.py` | Overpass 镜像可用性与网络出口探测 |
| `smoke.py` | 最小冒烟（引擎 ↔ 服务 ↔ 前端一条线） |
| `add_standard_refs.py` | 给要素清单补"标准依据"字段（幂等；规范条文仍需逐条核对原文） |
| `add_build_notes.py` | 给可落地要素补"建设形态 / 规模 / 审批"字段（幂等） |

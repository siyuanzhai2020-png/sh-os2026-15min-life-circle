# tools/ 脚本索引

本目录共 34 个 Python 脚本，按用途分为四组：主链路 7 个、自检与验收 11 个、
出图与横向对比 2 个、一次性 / 诊断探针 14 个。这些脚本保留在仓库中，用于复核报告中的数据来源。

统一约定：所有脚本均可在仓库根目录直接运行（`python tools/xxx.py`）；
不依赖网络的脚本读取 `data/cache/` 缓存，需要外网的脚本在表中单独标注。

## 1. 主链路

| 脚本 | 作用 |
| --- | --- |
| `serve.py` | 启动本地服务（FastAPI + 静态前端）。`--warm` 预热缓存，`--open` 就绪后自动打开浏览器 |
| `build_demo.py` | 全链路一键执行：装配数据 → 可达性 → 体检 → 选址优化 → 出报告 |
| `add_community.py` | 按地址或经纬度新建一个分析社区（需外网） |
| `make_package.py` | 生成交付包（ZIP，含离线数据集与底图缓存，收件人无需 AK） |
| `verify_package.py` | 将交付包解压到临时目录，在空白环境中完整运行一遍 |
| `lint_style.py` | 代码规范自检（行长 / 编码 / 行尾 / BOM），零依赖 |
| `fix_ps1_bom.py` | 为 `tasks.ps1` 补回 UTF-8 BOM（编辑器常会丢失该标记） |

## 2. 自检与验收（进 `tasks.ps1 verify` 链）

| 脚本 | 作用 |
| --- | --- |
| `check_api.py` | 端到端 API 自检（48 项）：端点、口径、前端资源、演示 UI 结构 |
| `tour_shots.py` | 演示流程逐步自检与取景截图（CDP 驱动无头 Edge，实跑 12 步） |
| `robustness.py` | 稳健性检验：替换权重、阈值、步速，检查排名与短板是否稳定 |
| `marginal.py` | 边际效益曲线：第 k 处设施可额外覆盖的户数 |
| `scope_delta.py` | 分析范围口径核对：逐社区统计范围外居住组团（`--rebuild` 输出前后对照） |
| `stitch_network.py` | 断头路补连（就地重算存量数据集） |
| `fix_carriers.py` | 载体表归并（一座地铁站只保留一条），并刷新 `meta.carrier_meta` |
| `name_clusters.py` | 居住组团实名（使用百度小区 POI，读取缓存） |
| `check_docs.py` | 文档规范自检：章节编号连续性、交叉引用有效性、文件格式与措辞 |
| `check_doc_links.py` | 文档内链检查：检出指向未跟踪路径的链接，避免 GitHub 上出现死链 |
| `check_no_secrets.py` | 密钥扫描：扫描暂存内容，暂存区为空时回退到 HEAD 树；`tasks.ps1 verify` 第 1 环执行 |

## 3. 出图与横向对比

| 脚本 | 作用 |
| --- | --- |
| `route_map.py` | 一天动线出图、单组团风圈图与住户画像对比（报告图 3/4 + 5.1/5.3） |
| `compare.py` | 跨社区横向对比，输出 `out/compare.html` |

## 4. 一次性 / 诊断探针

这些脚本用于排查具体问题，多数需要外网或特定环境，常规流程不执行。

| 脚本 | 用途 |
| --- | --- |
| `ak_probe.py` | 带硬超时的连通性 + AK 分步探针，任一步失败都不阻塞 |
| `check_baidu_ak.py` | 校验 AK 是否可用：地点检索 / 地理编码 / 步行路径规划 |
| `crs_check.py` | 坐标系换算核验（BD-09 ↔ WGS-84，抽 8 组已知地物） |
| `calibrate_walk.py` | 自算路网与百度步行路径规划抽样对照（30 组，用于步速标定） |
| `basemap_scale_check.py` | 百度静态底图实际分辨率标定（权威口径：262150·cos(lat)/2^zoom） |
| `basemap_align_probe.py` | 底图对齐核验（不依赖浏览器 JSAPI） |
| `basemap_diag.py` / `baidu_diag.py` | 浏览器端地图加载失败 / 百度检索返回异常时的分步诊断 |
| `align_check.py` | 将自建路网叠加到百度静态底图上，通过目视与指标确认不偏移 |
| `overpass_probe.py` / `probe_net.py` | Overpass 镜像可用性与网络出口探测 |
| `smoke.py` | 最小冒烟测试（引擎 ↔ 服务 ↔ 前端全链路） |
| `add_standard_refs.py` | 为要素清单补充标准依据字段（幂等；规范条文仍需逐条核对原文） |
| `add_build_notes.py` | 为可落地要素补充建设形态、规模、审批字段（幂等） |

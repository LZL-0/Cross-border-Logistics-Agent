# 跨境物流引擎（Cross-border Logistics Engine）

一个面向跨境出口场景的**多智能体物流决策系统**：输入「货物 + 重量尺寸 + 货值 + 目的国」，
输出**多承运商比价（含税费的总成本）**、**目的国清关时效预估**、**HS 归类与合规要求**、
**端到端方案（承运商 + 单证 + 时间线 + 风险 + 优化建议）**。

架构骨架参考开源项目 [Q_Reptile](https://github.com/QU1453/Q_Reptile)（FastAPI + 原生前端的
多智能体分层骨架），本仓库把「爬虫获客」领域替换为「跨境物流履约」，并修掉了原骨架里
工具越权、汇总智能体不可达等结构性问题（见 `struct.md` 的「相对原骨架的四处修补」）。

- 默认**离线可跑**：知识库为内置 JSON，报价为可复现的规则引擎，不依赖任何外部 API。
- 配置 `LLM_API_KEY` 后切换为 LLM ReAct 决策（已接 DeepSeek，`openai` 兼容接口）；
  **没有 Key 时自动走 classic 规则兜底**，功能完整、结果可解释。
- 开启实时接入后（默认开启），智能体可自己联网取数：运价指数、汇率、关税/船期新闻，
  回答里带上取数时间与来源；关掉即回到纯基线。

---

## 1. 能力清单

| 能力 | 说明 |
|---|---|
| 多承运商比价 | 9 家承运商 × 6 种运输方式，计费重/体积重取大、阶梯进位、燃油与旺季附加、目的港杂费、代理清关费逐项拆开 |
| 总成本（landed cost） | 运费 + 关税 + 进口 VAT/GST + 代理清关预估，按 min-max 归一化后综合评分（成本 50 / 时效 30 / 准点 8 / 风险 10） |
| 清关时效预估 | 基础天数 + 单证缺失 + 认证缺失 + 品类风险 + 危险品 + 承运商自营清关 + 口岸拥堵 + 旺季，输出区间/置信度/分解/缺失单证 |
| 税费测算 | 关税税率按 HS × 目的国，`de minimis` 货值豁免，VAT 按 CIF（货值+运费）口径 |
| HS 归类 | 13 个品类关键词匹配 + 10 位 HS 前缀匹配，返回认证要求、风险等级、是否危险品 |
| 端到端方案 | 比价结果 + 清关 + 税费 + 单证缺口 + 四段式时间线（备货/干线/清关/尾程）+ 风险 + 建议 |
| 自然语言入口 | 中文口语解析：国家、方式、重量、尺寸、件数、货值、HS，交由 Supervisor 路由到对应智能体 |
| 报价落盘与导出 | 每次导出写 `data/quotes/quote_log.csv`，可查询历史、可下载报价单 CSV |
| 实时取数 | 联网检索（360 结果页，Bing 兜底）、网页正文抽取、SCFI 综合运价指数、日更汇率；结果带取数时间与来源，失败自动退回基线 |
| 板块实时 | 比价/清关/合规/方案四页各有「实时参考」块（行情 + 汇率折算 + 板块资讯），页面顶部常驻全局行情条 |
| 知识库周更 | 每周生成行情基线 + 口岸时效候选，人工确认后备份落盘；行情基线兼作实时失败时的兜底 |
| 可观测 | 每一步工具调用进 trace（轮次/工具/状态/参数/结果摘要），遥测写 `data/telemetry/live.jsonl` |

---

## 2. 快速开始

```powershell
cd Cross-border-Logistics-Agent

# 1) 依赖（已内置 .venv，可直接用；重建则如下）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2) 启动（默认离线，无需任何 Key）
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000

# 3) 打开看板
start http://127.0.0.1:8000/
```

可选：复制 `.env.example` 为 `.env` 并填写 `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL`
（OpenAI 兼容接口，DeepSeek、通义等均可），重启后智能体走 LLM ReAct；不填则走规则兜底。
LLM 路径需要 `openai` 包（已在 `requirements.txt`）：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

页面共 7 个标签：**对话 / 比价 / 清关 / 合规 / 方案 / 调试 / 配置**；
「配置」页里点 **检测实时链路**，会逐项验模型、检索、汇率、SCFI 是否可用。

---

## 2.5 实时接入（DeepSeek + 联网取数）

`.env` 关键项：

```ini
LLM_API_KEY=sk-...                  # 模型 Key（DeepSeek 控制台生成）
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
REALTIME_ENABLED=1                  # 实时取数总开关
SEARCH_BASE_URL=https://www.so.com/s
SEARCH_NEWS_URL=https://news.so.com/ns     # 360 资讯搜索（带发布时间，优先用）
SEARCH_FALLBACK_URL=https://cn.bing.com/search
SEARCH_ENGINE=news,so,bing           # 引擎列表，逗号分隔；并发跑，按相关度合并
FX_BASE_URL=https://open.er-api.com/v6/latest
SCFI_URL=https://www.sse.net.cn/index/singleIndex?indexType=scfi
TOOL_NET_POLICY=allowlist
TOOL_NET_ALLOWLIST=*                # 收紧时改成 *.bing.com,open.er-api.com 等
```

四个实时工具（四个业务角色都可用）：

| 工具 | 作用 | 数据源 |
|---|---|---|
| `realtime.web_search` | 检索运价/附加费/港口/关税等最新公开信息，返回标题、链接、摘要、发布日期与相关度分 | 360 资讯（news.so.com）→ 360 网页 → Bing 兜底 + RSS 补位 |
| `realtime.fetch_page` | 抓网页正文转纯文本，用于核实原文 | 任意公网页面 |
| `realtime.scfi_index` | SCFI 综合指数：上期/本期/涨跌/涨跌幅 | 上海航运交易所 |
| `realtime.fx_rate` | 实时汇率（含更新时间） | open.er-api.com |

行为约定：凡涉及时效性数据，模型必须先取数再回答，并在正文里写「取数时间 + 来源链接」；
工具失败要做显式说明并退回知识库基线。取数结果缓存 `REALTIME_CACHE_TTL` 秒（默认 180）。
安全边界见第 6 节。

检索编排（`tools/realtime.py`）：多引擎**并发** + 整体 8s 预算（单引擎 news/so 6s、bing 5s）；
结果统一打分（关键词覆盖率 0.55 + 标题命中 0.25 + 权威域名 0.2，再按新鲜度加减，低于 0.2 丢弃）、
按 URL 与标题前 30 字去重；候选不足 2 条时用 RSS 补位；引擎抛异常/超时才进 90s 冷却（返回噪声不冷却）。
响应体新增 `tokens`、`engine`（实际供数的引擎，如 `news` / `so+bing` / `rss`），每条结果带 `score`、`host`。
检索词建议用中文短词（3–5 段），长句或整句英文容易被 360 判偏。

### 2.5.1 板块级实时快照（四个业务页 + 全局行情条）

对话之外，比价 / 清关 / 合规 / 方案四个页面也都带实时数据，由 `logistics_agent/core/live_feed.py` 统一产出：

- **全局行情条**：页面 `<main>` 顶部常驻 SCFI 综合指数、USD/CNY、取数时间，可手动刷新；前端每 5 分钟自动刷新一次。
- **每板块「实时参考」块**：行情 chips（SCFI 本期/上期/涨跌、汇率）+ 按实时汇率折算的 USD 金额 + 该板块话题的资讯（标题/来源/发布时间/链接）+ 降级原因。
- 话题按板块生成：比价 → 「{国家}航线 运价」+「国际快递 燃油附加费」；清关 → 海关政策 + 港口拥堵（带口岸时用口岸名）；合规 → 关税政策 + HS 认证要求；方案合并前三类。
- 缓存与降级：板块快照缓存 `LIVE_TTL` 秒（默认 300）；行情两路并发，失败时用 `data/telemetry/market_cache.json` 里上次成功值兜底并标 `stale`；资讯整体 8s 预算，超时只展示已回结果与 RSS。
- 不伪造：承运商协议价不因"实时"而被改写，实时部分只做行情与公开资讯引用，并附免责口径。

实时资讯的来源与过滤器（`tools/realtime.py` / `core/live_feed.py`）：

| 通道 | 说明 |
|---|---|
| 360 资讯检索 | 主通道（`news.so.com`），`li.res-list` 自带真实 URL、摘要与发布时间，最稳 |
| 360 网页检索 | 补充通道，`li.res-list` 的 `data-mdurl` 取真实地址；被风控时退化为首词百科结果，会被过滤器整批丢掉 |
| Bing RSS/HTML | 末位兜底，5s 上限 |
| RSS 订阅 | `https://www.ennews.com/rss`（亿恩网），带发布时间，候选不足时补位 |
| 噪声过滤 | 剔除百科/维基、站点首页、外交部国家概况；**标题**必须含行业词（运价/关税/港口/认证…，英文另有词表） |

行情侧：SCFI 与汇率两路并发、各自 8s 上限，超时用 `data/telemetry/market_cache.json` 的上次成功值兜底并标 `stale`。

### 2.5.2 知识库周更（一周一更，人工确认后落盘）

板块里的「实时参考」是当场取数；知识库则是**一周校准一次**，由 `logistics_agent/core/kb_sync.py` 负责：

- **范围**：行情基线（SCFI 综合指数 + 主要货币汇率）+ 口岸时效（`ports.json` 的 `congestion_days`）。
- **节奏**：默认每周五 18:00 之后自动生成一次（`KB_SYNC_ENABLED=1`、`KB_SYNC_WEEKDAY=4`、`KB_SYNC_HOUR=18`），
  需要服务在运行；到点只会生成周报，**不会自动改表**。
- **三步走**：
  1. 生成：`POST /api/kb/run`，或配置页点「生成本周周报」；
  2. 查看：`GET /api/kb/proposal`，报告落在 `data/knowledge/weekly/<stamp>.md`；
  3. 写入：`POST /api/kb/apply?confirm=true`，先把 `ports.json` 与 `market_baseline.json` 备份到
     `data/knowledge/_backup/<时间戳>/`，再覆盖对应字段，并清掉知识库缓存。
- **口岸候选的保守规则**：只盯当周拥堵最重的前 5 个口岸；标题必须命中该口岸名、且不是多港口横向对比稿；
  发布时间超过 120 天的旧文不作依据；单次最多调整 ±0.3 天（区间 0.5–8）。本周没有明确信号就不改表。
- **行情基线**：`market_baseline.json` 是二级兜底——实时源和 `market_cache.json` 都挂掉时，
  页面仍能显示上周口径，并标 `stale`。
- **不做什么**：税率表、HS 归类、承运商条款不在自动更新范围（公开源没有可核实的结构化数据），
  仍按内置基线 + 人工维护。

服务没开也想按周跑，用 `scripts/kb_weekly.bat`（只生成周报，不改表），配一条 Windows 计划任务：

```
schtasks /create /sc weekly /d FRI /st 18:30 /tn "跨境物流引擎-知识库周更" ^
  /tr "\"C:\Users\<你的用户名>\Desktop\跨境物流引擎\scripts\kb_weekly.bat\""
```

---

## 3. HTTP 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/ask` | 自然语言入口，返回 `agent / answer / trace / budget` |
| POST | `/api/quote` | 结构化比价（表单直连） |
| POST | `/api/plan` | 结构化全链路方案 |
| POST | `/api/clearance` | 结构化清关时效预估 |
| POST | `/api/export` | 导出报价单（写操作，内部自动带 `confirm=true` 过闸门） |
| GET | `/api/carriers` | 承运商名录（可按国家/方式过滤） |
| GET | `/api/country` | 目的国清关与税制信息 |
| GET | `/api/ports` | 口岸拥堵与平均停留 |
| GET | `/api/hs` | HS 关键词归类 |
| GET | `/api/duty` | 指定 HS + 目的国的税费测算 |
| GET | `/api/stats` | 知识库规模、报价总数、预算、模型与权限模式 |
| GET | `/api/quotes` | 历史报价（CSV 落盘） |
| POST | `/api/upload` | 上传 csv/txt/md（≤5MB，文件名净化） |
| GET/POST | `/api/config` | 读取/写入 `.env` 中可写配置（Key 只回显是否已配置） |
| GET | `/api/debug/tail` | 最近遥测事件 |
| GET | `/api/realtime/search` | 联网检索（`q` / `top_k`） |
| GET | `/api/realtime/fetch` | 抓网页正文（`url` / `max_chars`） |
| GET | `/api/realtime/fx` | 实时汇率（`base` / `symbols`） |
| GET | `/api/realtime/scfi` | SCFI 综合运价指数（最新一期） |
| GET | `/api/realtime/market` | 板块行情快照：SCFI + 主要货币汇率（`force=true` 跳缓存） |
| GET | `/api/realtime/news` | 实时资讯（`q` / `limit`） |
| POST | `/api/kb/run` | 立即生成本周知识库候选更新（只写周报） |
| GET | `/api/kb/proposal` | 查看最新周报（行情候选 + 口岸候选 + 报告路径） |
| GET | `/api/kb/history` | 历次周报摘要 |
| POST | `/api/kb/apply` | 确认写入（需 `confirm=true`，自动备份） |
| GET | `/api/realtime/health` | 模型 / 检索 / 汇率 / SCFI 四段自检 |
| GET | `/` | 单页看板（`{{V}}` 替换为版本号） |

`/api/quote` 请求体示例：

```json
{"country":"德国","weight_kg":20,"length":50,"width":40,"height":30,
 "hs":"6109100010","declared_value":5000,"pieces":1,"mode":"","prefer":"balance"}
```

---

## 4. 数据与计算口径

**知识库（`data/knowledge/`）**

- `countries.json`：11 国（US/CA/DE/FR/NL/IT/ES/GB/JP/SG/AU）——VAT 税率、关税免税额、
  VAT 免税额、按运输方式的清关天数、单证清单、旺季月份、备注。
- `hs_codes.json`：13 个品类（手机 / 笔记本 / 锂电池 / T 恤 / 鞋 / 玩具 / 木家具 / 化妆品 /
  保健品 / 箱包 / 塑料件 / 线缆 / 五金）——关键词、各国关税率、认证要求、风险等级、是否危险品。
- `carriers.json`：9 家（顺丰国际、DHL、UPS Saver、云途专线、燕文小包、中外运空运、
  中远海运 FCL、德迅 LCL、中欧班列）——计费方式、价格、航线系数、时效区间、准点率、
  是否自营清关、危险品资质、重量/货值上限、旺季涨幅、备注。
- `ports.json`：17 个海港/机场——拥堵天数、平均停留。

**计费**

- 快递/空运：计费重 = `max(实重, 长×宽×高 / divisor × 件数)`，再按 `step` 向上进位到计费档。
- 拼柜（LCL）/ 铁路：按 W/M 计费，取 `max(CBM, 重量吨)`。
- 整柜（FCL）：CBM ≤ 28 取 20GP，否则 40HQ。
- 附加项：燃油百分比、旺季附加（月份 ∈ `PEAK_MONTHS` 且 ∈ 目的国 `peak_months`）、
  目的港杂费、代理清关费（承运商不自营清关时计入 `AGENCY_CLEARANCE_FEE`，默认 300 元）。

**清关时效**（工作日口径，不含航班/船期等待）

```
基础天数(国×方式) + 0.35×缺失单证数(上限4项) + 2.0(高风险品类缺认证)
+ 1.2 / 0.5(品类高风险/中风险) + 1.0(危险品) - 0.6(承运商自营清关)
+ 0.6×口岸拥堵天数 + 旺季(海运/空运/铁路 ×1.2，快递 ×0.6) - 0.3(低货值)
```

输出区间、置信度（高/中/低）、逐项 `breakdown`、`missing_docs`、`risks`、`assumptions`。

**时效口径统一**：比价与方案都用 `备货 + 干线 + 清关 + 尾程` 同一口径，
方案时间线的末段日期与「预计签收」一致。

**评分**：`score = 100×(0.50×成本分 + 0.30×时效分 + 0.08×准点分 + 0.10×风险分)`，
成本与时效均按候选集 min-max 归一，风险分含「非自营清关 / 危险品空运 / 清关置信度低」扣分。

---

## 5. 一个可复现的例子

```
20kg 棉制 T 恤（HS 6109100010），50×40×30cm，货值 5000 元，发德国
```

- 比价：8 家可选（燕文小包因单件 ≤2.5kg 被排除）；税费 1,664 元 = 关税 600 + VAT 1,064；
- 推荐：顺丰国际，总成本 3,480 元，9.4–14.2 天；
- 备选：中外运空运 3,200 元（含 300 元代理清关预估）但 12.6–18.1 天、机场到机场；
- 清关（空运 + 已有发票/装箱单）：5.6–8.1 个工作日（置信度低），缺失 EORI、CE 声明、纤维成分标签、护理标签。

```
300kg 木家具（HS 9403600000），120×80×60cm × 10 件，货值 3 万，发德国，拼柜
```

- 德迅 LCL：总成本 10,563 元（含 300 元代理清关预估），47.5–61 天（备货 3 + 干线 34–45 + 清关 7.5–10 + 派送 3）；
- 缺失单证：EORI、CE 声明、熏蒸证明（IPPC）、原产地证（基础发票/装箱单按已备处理）。

---

## 6. 约束与安全

- `AGENT_PERMISSION_MODE`：`plan`（写操作需 `confirm=true`）/ `auto` / `readonly`。
- 写类工具白名单：`shipment.export_quote`（导出报价单），落盘目录限定 `data/quotes/`。
- 工具执行前做**角色校验**（`GLOBAL_REGISTRY.allows(role, name)`），智能体不能调用不属于自己的工具。
- 网络策略 `allowlist` + `NET_TOOLS` 为空集：本项目默认完全离线；如需联网工具，须同时配置域名白名单。
- 上传文件名净化（去目录、限后缀、限 5MB），防目录穿越。
- 报价单里明确声明「时效为估算区间，不作为赔付承诺」；提示不得拆单/低报避税。

---

## 7. 已知限制

- 报价为**规则模拟**（知识库参数化），非实时船司/货代运价；接入真实报价需替换 `tools/rates.py` 的数据源。
- 实时检索是**公开结果页**，多数为服务商宣传页或行业报道，只有 SCFI 与汇率是结构化权威数据；
  引用前需看来源，不能当成承运商协议价。
- SCFI 工具只取综合指数：欧洲/美西等分航线在官网由前端异步加载，需人工看官网当期表。
- 检索引擎（360）风控较严：网页结果页抽风时只返回首个词的百科结果，这类结果会被过滤器整批丢弃；
  资讯通道（`news.so.com`）目前最稳，RSS 再兜一层，智能体也会明确写「本轮未取到权威原文」而不是编细则。
- 检索词用中文短词命中率最高；整句英文（如 `US tariff policy 2026 USTR`）在中文引擎上常无结果，需要模型改写成中文再试。
- 板块「实时参考」里的资讯多为行业媒体/服务商文章，不是官方公告原文；政策类结论必须点链接回原文核实。
- 部分站点会拒绝非浏览器抓取（403）或证书链异常（个别政府站点），此时请改用检索摘要。
- 关税税率是**品类 × 国家的简化矩阵**，不等同于各国完整税则；正式申报以海关税则为准。
- 汇率为哨兵/日更值，全部按人民币计价，不做多币种结算口径换算。
- LLM 路径需要 `openai` 包 + 有效 Key（缺一即全部走规则兜底）。

---

## 8. 目录速查

```
config.py            全局配置槽（路径/模型/权限/预算/业务基线）
server.py            FastAPI 入口与全部 HTTP 接口
logistics_agent/
  core/              toolkit / dispatch / perception / cognition / constraint / reflection / telemetry / output，外加 live_feed（板块实时快照）、kb_sync（知识库周更）
  memory/            短期（会话 JSONL）与长期（偏好/事实）记忆
  tools/             knowledge / clearance / rates / shipment / catalog / document / realtime（16 个注册工具）
  Agents/            supervisor / plan / quote / clearance / compliance / summarizer
data/knowledge/      countries / hs_codes / carriers / ports；market_baseline.json（周更行情基线）
data/knowledge/weekly/   每周候选更新（json + md），latest.json 指向最新一版
data/knowledge/_backup/  每次 apply 前的快照备份
data/telemetry/      live.jsonl 遥测 + market_cache.json 行情兜底缓存
ui/                  index.html + app.js + style.css（原生，无构建）
```

更细的模块职责、数据流与设计取舍见 `struct.md`；版本变更见 `CHANGELOG.md`。

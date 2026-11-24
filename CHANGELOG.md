# CHANGELOG

## 20261124（未收录品类自动收录 + 周更品类复核）

### 新增
- **品类候选收录**（`logistics_agent/core/kb_ingest.py`）：`ingest / find_pending / review / list_candidates /
  apply_weekly`。`lookup_hs` 未命中时用 `realtime.web_search` 检索 1–2 组 query，抽取 6–10 位 HS、
  认证词与是否带电，写入 `data/knowledge/hs_candidates.json`（pending），保留标题/链接/发布时间/查询词。
- **确认才生效**：候选不参与 HS 匹配与税费测算；`review(action=confirm)` 后写入 `hs_codes.json`
  的 `auto_items`（同基线结构 + `source/confidence/evidence`），`hs_items()` 合并读取，确认后立即生效。
  确认时可改 HS；无 HS 的候选拒绝确认；同一 HS 已存在则合并关键词、不重复登记。
- **周更扩围**：`kb_sync` 周报新增「自动收录品类复核」——重检已确认条目 HS、刷新证据、连续 3 周无证据停用；
  超期且无证据的 pending 进清理建议；`apply_latest` 一并备份 `hs_codes.json` / `hs_candidates.json` 并落盘。
- **API**：`GET /api/kb/candidates`、`POST /api/kb/candidates/confirm|reject`、`POST /api/kb/ingest`。
- **配置页**：新增「品类候选（自动收录）」卡片，可改 HS 后确认入库或忽略；周更卡片显示品类复核摘要。
- **配置项**：`KB_AUTO_INGEST`（默认 1）、`KB_CANDIDATE_TTL_DAYS`（默认 30）；`.env.example` 已补。

### 行为约束
- 同关键词 10 分钟内不重复检索；被忽略的品类 7 天内不再自动检索；检索结果先按品名/英文词过滤，压掉同日无关新闻。
- 自动收录条目的目的国税率未核实时按默认 5% 计，`duty_calc` 返回 `source=auto`、`confidence`、`evidence` 与提示。
- 周更仍只出周报，人工 `confirm=true` 才写盘；写前备份四份文件。

### 验证
- 实测「LED植物生长灯」：未收录 → 检索出候选（HS 85312000，低置信）→ 确认 → `lookup_hs` 命中 `source=auto`
  → `duty_calc` 2000 元货值 DE 税费 556 元（关税 100 + VAT 456，默认 5%）→ 周报复核 verify=1、apply 成功并可继续匹配。
- 测试在知识库完整备份上执行，结束后已回滚测试数据（未留候选与测试周报）。

## 20261123（接入 DeepSeek + 实时取数）

### 新增
- **模型接入**：`.env` 填 `LLM_API_KEY` 即走 DeepSeek（`openai` 兼容接口），
  新增 `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` 两个可调项；`openai` 已装进 `.venv`。
- **实时取数工具**（`logistics_agent/tools/realtime.py`，4 个）：
  - `realtime.web_search`：联网检索最新公开信息（360 结果页为主，Bing RSS 兜底），
    解析 `data-mdurl` 取真实目标地址，跳过广告位与跳转链；
  - `realtime.fetch_page`：抓网页正文转纯文本（去脚本/样式、压空白、限长）；
  - `realtime.scfi_index`：上海航运交易所 SCFI 综合指数（上期/本期/涨跌/涨跌幅/期次日期）；
  - `realtime.fx_rate`：open.er-api 日更汇率（带更新时刻）。
- **调度与提示词**：四个业务角色均可调用实时工具；系统提示里写明今天日期、
  参数名必须与函数签名一致、涉及时效性数据必须先取数并标注「取数时间 + 来源」。
- **可观测与自检**：新增 `/api/realtime/{search, fetch, fx, scfi, health}`；
  「配置」页新增 **实时接入** 卡片，一键检测模型 / 检索 / 汇率 / SCFI 四段链路。
- **配置项**：`REALTIME_ENABLED`、`SEARCH_BASE_URL`、`SEARCH_FALLBACK_URL`、`SEARCH_ENGINE`、
  `FX_BASE_URL`、`SCFI_URL`、`REALTIME_TIMEOUT`、`REALTIME_CACHE_TTL`、`REALTIME_MAX_RESULTS`、
  `FETCH_MAX_CHARS`；`/api/config` 可写，Key 只报名「是否已配置」不回显。

### 变更
- `REACT_MAX_ROUNDS` 默认 4 → 6；工具清单改为「名称(参数: 类型)」签名形式，
  修掉模型自创参数名导致的连续失败（`lookup_hs(query=...)`、`duty_calc(value=...)`）。
- 网络白名单支持 `*` / `*.x.com` / `.x.com` 通配；`NET_TOOLS` 由空集改为实时工具集合。
- `.env.example` 同步新增全部实时项，默认 `TOOL_NET_ALLOWLIST=*`（收紧方式见 README）。

### 安全
- realtime 模块自带 SSRF 防线：仅 http/https、DNS 解析后拦内网/回环/链路本地/保留地址、
  重定向逐跳复检、响应体 ≤2MB、超时与缓存可配；403/404/证书异常给出可读提示。

### 板块实时化（同日第二轮）
- **新增 `logistics_agent/core/live_feed.py`**：`market()`（SCFI + 汇率并发、磁盘兜底）、
  `news()`（检索 + RSS 双通道、跑题过滤、去重）、`panel()` / `safe_panel()`（按板块出快照）。
- **四个业务工具注入 `live`**：`rates.compare_rates`（比价）、`clearance.estimate_clearance`（清关）、
  `knowledge.duty_calc`（合规/税费）、`shipment.plan_shipment`（方案）；只有 `compare()` 层面注入，
  各承运商单次估算不加，避免网络放大。
- **前端**：`ui/index.html` 顶部全局行情条（SCFI / USD-CNY / 更新时间 / 刷新），
  `ui/app.js` 新增 `renderLive()` 与四处接入、`refreshMarket()` 定时刷新（5 分钟），
  `ui/style.css` 新增 `.live-strip` / `.live-chip` / `.live-block` 样式。
- **接口**：新增 `/api/realtime/market`（`force` 跳过缓存）、`/api/realtime/news`；
  服务启动时后台预热行情，避免首屏等 10s。
- **检索鲁棒性**：360 单引擎 6s 上限、Bing 5s；检索结果先按物流语义过滤（剔除百科/维基/
  站点首页/外交部国家概况），不足 2 条时用 `ennews` RSS 按关键词补位；板块资讯整体 8s 预算。
- **新增配置**：`LIVE_TTL`(300s)、`LIVE_NEWS_LIMIT`(3×2 条)；行情兜底缓存落
  `data/telemetry/market_cache.json`。

### 知识库周更（同日第三轮）
- **新增 `logistics_agent/core/kb_sync.py`**：`build_proposal()` 生成周报（行情基线 + 口岸时效候选），
  `apply_latest(confirm=True)` 备份后落盘，`start_scheduler()` 每周定时生成（只生成不改表）。
- **范围与口径**：
  - 行情基线写 `data/knowledge/market_baseline.json`（SCFI + 主要货币汇率），
    并作为 `live_feed.market()` 的二级兜底（实时源与 market_cache 都失效时仍显示上周值并标 stale）；
  - 口岸只盯当周拥堵最重的前 5 个，标题须命中口岸名、排除多港口横向对比稿、排除发布 >120 天的旧文，
    单次最多 ±0.3 天（0.5–8 区间）；无明确信号不改表。
- **人工确认链路**：`POST /api/kb/run` → `GET /api/kb/proposal` → `POST /api/kb/apply?confirm=true`；
  新增 `GET /api/kb/history`；配置页新增「知识库周更」卡片（生成 / 确认写入）。
- **产物与备份**：`data/knowledge/weekly/<stamp>.json|.md` + `latest.json`；每次写入前备份到
  `data/knowledge/_backup/<时间戳>/`；落盘后自动清知识库 `lru_cache`，无需重启生效。
- **新增配置**：`KB_SYNC_ENABLED`、`KB_SYNC_WEEKDAY`(4=周五)、`KB_SYNC_HOUR`(18)。
- **无服务场景**：`scripts/kb_weekly.py` + `scripts/kb_weekly.bat`（只生成周报，日志落
  `data/knowledge/weekly/_task.log`），可挂 Windows 计划任务每周跑一次。
- **不做**：税率表、HS 归类、承运商条款不自动更新（公开源无可核实结构化数据）。

### 检索重构（同日第四轮）
- **新增资讯引擎**：`SEARCH_NEWS_URL=https://news.so.com/ns`，解析 `li.res-list` 的 `data-url` /
  `h3` / `p.summary` / `span.time`，拿真实地址 + 摘要 + 发布时间；`SEARCH_ENGINE` 默认改为 `news,so,bing`。
- **并发与预算**：多引擎同时跑、整体 8s（news/so 6s、bing 5s）；某家已给足候选即提前返回，不等慢引擎；
  只有异常/超时才进 90s 冷却，返回噪声不冷却（避免一次风控把后续查询全拖死）。
- **打分与去重**：关键词覆盖率 0.55 + 标题命中 0.25 + 权威域名 0.2，叠加新鲜度（30 天内 +0.12、
  120 天内 +0.04、一年内 -0.3、更旧 -0.5）；低于 0.2 丢弃；URL + 标题前 30 字去重；
  `_tokens` 去泛词/纯数字、长句只取最长 4 段，命中按 2–3 个词就封顶，避免长句全灭。
- **过滤收紧**：`live_feed._is_junk` 要求**标题**含行业词（英文另有词表），把「午盘播报」「交易大百科」
  这类借摘要混进来的泛内容挡掉；RSS 补位门槛改为 token ≥4 才需 2 个命中。
- **降级可观测**：`/api/realtime/search` 响应新增 `tokens`、`engine`（实际供数引擎），每条结果带
  `score` / `host` / `published`；失败时错误串写明「已过滤 N 条跑题结果」与各引擎状态。
- **超时收紧**：汇率与行情两路各自 8s 上限，超时用 `market_cache.json` 上次成功值兜底；
  板块快照实测比价 0.8–1.4s、清关 1.2s、合规 0.9–1.8s、方案 0.8s。
- **提示词**：实时提示加「检索词用中文短关键词（3–5 段），不要整句英文或长句，查不到就换短说法再试一次」。

---

## 20261121（首版）

### 新增
- **系统骨架**：FastAPI 接入 + Supervisor 规则路由 + 5 个业务智能体（plan / quote / clearance /
  compliance / summarizer）+ 12 个注册工具 + 四道约束检查 + 会话预算 + 遥测。
- **知识库**：`countries.json`（11 国）、`hs_codes.json`（13 品类）、`carriers.json`（9 承运商）、
  `ports.json`（17 口岸），全部内置 JSON，离线可用。
- **计费与比价**：实重/体积重取大与阶梯进位、W/M 计费、整柜 20GP/40HQ 选型、燃油与旺季附加、
  目的港杂费、关税（含 `de minimis` 豁免）与 VAT（CIF 口径）、min-max 归一评分
  （成本 50 / 时效 30 / 准点 8 / 风险 10）。
- **清关时效**：8 类因素加权（单证缺失、认证缺失、品类风险、危险品、自营清关、口岸拥堵、旺季、
  低货值），输出区间、置信度、逐项分解、缺失单证与风险。
- **端到端方案**：比价 + 税费 + 单证缺口 + 四段式时间线 + 风险 + 优化建议。
- **中文口语解析**：国家/方式/重量/尺寸/件数/货值/HS 别名表与正则抽取。
- **看板（原生前端，无构建）**：对话 / 比价 / 清关 / 合规 / 方案 / 调试 / 配置 七个标签，
  含 trace 折叠表、导出按钮、历史报价与遥测 tail。
- **落盘**：`data/quotes/quote_log.csv`（导出即写）、`data/memory/*.jsonl`、`data/telemetry/live.jsonl`。
- **文档**：`README.md`（能力/接口/口径/示例）、`struct.md`（分层/工具矩阵/数据结构/扩展指引）。

### 修复（相对 Q_Reptile 骨架的结构性问题）
- 工具执行增加**角色越权校验**（`GLOBAL_REGISTRY.allows(role, name)`），不再只靠提示词约束；
  实测 `QuoteAgent` 调 `shipment.plan_shipment` 被拒。
- 网络检查改为**递归扫描所有参数**中的 `http(s)` 值（原骨架只查顶层 `url`），清理
  `if name in NET_TOOLS or True` 的恒真分支。
- `SummarizerAgent` 由不可达变为可达：`ROUTE_RULES` 增加「总结/汇总/复盘/统计/报价记录/历史」分支。
- 模型失败转兜底时**合并 LLM 与 classic 两段 trace**，并记录 `llm_error` 遥测，过程可追溯、不重复副作用。
- 补齐 `requirements.txt` 缺失的 `python-multipart`（否则 `/api/upload` 会导致服务启动失败）。

### 修正（本轮自测中发现的口径问题）
- **时间线**：由"按总时效比例切分"改为按真实天数推进（备货 → 干线 → 清关 → 尾程），
  修掉 LCL 场景"干线仅 3–31 天"与实际 34–45 天船期不符的问题。
- **时效口径统一**：比价与方案都改为 `备货 + 干线 + 清关 + 尾程`，方案时间线末段日期与
  `eta_date` 右端一致（此前尾程多算 3 天备货导致不一致）。
- **比价可比性**：不自营清关的承运商统一计入 `AGENCY_CLEARANCE_FEE`（默认 300 元）并写入
  `flags`，避免"未含清关的低价"被误判为最优；机场到机场/港到港另加"不含尾程派送"提示与风险扣分。
  修复后德国 20kg T 恤场景推荐由"中外运空运"变为"顺丰国际"（3,480 元 vs 3,200 元但慢 3 天）。
- **单证默认口径**：方案/比价/清关统一按"已备商业发票与装箱单"估算，并在 `assumptions` 明示，
  避免同一票货在比价页"单证齐全"、方案页"缺发票"的自相矛盾。
- **展示口径**：比价明细补齐「+ 代理清关 300」分项，避免出现 1,236 + 1,664 = 3,200 的对不上账；
  承运商名称已含方式时不再重复括号（修掉"云途物流（专线）（国际快递）"）；窄窗口下结果表格改为横向滚动，
  表头不再被压成竖排。

### 验证
- `compare_rates('德国',20,50×40×30,'6109100010',5000)`：8 家可选，税费 1,664（关税 600 +
  VAT 1,064），推荐顺丰国际 3,479.91 元 / 9.4–14.2 天。
- `estimate_clearance('德国','air','6109100010',已备发票+装箱单)`：5.6–8.1 工作日，置信度低。
- `plan_shipment('德国',300kg,120×80×60×10,3 万,'9403600000','lcl')`：德迅 LCL，10,562.9 元，
  47.5–61 天，缺 EORI / CE / 熏蒸 / 原产地证。
- HTTP：`/` 200（版本占位已替换）、`/api/stats`（11/13/9/17）、`/api/quote`、`/api/plan`、
  `/api/clearance`、`/api/export`（写盘成功）、`/api/duty`、`/api/carriers`、`/api/config`、
  `/api/quotes`、`/api/ask`（plan/quote/clearance/summarizer 路由均命中）。
- 前端 `node --check ui/app.js` 通过。

### 已知限制
- 运价为规则模拟，非实时船司/货代报价；税则为品类×国家简化矩阵；全系统人民币计价，不做汇率换算。
- 未安装 `openai` 或无 `LLM_API_KEY` 时 LLM ReAct 不可用，全部走 classic 兜底（功能完整）。

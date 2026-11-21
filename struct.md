# struct.md — 架构与模块说明

> 配套文档：运行与口径见 `README.md`，变更记录见 `CHANGELOG.md`。

## 1. 一句话架构

**FastAPI 接入层 → Supervisor 规则路由 → 领域智能体（ReAct + classic 兜底）→ 12 个注册工具 → JSON 知识库 + 规则引擎 → 格式化输出 + 落盘/遥测**。

分层目的：把「理解问题」「决定调什么」「怎么算」「怎么防越权」拆开，任何一层都可以单独替换或扩展：

- 换模型：只动 `core/cognition/react.py` 与 `config.py`；
- 换数据源：只动 `logistics_agent/tools/*`；
- 加意图：只动 `Agents/supervisor.py` 的 `ROUTE_RULES` 与新增 Agent；
- 加工具：`tools/` 里写函数 + `@register`，再在 `dispatch/registry.py` 登记角色。

## 2. 目录与职责

```
config.py                        唯一配置入口：路径、模型、权限、预算、业务基线（旺季/尾程/代理费）
server.py                        FastAPI 入口：REST 接口、静态页、配置读写、上传、遥测 tail
logistics_agent/
├─ core/                         「与业务无关」的骨架层（可迁移到其它领域）
│  ├─ toolkit/decorator.py       @register 装饰器 + 进程内 REGISTRY（名称/说明/角色/func）
│  ├─ dispatch/registry.py       GlobalRegistry：tools_for(role) / allows(role,name) 运行时越权校验
│  ├─ dispatch/orchestrator.py   会话编排：session、预算、Supervisor 调用的统一外壳
│  ├─ perception/input.py        输入清洗（去控制字符、限长、压缩空白）
│  ├─ perception/parser.py       中文口语抽取：国家/方式/重量/尺寸/件数/货值/HS（别名表 + 正则）
│  ├─ perception/context.py      系统提示 + 规则 + 记忆 → 上下文文本（带字符预算）
│  ├─ perception/session.py      会话 ID 生成/校验
│  ├─ cognition/react.py         ReAct 循环：模型只输出一个 JSON（action+args 或 final），最多 N 轮
│  ├─ constraint/layer.py        三道闸门：白名单/写操作 confirm/参数净网校验 + 预算检查
│  ├─ constraint/budget.py       会话级工具调用计数（MAX_TOOL_CALLS）
│  ├─ constraint/validator.py    参数递归校验：URL 域名白名单、路径根限制
│  ├─ constraint/rules.py        DEAD_RULES：禁止编造运价/税率、禁止承诺时效、禁止协助低报
│  ├─ reflection/               reflect()：回答与 trace 的一致性/风险自检
│  ├─ telemetry/live.py         工具/思考/错误事件 → data/telemetry/live.jsonl
│  └─ output/formatter.py       统一返回体 build(answer, trace, agent) / error()
├─ memory/                       短期（会话轮次 JSONL）+ 长期（偏好/事实 KB）+ JSONL 落盘
├─ tools/                        领域能力（12 个工具，见 §4）
│  ├─ knowledge.py               HS 查询、税则查表、目的国信息、compute_duty、required_docs
│  ├─ clearance.py               estimate()/estimate_clearance()、口岸拥堵、缺失单证与风险
│  ├─ rates.py                   计费重/W-M/整柜选型、附加费、税费、归一评分、推荐
│  ├─ shipment.py                plan_shipment()、时间线、export_quote()、quote_stats()
│  ├─ catalog.py                 报价 CSV 落盘（QUOTE_FIELDS / append_rows / quote_rows）
│  └─ document.py                parse_document：csv/txt/md 解析为结构化行
├─ Agents/                       业务智能体（每个都有 classic_<role>_reply 兜底）
│  ├─ base.py                    ReActAgentBase：领取工具、过闸门、LLM→兜底、记忆、反思
│  ├─ supervisor.py              规则路由（不持工具、不调 LLM）+ stats()
│  ├─ plan_agent.py              全链路方案
│  ├─ quote_agent.py             比价（表格 + 推荐 + 权衡）
│  ├─ clearance_agent.py         清关时效（区间 + 分解 + 缺失单证 + 风险）
│  ├─ compliance_agent.py        HS / 税率 / 认证 / 单证
│  └─ summarizer.py              统计与复盘（读落盘报价，不依赖 LLM）
data/knowledge/                  countries.json / hs_codes.json / carriers.json / ports.json
data/quotes/quote_log.csv        报价落盘（导出即写一行）
ui/                              index.html + app.js + style.css（原生，无构建链）
```

## 3. 一次请求的生命周期

1. `POST /api/ask` → `Orchestrator.handle(text, session_id)`（生成/校验会话，写 request/response 遥测）；
2. `Supervisor._route()` 用 `ROUTE_RULES` 正则按顺序命中（plan → summarizer → quote → clearance → compliance，全不中默认 quote）；
3. 选中 Agent 实例调用 `ReActAgentBase.ask()`：
   1. `clean_input` → `ensure_session`；
   2. 有 `LLM_API_KEY` → `MEMORY.recall` + `build_context` → `run_react`（模型每轮输出 `{"action","args"}` 或 `{"final"}`，工具结果作为 observation 回填，最多 `REACT_MAX_ROUNDS` 轮）；
   3. 模型异常/无 Key → 调 `classic_<role>_reply`，**并保留已执行工具的 trace**（避免"重放"副作用且过程透明）；
   4. `MEMORY.remember` 写短期记忆 → `reflect()` 自检 → `build()` 统一返回。
4. 每次工具调用在 `_execute_tool` 内依次过：**角色校验 → 约束层（白名单/confirm/净网）→ 预算 → 执行**，失败一律以 `{"error": ...}` 回填而不是抛给用户；
5. 返回 `{agent, answer, trace[], budget}`；trace 首条固定是 `route`。

> 兜底路径是"第一公民"：没有 Key 时全部能力仍可用，只是不做自由文本推理，改为「解析 → 调工具 → 模板化回答」。

## 4. 工具 — 角色矩阵（`dispatch/registry.py` 为准）

| 工具 | plan | quote | clearance | compliance | 说明 |
|---|:-:|:-:|:-:|:-:|---|
| `document.parse_document` | ✓ | ✓ | ✓ | ✓ | `*` 角色通用；上传文件解析 |
| `knowledge.lookup_hs` | ✓ | ✓ |  | ✓ | HS 关键词/前缀归类 |
| `knowledge.duty_calc` | ✓ | ✓ |  | ✓ | 关税 + VAT |
| `knowledge.lookup_country` | ✓ |  | ✓ | ✓ | 税制/单证/旺季 |
| `clearance.estimate_clearance` | ✓ |  | ✓ |  | 清关时效区间 |
| `clearance.port_status` | ✓ |  | ✓ |  | 口岸拥堵 |
| `rates.list_carriers` | ✓ | ✓ |  |  | 承运商名录 |
| `rates.quote_rates` | ✓ | ✓ |  |  | 单承运商报价 |
| `rates.compare_rates` | ✓ | ✓ |  |  | 多承运商比价 |
| `shipment.plan_shipment` | ✓ |  |  |  | 端到端方案 |
| `shipment.export_quote` | ✓ | ✓ |  |  | **写操作**，需 `confirm=true` |
| `shipment.quote_stats` | ✓ | ✓ |  |  | 历史报价统计 |

写操作集合：`WRITE_TOOLS = DANGEROUS_WRITE = {"shipment.export_quote"}`；
`AGENT_PERMISSION_MODE=plan` 时必须 `confirm=true`，`readonly` 一律拒绝。

## 5. 约束层（四道检查 + 预算）

```
_execute_tool(name, args)
  ├─ ① GLOBAL_REGISTRY.allows(role, name)      角色越权 → 拒绝（执行级，不只靠提示词）
  ├─ ② check_tool_call(name, args)             权限 L1/L2 → 路径根 → 净网 → 危险写 confirm
  └─ ③ check_budget(session_id)                会话工具预算（默认 12 次）
```

- **权限**：`memory/access.check("L1"/"L2")`，写类工具走 L2。
- **路径**：`validator.check_paths` 递归扫描 `path/file/dir/root/out_path/export_path` 等键，
  拒绝含 `..` 或落在 `TOOL_PATH_ROOTS`（默认项目根）之外的绝对路径。
- **净网**：`layer._iter_urls` 递归扫描**所有**参数里的 `http(s)` 字符串（不只 `url` 字段），
  `TOOL_NET_POLICY=allowlist` 时域名必须在 `TOOL_NET_ALLOWLIST`；`block` 一律拒绝。
  本项目 `NET_TOOLS` 为空集，默认完全离线。
- **危险写**：`AGENT_PERMISSION_MODE=plan` 时 `shipment.export_quote` 必须 `confirm=true`。
- **提示词层**：`DEAD_RULES` 明确「不编造运价/税率、不承诺时效、不协助低报或拆单避税」。

## 6. 关键数据结构

**比价 `compare()` →**

```jsonc
{
  "query": {"country":"DE","country_name":"德国","mode":"不限","weight_kg":20,"dims_cm":"50 40 30",
             "pieces":1,"hs":"6109100010","declared_value":5000},
  "count": 8,
  "quotes": [{"carrier":"顺丰国际","service":"国际快递 · 门到门","mode":"express","mode_name":"国际快递",
              "own_clearance":true,"ontime":0.92,"max_kg":30,"transit_days":[7,11],
              "chargeable_weight":20,"freight":...,"fuel":...,"peak":...,"dest_fee":...,
              "total":1815.91,"tax":1664,"duty":600,"vat":1064,"agency_fee":0,
              "landed_total":3479.91,"eta_days":[9.4,14.2],"eta_date":"12-01 ~ 12-06",
              "score":83.4,"flags":[]}],
  "excluded": [{"carrier":"燕文物流（小包）","reason":"单件超重 20kg > 2.5kg"}],
  "recommendation": {"carrier":"顺丰国际","landed_total":3479.91,"eta_date":"12-01 ~ 12-06",
                     "score":83.4,"reason":"...","tradeoff":"..."},
  "tax": {...}, "assumptions": [...]
}
```

**清关 `estimate_clearance()` →**

```jsonc
{"clearance_days":[5.6,8.1],"confidence":"低","tail_days":2,
 "breakdown":[{"item":"目的国基础清关","days":2.5},...],
 "missing_docs":["EORI 号","CE 符合性声明"],"docs_covered":["商业发票","装箱单"],
 "risks":[...],"assumptions":[...],"note":"时效为估算区间，不作为赔付承诺"}
```

**方案 `plan_shipment()` →** `recommendation / chosen_quote / alternatives / tax / duty_summary /
clearance / docs{required,covered,missing} / timeline[{stage,start,end,date,note}] / risks / advice / assumptions`。

## 7. 相对 Q_Reptile 骨架的四处修补

原骨架（FastAPI + 原生前端 + Supervisor/子 Agent + 工具注册表）分层清晰，但存在几个结构性问题，本仓库逐条修掉：

| # | 原骨架问题 | 本项目做法 |
|---|---|---|
| 1 | 只靠提示词约束角色，工具执行不校验归属，子 Agent 可越权调别人的工具 | `_execute_tool` 先 `GLOBAL_REGISTRY.allows(role, name)`，越权直接拒绝并写遥测（实测被拦截） |
| 2 | 网络白名单只查顶层 `url` 参数，嵌套/别名字段可绕过 | `layer._iter_urls` 递归扫描所有含 `http(s)` 的参数值，命中即校验域名 |
| 3 | `SummarizerAgent` 因 `ROUTE_RULES` 无对应关键词而**永远不可达**（死代码） | 路由表新增 `summarizer` 分支（总结/汇总/复盘/统计/报价记录/历史），并实测可达 |
| 4 | 模型失败转兜底时 trace 丢失，前端看不到"模型失败→规则兜底"，且可能重复副作用 | 兜底时合并 `trace = llm_trace + classic_trace`，已执行工具记录在 `_executed` 并写 `llm_error` 遥测 |

另外两处领域化改造：`source_url` 之类"存本地路径冒充 URL"的字段在本文项目中被彻底移除（报价落盘只写
`quote_id / created_at / ... / note` 等真实字段）；`classic` 兜底从"一句提示"升级为**完整业务回答模板**
（比价排名、清关分解、方案时间线），保证无 Key 时也是可用产品。

## 8. 扩展指引

- **加承运商**：在 `carriers.json` 追加一条（`billing/price/lane_factor/transit/ontime/own_clearance/
  dangerous/max_kg/max_declared/peak_pct`），无需改代码。
- **加目的国**：`countries.json` 追加（`vat_rate/duty_de_minimis_cny/vat_de_minimis_cny/
  clearance_days/docs/peak_months`），并在 `perception/parser.py` 的国家别名表补中文名/代码。
- **加 HS 品类**：`hs_codes.json` 追加（`keywords/duty/certs/risk/dg`）。
- **加工具**：`tools/xxx.py` 写函数 + `@register(roles=[...], name="ns.tool")`，并在
  `dispatch/registry.py` 的 `TOOL_ROLES` 登记（该表是文档镜像，两处必须同步）。
- **加智能体**：继承 `ReActAgentBase`，实现 `classic_<role>_reply`，在 `Supervisor._agent` 与
  `ROUTE_RULES` 注册；不实现兜底则该角色在无 Key 时不可用。
- **接真实运价**：替换 `rates.py` 中 `_base_price` 的数据来源（HTTP/数据库），保持
  `QuoteResult` 字段不变，上层与前端零改动。

## 9. 测试与验证基线

```
python -c "from logistics_agent.tools.rates import compare_rates; print(compare_rates('德国',20,50,40,30,'6109100010',5000)['count'])"   # 8
python -c "from logistics_agent.tools.shipment import plan_shipment; print(plan_shipment('德国',300,120,80,60,30000,'9403600000','lcl')['chosen_quote']['carrier'])"
uvicorn server:app --port 8000   # 首页 200、/api/stats、/api/quote、/api/plan、/api/export 均返回 ok
```

健康检查清单：`/` 返回 200 且 `{{V}}` 已替换；`/api/stats` 知识库计数为 11/13/9/17；
`/api/quote` 返回 8 家且推荐总成本 = 运费 + 税费 + 代理费；`/api/plan` 时间线末段日期 = `eta_date` 右端。

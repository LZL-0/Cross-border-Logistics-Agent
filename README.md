# 跨境物流引擎（Cross-border Logistics Engine）

一个面向跨境出口场景的**多智能体物流决策系统**：输入「货物 + 重量尺寸 + 货值 + 目的国」，
输出**多承运商比价（含税费的总成本）**、**目的国清关时效预估**、**HS 归类与合规要求**、
**端到端方案（承运商 + 单证 + 时间线 + 风险 + 优化建议）**。

架构骨架参考开源项目 [Q_Reptile](https://github.com/QU1453/Q_Reptile)（FastAPI + 原生前端的
多智能体分层骨架），本仓库把「爬虫获客」领域替换为「跨境物流履约」，并修掉了原骨架里
工具越权、汇总智能体不可达等结构性问题（见 `struct.md` 的「相对原骨架的四处修补」）。

- 默认**离线可跑**：知识库为内置 JSON，报价为可复现的规则引擎，不依赖任何外部 API。
- 配置 `LLM_API_KEY` 后可切换为 LLM ReAct 决策；**没有 Key 时自动走 classic 规则兜底**，
  功能完整、结果可解释。

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

页面共 7 个标签：**对话 / 比价 / 清关 / 合规 / 方案 / 调试 / 配置**。

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
- 关税税率是**品类 × 国家的简化矩阵**，不等同于各国完整税则；正式申报以海关税则为准。
- 汇率为哨兵值，全部按人民币计价，不做多币种换算。
- 未安装 `openai` 时 LLM 路径不可用（默认即此状态），全部走规则兜底。

---

## 8. 目录速查

```
config.py            全局配置槽（路径/模型/权限/预算/业务基线）
server.py            FastAPI 入口与全部 HTTP 接口
logistics_agent/
  core/              toolkit / dispatch / perception / cognition / constraint / reflection / telemetry / output
  memory/            短期（会话 JSONL）与长期（偏好/事实）记忆
  tools/             knowledge / clearance / rates / shipment / catalog / document（12 个注册工具）
  Agents/            supervisor / plan / quote / clearance / compliance / summarizer
data/knowledge/      countries / hs_codes / carriers / ports
data/quotes/         报价落盘 CSV
ui/                  index.html + app.js + style.css（原生，无构建）
```

更细的模块职责、数据流与设计取舍见 `struct.md`；版本变更见 `CHANGELOG.md`。

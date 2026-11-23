# 跨境物流引擎 · Android App

手机端可离线运行的版本：比价、清关时效、HS 归类与税费、全链路方案全部在手机本地计算，
不依赖电脑、不联网、不调用大模型。也支持切回「连电脑」模式使用电脑端的完整后端。

## 产物

| 文件 | 说明 |
| --- | --- |
| `dist/跨境物流引擎.apk` | 已签名安装包（自签证书，可直接安装） |
| `icon-512.png` | 应用图标（512×512） |
| `app/assets/www/` | App 内置页面与引擎（构建时打进 APK） |
| `build.ps1` | 一条命令构建签名 APK |
| `scripts/` | 知识库内联、对数、测试、图标、环境准备脚本 |

## 安装

1. 把 `dist/跨境物流引擎.apk` 传到手机（微信文件传输 / 数据线 / 网盘均可）。
2. 点击安装，系统提示「未知来源」时允许本次安装。
3. 首次打开即进入离线模式，无需任何配置。

用 ADB 安装（手机已开 USB 调试）：

```powershell
adb install -r "dist\跨境物流引擎.apk"
```

要求：Android 5.0 及以上（建议 Android 8+，系统 WebView 需为较新版本）。

## 两种运行模式

App 内「设置」标签可切换：

- **离线模式（默认）**：加载 `assets/www`，`/api/*` 请求由内置 JS 规则引擎就地计算。
  数据全部来自 `data/knowledge/*.json`，构建时内联成 `data.js`。
- **连电脑模式**：加载 `http://<电脑IP>:8000/`，使用电脑端完整后端（含 LLM 智能体）。
  电脑需执行：
  ```powershell
  .\.venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8000
  ```
  手机与电脑连同一 WiFi，地址填 `http://192.168.x.x:8000`。电脑防火墙需放行 8000 端口。

## 离线版能力

- 比价：9 家承运商、11 个目的国，按「总成本 + 时效 + 准点率 + 风险」综合评分排序。
- 清关：目的国基础时效 + 单证缺口 + 认证缺口 + 口岸拥堵 + 旺季 + 自营清关，输出区间与风险。
- 合规：HS 归类、关税率、VAT/销售税、免税额、认证与单证清单。
- 方案：推荐承运商 + 备选 + 税费 + 单证 + 四段式时间线 + 风险与优化建议。
- 对话：主控按关键词路由到比价/清关/合规/方案/总结智能体，输出执行 trace。
- 导出：报价单存本地并写入手机「下载/跨境物流引擎」目录（CSV，UTF-8 BOM，可直接用 Excel 打开）。
- 调试：知识库规模、已导出报价、每次工具调用的参数与结果、遥测尾部。

## 构建

环境（仅用户目录，不改系统）：

```powershell
powershell -File .\scripts\setup_toolchain.ps1   # JDK17 + build-tools 34 + platform 34
```

构建：

```powershell
.\build.ps1
```

流程：`gen_data.py` 内联知识库 → `aapt2 compile` → `javac` → `d8` → `aapt2 link`
→ 写入 `classes.dex` → `zipalign` → `apksigner`。全程不依赖 Gradle 与 Android Studio。

> aapt2 在 Windows 下不认中文路径，脚本会把构建复制到 `%TEMP%\logi_apk_stage` 执行，
> 再把产物拷回 `dist/`。

签名：自签 keystore 位于 `%LOCALAPPDATA%\LogiAndroid\logistics.keystore`
（口令 `logistics2026`，别名 `logistics`）。**后续升级必须沿用同一 keystore**，
否则无法覆盖安装。正式对外分发前请替换为企业自己的证书。

## 与 Python 后端的一致性

离线引擎按 `logistics_agent/tools/{knowledge,clearance,rates,shipment}.py` 与各 Agent 的
classic 兜底逻辑逐行移植，数值口径一致，包括 Python 的「四舍六入五成双」舍入。

对数方法：

```powershell
.\.venv\Scripts\python.exe .\scripts\baseline.py   # Python 基准
node .\scripts\test_engine.js                      # JS 引擎，逐项对照
node .\scripts\test_api.js                         # /api 接口层自测
```

已验证：德国 20kg T 恤（8 家可选、税费 1664、推荐顺丰 3479.91、9.4–14.2 天、评分 83.4）
与 Python 完全一致；德国 300kg 家具拼柜方案（10562.9 元、47.5–61 天）一致。

## 边界

- 运价、税率、清关时效均为内置演示基线，不含真实承运商协议价，不能直接用于对外报价。
- 离线版不接 LLM：对话走关键词路由 + 规则化回复；需要自由问答请切「连电脑」模式。
- 离线版不支持文件上传；报价记录只存在手机本地，卸载即丢失。
- Android 端未接入定位/相机/通知等权限，仅申请网络权限（连电脑模式需要）。

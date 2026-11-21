# -*- coding: utf-8 -*-
"""死规则与工具分类常量（约束层与提示词共用）。"""

DEAD_RULES = """1. 只使用知识库里的税率、运价与时效基线；不确定就说明是估算区间，不编造承运商报价。
2. 关税、增值税以目的国官方税则为准，本引擎结果仅供报价参考，不构成报关依据。
3. 不承诺具体船期/舱位；海运与班列时效按月波动，必须给区间而非单点。
4. 危险品（锂电池、液体、粉末）必须提示承运商限制，不得当作普货报价。
5. 涉及品牌授权、认证（CE/FDA/CPSC/PSE 等）缺失时，必须提示扣关风险。
6. 不提供低报、瞒报、拆单避税等违法建议。"""

# 需要联网的工具（本项目离线运行，暂无）
NET_TOOLS: set[str] = set()

# 会写数据的工具（权限等级按 L2 处理）
WRITE_TOOLS = {"shipment.export_quote"}

# 「危险写操作」：plan 模式下必须显式 confirm=true
DANGEROUS_WRITE = {"shipment.export_quote"}

__all__ = ["DEAD_RULES", "NET_TOOLS", "WRITE_TOOLS", "DANGEROUS_WRITE"]

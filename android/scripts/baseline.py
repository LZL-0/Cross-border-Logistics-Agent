# -*- coding: utf-8 -*-
"""打印 Python 端基准数值，用于与 App 内置 JS 引擎对数。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from logistics_agent.tools.clearance import estimate_clearance  # noqa: E402
from logistics_agent.tools.rates import compare_rates  # noqa: E402
from logistics_agent.tools.shipment import plan_shipment  # noqa: E402

c = compare_rates("德国", 20, 50, 40, 30, "", 1, "6109100010", 5000)
print("count", c["count"], "tax", c["tax"]["total_tax"], "rec", c["recommendation"]["carrier"],
      c["recommendation"]["landed_total"], c["recommendation"]["eta_days"],
      c["recommendation"]["eta_date"], c["recommendation"]["score"])
for q in c["quotes"]:
    print("|".join(str(x) for x in [q["carrier"], q["total"], q["tax"], q["agency_fee"],
                                     q["landed_total"], q["eta_days"], q["score"]]))

cl = estimate_clearance("德国", "air", "6109100010", ["商业发票", "装箱单"], declared_value=5000)
print("clearance", cl["clearance_days"], cl["confidence"], cl["missing_docs"])

p = plan_shipment("德国", 300, 120, 80, 60, 30000, "9403600000", "lcl", 10)
print("plan", p["ok"], p["chosen_quote"]["carrier"], p["chosen_quote"]["landed_total"],
      p["chosen_quote"]["eta_days"])
print("timeline", " / ".join(
    "{0}:{1}-{2}@{3}".format(t["stage"], t["start"], t["end"], t["date"]) for t in p["timeline"]))
print("docs missing", p["docs"]["missing"])

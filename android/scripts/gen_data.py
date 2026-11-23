# -*- coding: utf-8 -*-
"""把 data/knowledge/*.json 内联成 App 可用的 data.js（WebView 下 file:// 不能 fetch JSON）。"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KNOW = ROOT / "data" / "knowledge"
OUT = ROOT / "android" / "app" / "assets" / "www" / "data.js"


def load(name: str, key: str) -> list:
    with open(KNOW / name, "r", encoding="utf-8") as f:
        return json.load(f).get(key, [])


def main() -> None:
    payload = {
        "countries": load("countries.json", "countries"),
        "hs": load("hs_codes.json", "items"),
        "carriers": load("carriers.json", "carriers"),
        "ports": load("ports.json", "ports"),
    }
    text = "/* 由 scripts/gen_data.py 生成，请勿手改。数据源 data/knowledge/*.json */\n"
    text += "window.KB = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    OUT.write_text(text, encoding="utf-8")
    print("data.js ->", OUT, len(text), "bytes")


if __name__ == "__main__":
    main()

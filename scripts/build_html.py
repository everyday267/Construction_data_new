#!/usr/bin/env python3
"""data/normalized.json + scripts/search_template.html → 조회기 HTML 생성.

사용법:
    python scripts/build_html.py --year 2025
    python scripts/build_html.py --year 2026 --out construction_capability_search_v5.html

사전 조건: scripts/normalize.py 실행으로 data/normalized.json이 생성되어 있어야 한다.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "scripts" / "search_template.html"
DATA = ROOT / "data" / "normalized.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", required=True, help="공시 기준연도 (예: 2025)")
    ap.add_argument("--out", default="construction_capability_search_v5.html")
    args = ap.parse_args()

    if not DATA.exists():
        raise SystemExit("오류: data/normalized.json 없음 — 먼저 scripts/normalize.py를 실행하세요")

    records = json.loads(DATA.read_text(encoding="utf-8"))
    data_js = json.dumps(records, ensure_ascii=False, separators=(",", ":"))

    html = TEMPLATE.read_text(encoding="utf-8")
    assert "__DATA__" in html and "__YEAR__" in html
    html = html.replace("__DATA__", data_js).replace("__YEAR__", str(args.year))

    out = ROOT / args.out
    out.write_text(html, encoding="utf-8")
    has_bizno = sum(1 for r in records if r.get("bizno"))
    print(f"✔ {out.name} 생성 — {len(records):,}건 내장, 사업자번호 컬럼 {'표시' if has_bizno else '숨김'} ({has_bizno:,}건)")


if __name__ == "__main__":
    main()

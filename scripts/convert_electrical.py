#!/usr/bin/env python3
"""data/keca/keca_result.csv (크롤링 산출물) → raw/electrical_2026.csv 표준 헤더 변환.

사용법:
    python scripts/convert_electrical.py

매핑 (docs/PRD.md §3.1, §4.4, §5.1):
    대분류 = "전기설비" (고정)
    중분류 = "전기공사업" (공종 1종, 고정)
    순위   = 전국순위
    시공능력평가액 = 시공능력평가액 (KECA 원문은 "원" 단위 — normalize.py가 이후 ÷1,000,000 처리하여 백만원 통일)
    공사실적평가액·경영평가액·기술능력평가액·신인도평가액·건설공사실적·기술자수 = 미제공(빈 값)
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "keca" / "keca_result.csv"
DST = ROOT / "raw" / "electrical_2026.csv"

HEADER = ["대분류", "중분류", "순위", "상호", "대표자", "소재지", "등록번호",
          "시공능력평가액", "공사실적평가액", "경영평가액", "기술능력평가액",
          "신인도평가액", "건설공사실적", "기술자수"]


def main():
    with open(SRC, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    out_rows = []
    for r in rows:
        out_rows.append(["전기설비", "전기공사업", r.get("전국순위", ""),
                          r.get("상호", ""), r.get("대표자", ""), r.get("소재지", ""),
                          r.get("등록번호", ""), r.get("시공능력평가액", ""),
                          "", "", "", "", "", ""])

    with open(DST, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(out_rows)

    print(f"완료: {len(out_rows):,}행 → {DST}")


if __name__ == "__main__":
    main()

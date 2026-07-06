#!/usr/bin/env python3
"""raw/*.csv → data/normalized.json 정규화 + 품질 검증.

사용법:
    python scripts/normalize.py
    python scripts/normalize.py --fallback-html construction_capability_search_v4.html

--fallback-html: raw CSV가 비어 있는 대분류를 기존 산출물 HTML에 내장된
데이터에서 가져온다 (raw 원본 업로드 전 임시 조치, PRD §11 O1).

규칙 상세: docs/PRD.md §5~§7
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "raw"
OUT_PATH = ROOT / "data" / "normalized.json"

# 소스별 설정: (파일명, 대분류, 금액단위 divisor, 순위 정책, 2025 기준 행수)
# divisor: 원본 단위 → 백만원. 백만원=1, 천원=1000 (PRD §6.2)
# rank_policy: "source"=원본 순위 사용, "recompute"=평가액 내림차순 재산정 (PRD §6.4)
SOURCES = [
    {"file": "general_construction.csv",   "cat": "종합건설", "divisor": 1,    "rank_policy": "source",    "baseline_rows": 19610},
    {"file": "specialty_construction.csv", "cat": "전문건설", "divisor": 1,    "rank_policy": "recompute", "baseline_rows": 76118},
    {"file": "mechanical_gas.csv",         "cat": "기계설비", "divisor": 1,    "rank_policy": "recompute", "baseline_rows": 7694},
    {"file": "fire_protection.csv",        "cat": "소방설비", "divisor": 1000, "rank_policy": "source",    "baseline_rows": 6845},
    {"file": "electrical.csv",             "cat": "전기설비", "divisor": 1000, "rank_policy": "source",    "baseline_rows": 21179},
    {"file": "ict_communication.csv",      "cat": "정보통신", "divisor": 1000, "rank_policy": "source",    "baseline_rows": 11836},
]

REQUIRED_COLS = ["대분류", "중분류", "순위", "상호", "시공능력평가액"]
BIZNO_HEADERS = ["사업자번호", "사업자등록번호"]
MIN_ROW_RATIO = 0.8  # Q2: 기준 행수 대비 80% 미만이면 실패

# Q4: 단위 변환 누락 감지용 — 공종 1위 평가액 하한 (백만원)
SANITY_TOP_AMT_MIN = {
    "종합건설": 10_000_000,  # 1위 > 10조
    "전기설비": 1_000_000,   # 1위 > 1조
    "정보통신": 100_000,
    "소방설비": 100_000,
    "기계설비": 100_000,
    "전문건설": 100_000,
}


def parse_number(s):
    """콤마 포함 문자열 → float. 실패 시 None."""
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def clean_reg(s):
    """등록번호: 문자열 보존, float 변환 흔적('668.0')만 제거."""
    s = (s or "").strip()
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s


def clean_bizno(s):
    """사업자번호: 하이픈·공백 제거. float 흔적 제거."""
    s = re.sub(r"[-\s]", "", (s or "").strip())
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s


def load_source(src, errors, warnings):
    path = RAW_DIR / src["file"]
    cat = src["cat"]

    # Q1: 파일 존재·비어있지 않음
    if not path.exists() or path.stat().st_size < 100:
        errors.append(f"[Q1] {src['file']}: 파일이 없거나 비어 있음 ({path.stat().st_size if path.exists() else 0} bytes)")
        return None

    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    # Q2: 최소 행수
    if len(rows) < src["baseline_rows"] * MIN_ROW_RATIO:
        errors.append(f"[Q2] {src['file']}: 행수 {len(rows)} < 기준 {src['baseline_rows']}의 {MIN_ROW_RATIO:.0%}")
        return None

    bizno_col = next((h for h in BIZNO_HEADERS if rows and h in rows[0]), None)

    records, missing_required, bad_amt = [], 0, 0
    for row in rows:
        if any(not (row.get(c) or "").strip() for c in REQUIRED_COLS):
            missing_required += 1
        amt_raw = parse_number(row.get("시공능력평가액"))
        if amt_raw is None:
            bad_amt += 1
        rank = parse_number(row.get("순위"))
        rec = {
            "cat": (row.get("대분류") or "").strip() or cat,
            "sub": (row.get("중분류") or "").strip(),
            "rank": int(rank) if rank is not None else None,
            "name": (row.get("상호") or "").strip(),
            "rep": (row.get("대표자") or "").strip(),
            "addr": (row.get("소재지") or "").strip(),
            "reg": clean_reg(row.get("등록번호")),
            # 백만원 정수로 통일 — 십만원 단위에서 반올림 (PRD §6.2)
            "amt": round(amt_raw / src["divisor"]) if amt_raw is not None else None,
        }
        if bizno_col:
            b = clean_bizno(row.get(bizno_col))
            if b:
                rec["bizno"] = b
        records.append(rec)

    # Q3: 필수 컬럼 결측률
    if missing_required / max(len(rows), 1) > 0.01:
        errors.append(f"[Q3] {src['file']}: 필수 컬럼 결측 {missing_required}/{len(rows)}행 (>1%)")
    if bad_amt:
        warnings.append(f"{src['file']}: 시공능력평가액 파싱 실패 {bad_amt}행")

    # 순위 정책 (PRD §6.4)
    if src["rank_policy"] == "recompute":
        by_sub = {}
        for r in records:
            by_sub.setdefault(r["sub"], []).append(r)
        for grp in by_sub.values():
            grp.sort(key=lambda r: -(r["amt"] if r["amt"] is not None else -1))
            for i, r in enumerate(grp, 1):
                r["rank"] = i
        warnings.append(f"{src['file']}: 순위를 평가액 내림차순으로 재산정 (rank_policy=recompute)")

    # Q4: 단위 변환 누락 감지
    amts = [r["amt"] for r in records if r["amt"] is not None]
    if amts and max(amts) < SANITY_TOP_AMT_MIN.get(cat, 0):
        errors.append(f"[Q4] {src['file']}: 최대 평가액 {max(amts):,.0f}백만원 < 하한 {SANITY_TOP_AMT_MIN[cat]:,} — 단위 변환 확인 필요")

    # Q5: 공종 내 최대 amt 행의 rank == 1
    by_sub = {}
    for r in records:
        if r["amt"] is not None:
            by_sub.setdefault(r["sub"], []).append(r)
    for sub, grp in by_sub.items():
        top = max(grp, key=lambda r: r["amt"])
        if top["rank"] != 1:
            warnings.append(f"[Q5] {src['file']} / {sub}: 평가액 1위 '{top['name']}'의 순위가 {top['rank']} (≠1)")

    # Q6: 사업자번호 형식
    if bizno_col:
        biznos = [r["bizno"] for r in records if r.get("bizno")]
        bad = [b for b in biznos if not re.fullmatch(r"\d{10}", b)]
        if biznos and len(bad) / len(biznos) > 0.01:
            warnings.append(f"[Q6] {src['file']}: 사업자번호 10자리 아님 {len(bad)}/{len(biznos)}건 (예: {bad[0]})")

    return records


def extract_from_html(html_path, cats_needed, warnings):
    """기존 산출물 HTML의 내장 RAW 배열에서 지정 대분류 레코드 추출 (이미 백만원 단위)."""
    text = Path(html_path).read_text(encoding="utf-8")
    m = re.search(r"const RAW = (\[.*?\]);\n", text, re.DOTALL)
    if not m:
        raise SystemExit(f"오류: {html_path}에서 내장 RAW 배열을 찾지 못했습니다")
    all_recs = json.loads(m.group(1))
    out = [r for r in all_recs if r.get("cat") in cats_needed]
    for r in out:
        if r.get("amt") is not None:
            r["amt"] = round(r["amt"])  # 백만원 정수 통일 (PRD §6.2)
    warnings.append(f"fallback: {html_path}에서 {sorted(cats_needed)} {len(out):,}건 추출 (raw CSV 업로드 전 임시 조치, PRD §11 O1)")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fallback-html", help="raw CSV가 빈 대분류를 이 HTML의 내장 데이터로 대체")
    args = ap.parse_args()

    errors, warnings, records = [], [], []
    failed_cats = []
    for src in SOURCES:
        recs = load_source(src, errors, warnings)
        if recs is None:
            failed_cats.append(src["cat"])
        else:
            records.extend(recs)
            print(f"  {src['cat']:<5} {src['file']:<28} {len(recs):>7,}건")

    if failed_cats and args.fallback_html:
        # fallback 사용 시 해당 대분류의 Q1/Q2 오류는 경고로 강등
        errors = [e for e in errors if not any(s["file"] in e for s in SOURCES if s["cat"] in failed_cats)]
        fb = extract_from_html(args.fallback_html, set(failed_cats), warnings)
        records.extend(fb)

    for w in warnings:
        print(f"⚠ {w}")
    if errors:
        for e in errors:
            print(f"✖ {e}", file=sys.stderr)
        sys.exit(1)

    OUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, separators=(",", ":"))
    has_bizno = sum(1 for r in records if r.get("bizno"))
    print(f"\n✔ {OUT_PATH.relative_to(ROOT)} 생성 — 총 {len(records):,}건, 사업자번호 보유 {has_bizno:,}건")


if __name__ == "__main__":
    main()

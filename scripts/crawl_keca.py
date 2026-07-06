#!/usr/bin/env python3
"""KECA(한국전기공사협회) 전기공사업 시공능력평가 크롤러.

2025년 수집 시 검증된 방식(docs/PRD.md §4)의 참조 구현.
매년 실행 전 PRD §4.6 재크롤링 체크리스트를 먼저 확인할 것.

사용법:
    pip install requests beautifulsoup4
    python scripts/crawl_keca.py --start-page 1 --end-page 3          # 소량 테스트
    python scripts/crawl_keca.py --start-page 1 --end-page 2118      # 전체
    python scripts/crawl_keca.py --retry-failed                       # 실패 ID 재수집

출력 (data/keca/):
    keca_list_XXXXp.csv       중간 저장 (SAVE_INTERVAL 페이지마다)
    keca_result.csv           최종 결과
    keca_failed_ids.txt       상세 조회 실패 license ID (재수집용)
"""
import argparse
import csv
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://m.keca.or.kr"
LIST_ENTRY_URL = f"{BASE}/service/service07.do?menuCd=4051"  # GET: 세션+CSRFToken 확보
LIST_ACTION = f"{BASE}/service/service07.do"                  # POST: 목록 (gubun=page)
DETAIL_ACTION = f"{BASE}/service/service07D.do"               # POST: 상세 (gubun=detail)
MENU_CD = "4051"

SAVE_INTERVAL = 100        # N페이지마다 중간 저장
REQUEST_DELAY = 0.4        # 요청 간 대기(초) — 서버 부하 방지
TIMEOUT = 20
UA = "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36"

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "keca"

FIELDS = ["license_id", "등록번호", "등록일", "관할시도회", "상호", "대표자", "소재지",
          "시공능력평가액_원문", "시공능력평가액", "지역순위", "전국순위"]


def get_session_and_token():
    """1단계: 목록 첫 페이지 GET으로 세션·CSRFToken 확보."""
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.get(LIST_ENTRY_URL, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r'name="CSRFToken"\s+value="([^"]+)"', r.text)
    if not m:
        raise SystemExit("CSRFToken을 찾지 못함 — 페이지 구조 변경 여부 확인 (PRD §4.6)")
    return s, m.group(1)


def form_params(token, page=1, license_id="", gubun="page"):
    """목록/상세 공통 폼 파라미터 (PRD §4.3)."""
    return {
        "menuCd": MENU_CD, "currentPageNo": str(page), "license": license_id,
        "gubun": gubun, "searchSido": "", "searchSigungu": "",
        "searchGubun": "company", "searchText": "", "CSRFToken": token,
    }


def fetch_list_page(session, token, page):
    """2단계: 목록 POST → [(license_id, 상호, 대표자, 주소)]."""
    r = session.post(LIST_ACTION, data=form_params(token, page=page, gubun="page"), timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for li in soup.select("li"):
        onclick = li.find(attrs={"onclick": re.compile(r"js_detailAction")})
        if not onclick:
            continue
        m = re.search(r"js_detailAction\('(\d+)'\)", onclick.get("onclick", "") or str(li))
        if not m:
            continue
        name_el = li.select_one(".name")
        comp_el = li.select_one(".company")
        items.append({
            "license_id": m.group(1),
            "목록_상호": name_el.get_text(strip=True) if name_el else "",
            "목록_기타": comp_el.get_text(" ", strip=True) if comp_el else "",
        })
    return items


def num_only(s):
    """순위·평가액처럼 문구가 섞인 값에서 숫자만 추출."""
    m = re.search(r"[\d,]+", s or "")
    return m.group(0).replace(",", "") if m else ""


def fetch_detail(session, token, license_id):
    """3단계: 상세 POST → 등록번호·평가액·순위 등."""
    r = session.post(DETAIL_ACTION, data=form_params(token, license_id=license_id, gubun="detail"), timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    rec = {"license_id": license_id}
    label_map = {"등록번호": "등록번호", "등록일": "등록일", "관할시도회": "관할시도회",
                 "상호": "상호", "대표자": "대표자", "소재지": "소재지",
                 "시공능력평가액": "시공능력평가액_원문", "지역순위": "지역순위", "전국순위": "전국순위"}
    for th in soup.select("th"):
        label = th.get_text(strip=True).replace(" ", "")
        td = th.find_next("td")
        if not td:
            continue
        for key, field in label_map.items():
            if key in label:
                rec[field] = td.get_text(" ", strip=True)
    # 원본 문자열과 숫자 정제값을 둘 다 보존 (PRD §4.4)
    rec["시공능력평가액"] = num_only(rec.get("시공능력평가액_원문"))
    rec["지역순위"] = num_only(rec.get("지역순위"))
    rec["전국순위"] = num_only(rec.get("전국순위"))
    return rec


def save_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start-page", type=int, default=1)
    ap.add_argument("--end-page", type=int, default=3, help="첫 실행은 3으로 소량 테스트 권장")
    ap.add_argument("--retry-failed", action="store_true", help="keca_failed_ids.txt의 ID만 재수집")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failed_path = OUT_DIR / "keca_failed_ids.txt"
    session, token = get_session_and_token()
    print(f"세션 확보, CSRFToken: {token[:12]}...")

    results, failed = [], []

    if args.retry_failed:
        ids = [l.strip() for l in failed_path.read_text().splitlines() if l.strip()]
        print(f"실패 ID {len(ids)}건 재수집")
        for lid in ids:
            try:
                results.append(fetch_detail(session, token, lid))
            except Exception as e:
                print(f"  실패 {lid}: {e}", file=sys.stderr)
                failed.append(lid)
            time.sleep(REQUEST_DELAY)
        save_csv(OUT_DIR / "keca_retry_result.csv", results)
    else:
        for page in range(args.start_page, args.end_page + 1):
            try:
                items = fetch_list_page(session, token, page)
            except Exception as e:
                print(f"목록 p{page} 실패: {e}", file=sys.stderr)
                time.sleep(3)
                continue
            for it in items:
                try:
                    results.append(fetch_detail(session, token, it["license_id"]))
                except Exception as e:
                    print(f"  상세 실패 {it['license_id']}: {e}", file=sys.stderr)
                    failed.append(it["license_id"])
                time.sleep(REQUEST_DELAY)
            if page % SAVE_INTERVAL == 0:
                save_csv(OUT_DIR / f"keca_list_{page:04d}p.csv", results)
                print(f"p{page} 중간 저장 — 누적 {len(results):,}건, 실패 {len(failed)}건")
            time.sleep(REQUEST_DELAY)
        save_csv(OUT_DIR / "keca_result.csv", results)

    if failed:
        failed_path.write_text("\n".join(failed), encoding="utf-8")
    print(f"완료: {len(results):,}건 수집, 실패 {len(failed)}건 → {OUT_DIR}")
    print("다음 단계: 결과를 raw/electrical.csv 표준 헤더(PRD §5.1)로 변환. "
          "표준 '순위'에는 전국순위, '시공능력평가액'은 천원 단위 값을 사용.")


if __name__ == "__main__":
    main()

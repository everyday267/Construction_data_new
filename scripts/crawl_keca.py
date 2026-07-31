#!/usr/bin/env python3
"""KECA(한국전기공사협회) 전기공사업 시공능력평가 크롤러.

방법 명세: docs/PRD.md §4. 매년 실행 전 §4.6 체크리스트를 --probe로 자동 확인할 것.

사용법:
    pip install requests beautifulsoup4

    # 1) 구조 점검 (필수, 1분 이내) — 사이트 구조가 작년과 같은지 확인
    python scripts/crawl_keca.py --probe

    # 2) 소량 테스트
    python scripts/crawl_keca.py --end-page 3

    # 3) 전체 수집 (약 2~3시간). 중단되면 같은 명령을 다시 실행하면 이어받는다.
    python scripts/crawl_keca.py --all

    # 4) 표준 raw CSV 생성 (수집 완료 후)
    python scripts/crawl_keca.py --to-raw

출력 (data/keca/):
    keca_detail.csv        수집 결과 (재실행 시 이어받기 기준)
    keca_failed_ids.txt    상세 조회 실패 license ID
→ raw/electrical.csv      --to-raw로 생성하는 표준 헤더 CSV (PRD §5.1)
"""
import argparse
import csv
import re
import sys
import time
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install requests beautifulsoup4 를 먼저 실행하세요")

BASE = "https://m.keca.or.kr"
LIST_ENTRY_URL = f"{BASE}/service/service07.do?menuCd=4051"  # GET: 세션+CSRFToken 확보
LIST_ACTION = f"{BASE}/service/service07.do"                  # POST: 목록 (gubun=page)
DETAIL_ACTION = f"{BASE}/service/service07D.do"               # POST: 상세 (gubun=detail)
MENU_CD = "4051"

PER_PAGE = 10              # 목록 페이지당 업체 수
REQUEST_DELAY = 0.4        # 요청 간 대기(초)
SAVE_INTERVAL = 20         # N페이지마다 저장
SESSION_REFRESH_PAGES = 200  # N페이지마다 세션·CSRFToken 갱신 (장시간 실행 대비)
MAX_RETRY = 3
TIMEOUT = 20
UA = "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36"

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "keca"
DETAIL_CSV = OUT_DIR / "keca_detail.csv"
FAILED_TXT = OUT_DIR / "keca_failed_ids.txt"
RAW_CSV = ROOT / "raw" / "electrical.csv"

FIELDS = ["license_id", "등록번호", "등록일", "관할시도회", "상호", "대표자", "소재지",
          "시공능력평가액_원문", "시공능력평가액", "지역순위", "전국순위"]

RAW_HEADER = ["대분류", "중분류", "순위", "상호", "대표자", "소재지", "등록번호",
              "시공능력평가액", "공사실적평가액", "경영평가액", "기술능력평가액",
              "신인도평가액", "건설공사실적", "기술자수"]


# ── 세션 / 요청 ────────────────────────────────────────────────────────────

def make_session():
    """목록 첫 페이지 GET으로 세션·CSRFToken 확보 (PRD §4.5 1단계)."""
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.get(LIST_ENTRY_URL, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r'name="CSRFToken"[^>]*value="([^"]*)"', r.text)
    if not m:
        raise SystemExit("CSRFToken을 찾지 못함 — 페이지 구조 변경 확인 필요 (PRD §4.6)")
    return s, m.group(1), r.text


def form_params(token, page=1, license_id="", gubun="page"):
    """목록/상세 공통 폼 파라미터 (PRD §4.3)."""
    return {
        "menuCd": MENU_CD, "currentPageNo": str(page), "license": license_id,
        "gubun": gubun, "searchSido": "", "searchSigungu": "",
        "searchGubun": "company", "searchText": "", "CSRFToken": token,
    }


def post_retry(session, url, data):
    """POST + 지수 백오프 재시도. 실패 시 마지막 예외를 올린다."""
    last = None
    for attempt in range(MAX_RETRY):
        try:
            r = session.post(url, data=data, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:                      # noqa: BLE001 — 네트워크 예외 전반
            last = e
            time.sleep(2 ** attempt)
    raise last


# ── 파싱 ──────────────────────────────────────────────────────────────────

def parse_license_ids(html):
    """목록 HTML에서 상세 진입 키(내부 license ID) 추출.
    화면의 등록번호가 아니라 js_detailAction('...') 인자가 실제 키다 (PRD §4.2).
    마크업이 바뀌어도 견디도록 정규식으로 직접 훑는다."""
    ids, seen = [], set()
    for m in re.finditer(r"js_detailAction\(\s*['\"](\w+)['\"]\s*\)", html):
        lid = m.group(1)
        if lid not in seen:
            seen.add(lid)
            ids.append(lid)
    return ids


def num_only(s):
    """순위·평가액처럼 문구·공백이 섞인 값에서 숫자만 추출 (PRD §4.4)."""
    m = re.search(r"[\d,]+", s or "")
    return m.group(0).replace(",", "") if m else ""


DETAIL_LABELS = {
    "등록번호": "등록번호", "등록일": "등록일", "관할시도회": "관할시도회",
    "상호": "상호", "대표자": "대표자", "소재지": "소재지",
    "시공능력평가액": "시공능력평가액_원문", "지역순위": "지역순위", "전국순위": "전국순위",
}


def parse_detail(html, license_id):
    """상세 HTML → 레코드. th 라벨(공백·줄바꿈 제거 후 비교)의 다음 td를 값으로 본다."""
    soup = BeautifulSoup(html, "html.parser")
    rec = {"license_id": license_id}
    for th in soup.find_all("th"):
        label = re.sub(r"\s+", "", th.get_text())
        td = th.find_next("td")
        if not td:
            continue
        for key, field in DETAIL_LABELS.items():
            if key in label and field not in rec:
                # 주소 등에 줄바꿈·연속 공백이 섞여 들어온다 (PRD §4.5)
                rec[field] = re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip()
    # 원본 문자열과 숫자 정제값을 둘 다 보존
    rec["시공능력평가액"] = num_only(rec.get("시공능력평가액_원문"))
    rec["지역순위"] = num_only(rec.get("지역순위"))
    rec["전국순위"] = num_only(rec.get("전국순위"))
    return rec


# ── 저장 / 이어받기 ────────────────────────────────────────────────────────

def load_done():
    """기존 수집분 로드 → (레코드 리스트, 수집된 license_id 집합)."""
    if not DETAIL_CSV.exists():
        return [], set()
    with open(DETAIL_CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows, {r["license_id"] for r in rows}


def save_details(rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(DETAIL_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ── 명령 ──────────────────────────────────────────────────────────────────

def cmd_probe():
    """PRD §4.6 재크롤링 체크리스트 자동 점검. 전체 수집 전 반드시 실행."""
    print("KECA 구조 점검 (PRD §4.6)\n")
    session, token, entry_html = make_session()
    print(f"  ✔ 세션·CSRFToken 확보: {token[:16]}...")

    for name in ("menuCd", "currentPageNo", "license", "gubun", "CSRFToken"):
        ok = re.search(rf'name="{name}"', entry_html) is not None
        print(f"  {'✔' if ok else '✖'} hidden 필드 '{name}'")

    for fn in ("js_linkPage", "js_detailAction", "js_searchAction"):
        ok = fn in entry_html
        print(f"  {'✔' if ok else '✖'} 자바스크립트 '{fn}'")

    pages = [int(m.group(1)) for m in re.finditer(r"js_linkPage\(\s*(\d+)\s*\)", entry_html)]
    last_page = max(pages) if pages else 0
    counts = [int(m.group(1).replace(",", "")) for m in re.finditer(r"([\d,]{4,})\s*(?:건|개)", entry_html)]
    print(f"\n  마지막 페이지: {last_page or '찾지 못함'}  (2025년: 2118)")
    print(f"  총 건수 후보: {counts or '찾지 못함'}  (2025년: 21179)")
    print(f"  추정 총 건수: {last_page * PER_PAGE:,}")

    html = post_retry(session, LIST_ACTION, form_params(token, page=1, gubun="page"))
    ids = parse_license_ids(html)
    print(f"\n  ✔ 목록 p1에서 license ID {len(ids)}개 추출: {ids[:3]}")
    if not ids:
        sys.exit("✖ license ID를 추출하지 못했습니다 — 목록 파싱 로직 점검 필요")

    time.sleep(REQUEST_DELAY)
    detail_html = post_retry(session, DETAIL_ACTION, form_params(token, license_id=ids[0], gubun="detail"))
    rec = parse_detail(detail_html, ids[0])
    print(f"  ✔ 상세 조회 성공 ({ids[0]})")
    for field in ("상호", "등록번호", "소재지", "시공능력평가액_원문", "시공능력평가액", "전국순위", "지역순위"):
        val = rec.get(field, "")
        print(f"      {field:<18} {val if val else '⚠ 비어 있음'}")

    missing = [f for f in ("상호", "시공능력평가액", "전국순위") if not rec.get(f)]
    if missing:
        sys.exit(f"\n✖ 필수 항목 누락: {missing} — 상세 페이지 라벨 변경 여부 확인 (PRD §4.6)")
    print(f"\n✔ 점검 통과. 전체 수집 예상 시간: 약 {(last_page + last_page * PER_PAGE) * REQUEST_DELAY / 3600:.1f}시간")
    print("  다음: python scripts/crawl_keca.py --end-page 3   (소량 테스트)")


def cmd_crawl(start_page, end_page, crawl_all):
    session, token, entry_html = make_session()
    if crawl_all:
        pages = [int(m.group(1)) for m in re.finditer(r"js_linkPage\(\s*(\d+)\s*\)", entry_html)]
        end_page = max(pages) if pages else end_page
        print(f"전체 수집 모드: 1~{end_page}페이지")

    rows, done = load_done()
    if done:
        print(f"이어받기: 기존 {len(done):,}건 수집됨")
    failed = []

    for page in range(start_page, end_page + 1):
        if page % SESSION_REFRESH_PAGES == 0:       # 장시간 실행 시 토큰 만료 방지
            session, token, _ = make_session()
        try:
            html = post_retry(session, LIST_ACTION, form_params(token, page=page, gubun="page"))
        except Exception as e:                      # noqa: BLE001
            print(f"  ✖ 목록 p{page} 실패: {e}", file=sys.stderr)
            continue

        for lid in parse_license_ids(html):
            if lid in done:
                continue
            try:
                dhtml = post_retry(session, DETAIL_ACTION, form_params(token, license_id=lid, gubun="detail"))
                rows.append(parse_detail(dhtml, lid))
                done.add(lid)
            except Exception as e:                  # noqa: BLE001
                print(f"    ✖ 상세 {lid} 실패: {e}", file=sys.stderr)
                failed.append(lid)
            time.sleep(REQUEST_DELAY)

        if page % SAVE_INTERVAL == 0:
            save_details(rows)
            print(f"  p{page}/{end_page} 저장 — 누적 {len(rows):,}건, 실패 {len(failed)}건")
        time.sleep(REQUEST_DELAY)

    save_details(rows)
    if failed:
        FAILED_TXT.write_text("\n".join(failed), encoding="utf-8")
    print(f"\n✔ 수집 완료: {len(rows):,}건 (실패 {len(failed)}건) → {DETAIL_CSV.relative_to(ROOT)}")
    print("  다음: python scripts/crawl_keca.py --to-raw")


def cmd_retry_failed():
    if not FAILED_TXT.exists():
        sys.exit("실패 ID 파일이 없습니다")
    ids = [l.strip() for l in FAILED_TXT.read_text(encoding="utf-8").splitlines() if l.strip()]
    session, token, _ = make_session()
    rows, done = load_done()
    still = []
    print(f"실패 ID {len(ids)}건 재수집")
    for lid in ids:
        if lid in done:
            continue
        try:
            html = post_retry(session, DETAIL_ACTION, form_params(token, license_id=lid, gubun="detail"))
            rows.append(parse_detail(html, lid))
        except Exception as e:                      # noqa: BLE001
            print(f"  ✖ {lid}: {e}", file=sys.stderr)
            still.append(lid)
        time.sleep(REQUEST_DELAY)
    save_details(rows)
    FAILED_TXT.write_text("\n".join(still), encoding="utf-8")
    print(f"✔ 재수집 후 총 {len(rows):,}건, 잔여 실패 {len(still)}건")


def cmd_to_raw():
    """수집 결과 → raw/electrical.csv 표준 헤더 (PRD §5.1).
    표준 '순위'에는 전국순위, 금액은 천원 단위 원본값을 넣는다 (백만원 변환은 normalize.py 담당)."""
    rows, _ = load_done()
    if not rows:
        sys.exit(f"{DETAIL_CSV} 가 비어 있습니다 — 먼저 크롤링을 실행하세요")
    out, skipped = [], 0
    for r in rows:
        if not r.get("상호") or not r.get("시공능력평가액"):
            skipped += 1
            continue
        out.append(["전기설비", "전기공사업", r.get("전국순위", ""), r["상호"], r.get("대표자", ""),
                    r.get("소재지", ""), r.get("등록번호", ""), r["시공능력평가액"],
                    "", "", "", "", "", ""])
    with open(RAW_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(RAW_HEADER)
        w.writerows(out)
    print(f"✔ raw/electrical.csv: {len(out):,}행" + (f" (상호/평가액 누락 {skipped}행 제외)" if skipped else ""))
    print("  다음: python scripts/normalize.py")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true", help="사이트 구조 점검 (PRD §4.6, 전체 수집 전 필수)")
    ap.add_argument("--all", action="store_true", help="마지막 페이지까지 전체 수집")
    ap.add_argument("--start-page", type=int, default=1)
    ap.add_argument("--end-page", type=int, default=3, help="첫 실행은 3으로 소량 테스트 권장")
    ap.add_argument("--retry-failed", action="store_true", help="실패한 license ID만 재수집")
    ap.add_argument("--to-raw", action="store_true", help="수집 결과 → raw/electrical.csv 변환")
    args = ap.parse_args()

    if args.probe:
        cmd_probe()
    elif args.to_raw:
        cmd_to_raw()
    elif args.retry_failed:
        cmd_retry_failed()
    else:
        cmd_crawl(args.start_page, args.end_page, args.all)


if __name__ == "__main__":
    main()

# Construction_data_new — 건설업 시공능력평가 통합 조회

6개 대분류(종합건설·전문건설·기계설비·소방설비·전기설비·정보통신)의 시공능력평가 데이터를 하나의 표준 스키마로 정규화하고, 약 14.3만 건을 내장한 **단일 HTML 조회기**를 제공합니다.

📄 **상세 명세: [docs/PRD.md](docs/PRD.md)** — 데이터 소스별 획득 방법(협회 엑셀 / KECA 크롤링), 정규화 규칙, 품질 검증, 연간 갱신 절차

## 산출물

`(<연도>) 건설업시공능력평가 조회_v5.html` — 브라우저로 열면 바로 동작 (서버·설치 불필요, 오프라인 가능)

- 업체명 최대 10개 동시 검색 (부분일치), 공종 최대 3개 필터
- 예정가격 입력 시 초과/미달 판정, 컬럼 정렬, 행 제외, CSV 내보내기
- 데이터에 사업자번호가 있으면 컬럼 자동 표시 (없으면 숨김)

## 데이터 흐름

```
협회 원본 5종(RAW_new/, xlsx·csv 혼합) + KECA 크롤링(scripts/crawl_keca.py)
        → scripts/convert_raw.py  (시트·헤더·인코딩 처리 → raw/*.csv 표준 14컬럼)
        → scripts/normalize.py    (단위 통일·순위 재산정·품질 검증 → data/normalized.json)
        → scripts/build_html.py   (→ "(<연도>) 건설업시공능력평가 조회_v5.html")
```

## 빌드 방법

```bash
pip install openpyxl
python scripts/convert_raw.py                # RAW_new/ 원본 → raw/*.csv
python scripts/normalize.py                  # raw/*.csv → data/normalized.json (+검증)
python scripts/build_html.py --year 2026     # → "(2026) 건설업시공능력평가 조회_v5.html"
```

## 업데이트 주기

**매년 8월 2일** (시공능력평가 연간 공시 직후). GitHub Actions가 해당일에 갱신 체크리스트 이슈를 자동 생성합니다 (`.github/workflows/annual-update-reminder.yml`). 절차는 PRD §9 참고.

## 폴더 구조

| 경로 | 내용 |
|---|---|
| `docs/PRD.md` | 제품 요구사항 문서 (소스 명세·크롤링 방법·정규화 규칙) |
| `RAW_new/` | 협회 제공 원본 파일 (xlsx·csv) |
| `raw/` | 소스별 표준 raw CSV 6개 |
| `scripts/` | 크롤러·정규화·빌드 스크립트, HTML 템플릿 |
| `data/` | 파이프라인 중간 산출물 (git 미추적) |
| `dataset-map.json` | 데이터셋 메타 정보 |
| `(<연도>) 건설업시공능력평가 조회_v5.html` | 최종 산출물 |
| `not_in_use/` | 구버전 보관 |

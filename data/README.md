# 데이터 설명

## 출처·수집 방법
- **합성 데이터**입니다. `python src/generate_data.py` 로 생성합니다(시드 `20261018` 고정 → 같은 파일).
- 실제 고객사 출결 데이터는 개인정보라 쓰지 않았습니다(디딤돌파트너스 원칙).
- 출퇴근 기록 형식은 이음24 전자 출결(`core/attend.py`)과 같습니다. 실제 데이터를 같은 형식으로 내보내면 코드를 그대로 쓸 수 있습니다.

## 파일
| 파일 | 행 수 | 설명 |
|---|---|---|
| attendance_logs.csv | 5,620 | `emp_id, d, in, out, edited, edit_reason`. 시각 ISO 8601(+09:00). `out` 공란 = 퇴근 미기록, `edited=1` = 사업주 사후입력 |
| work_plan.csv | 1,715 | 선택근무 근무계획표 `emp_id, date, plan(장일/단축일/통상), plan_min` — 달마다 합계 = 근무일 × 8시간 |
| leave.csv | 188 | 승인 연차 대장 `emp_id, date, type, hours` — 연차일은 그날 계획 시간만큼 인정, 기록·연차 모두 없는 근무일은 출결 누락 |
| roster.csv | 24 | **회사 근로자 명부(예시)** — 관리구분(지원대상/일반), 지원시작월, 주소정근로시간, 월보수, 고용보험, 사업주가족, 국적_체류, 고용형태, 육아기자녀, 비고 |
| employees.csv | 24 | `emp_id, alias, dept, work_type(선택근무/고정근무), flex_from, hire_date, exit_date` |
| holidays_kr.csv | 16 | 2025-07 ~ 2026-06 공휴일·대체공휴일·근로자의 날 (직접 정리 — 변경 시 수정) |
| processed/daily_panel.csv | 5,798 | 직원×근무일: 계획(장일·단축일·통상), 상태(근무·연차·누락·휴일), 누락 종류, 출퇴근 분, 실근로, 계획 대비 과부족, 단축일 4h 미달, 지각, 장려금 단축(증빙) |
| processed/weekly.csv | | 직원×주: 계획 합계·실적·과부족(누락일 제외)·부족 여부·52시간 |
| processed/monthly.csv | 284 | 직원×월(정산기간): 소정·실적·과부족, 누락(종류별), 단축 합계·요일별, 판정(level·pay) |
| processed/short_preference.csv · short_share_cumulative.csv | | 단축 요일 선호(주 선호·비중·8주 이동창), 월별 누적 비중 |
| processed/eligibility_*.csv · roster_check.csv | | 장려금 월 판정·회차별 금액·명부 점검 |

## 라이선스·주의
직접 생성한 데이터로 제3자 권리가 없습니다. 인물명은 모두 `(가상)` 가명입니다. 수치는 실제 기업 행동의 증거가 아니라 분석 방법 검증용입니다.

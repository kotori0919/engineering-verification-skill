# cam-housing 검증 실행 보고

작성: 2026-10-21 16:20 UTC (KST 2026-10-22 01:20), 실행·보고 모드. 이 보고서의 모든 숫자는 저장소 안의 파일을 가리키고, 아래 "직접 확인" 절의 명령으로 사용자의 터미널에서 다시 만들 수 있다. 원격 서버, GPU, 상용 GUI에서 나온 결과는 쓰지 않았다(이 저장소의 검사는 전부 로컬 Python이다).

## 1. 요약

조립 가능성 쪽 검사 하나가 FAIL이고, 물리적 타당성 쪽 검사 하나는 PASS이며, 의도 동작을 보는 검사는 저장소에 없다.

- 조립 가능성: 캠 로브–하우징 최소 여유는 -0.15 mm `ev:results/clearance.json#/metrics/min_clearance_mm/value` 이고, 하한 0.0 mm `ev:results/clearance.json#/metrics/min_clearance_mm/limit` 미만이라 증거 status는 FAIL `ev:results/clearance.json#/status` 이다.
- 이 값의 뜻: 공칭 형상, 강체, 전 회전 범위를 하나의 폐형식(gap − 로브 돌출)으로 본 값이며, 공차 누적과 열팽창은 보지 않았다(증거의 `scope`·`assumptions`·`limitations` 필드).
- 물리적 타당성: 하우징 벽 안전율은 2.125 `ev:results/wall.json#/metrics/safety_factor/value` 이고, 목표 1.5 `ev:results/wall.json#/metrics/safety_factor/target` 이상이라 status는 PASS `ev:results/wall.json#/status` 이다.
- 이 값의 뜻: 정적 닫힘 하중, 상온, mock 재료 모델 기준이고 피로·크리프·출력 방향은 보지 않았다. 또 L2(조립)가 FAIL인 형상의 값이라, gap을 고치면 이 값도 바뀐다. 그래서 확정 판정으로 쓰지 않는다(아래 4절).
- 의도 동작: 캠 회전·종동 기구를 보는 검사가 저장소에 없다. 근거 없음.

```
기준 커밋: cam-housing a0b60052acd5 (master, 2026-10-22 01:18 +0900, "fixture: cam")
판정 집계(증거 status 기준): PASS 1 (L2 FAIL 형상 기준, 잠정), PASS_BELOW_TARGET 0, PASS_BY_MODEL_CHANGE 0, FAIL 1, BLOCKED 0, NOT_RUN 0
저장소에 검사가 없는 질문: 의도 동작 전부, 조립의 분해 경로·공차 누적, 캠 자체의 강도
미결 공칭–실측 불일치: 대장 없음. 형상에서 다시 재지 않은 공칭 입력 2건(로브 돌출, 벽 허용치 나눗수)이 판정 2건에 쓰임
```

제안:

1. **로브 돌출 공칭값부터 확인**: FAIL 전체가 검사기 안에 하드코딩된 돌출 0.45 mm `ev:tools/check_clearance.py#L4` 에 달려 있다. 출처로 적힌 "cam drawing"이 저장소에 없어 이 값을 형상에서 확인할 수 없다.
2. **gap 수정은 사용자 결정**: gap을 넓히면 조립 여유는 오르고 벽 안전율은 내려간다(두 질문의 여유 맞바꿈). 후보와 근거는 4절에 있다. 이번 작업에서는 설계를 고치지 않았다.
3. **검사기 신뢰 보강**: 두 검사기 모두 known-answer 픽스처가 없고, 증거에 검사기 해시와 커밋이 기록되지 않는다(5절).

범위: 이 저장소에는 제외 대상 하위 시스템이 없다.

## 1a. 범위표와 완료 정의

| 하위 시스템 | 주요 경로 | 범위 | 다루는 방식 |
| --- | --- | --- | --- |
| 캠 로브–하우징 여유 | `tools/check_clearance.py`, `results/clearance.json` | 포함 | 전체 |
| 하우징 벽 강도 | `tools/check_wall.py`, `results/wall.json` | 포함 | 전체 |
| 공통 파라미터 | `params/cam.json` | 포함 | 입력으로만 읽음(수정 안 함) |

```
완료 정의(제안): 포함 하위 시스템의 타당성·조립·동작 판정이 모두 PASS(또는 사용자가 승인한 PASS_BELOW_TARGET)
완료 정의 밖: 없음(릴리스·원격 게이트가 저장소에 없음)
실행 전 확인: 실행 목록 중 제외 경로에 닿는 명령 0건
```

기준값(하한·목표)은 README가 고객 사양이라고 적은 값이다 `ev:README.md#L5` `ev:README.md#L6`. 벽 두께 하한 0.8 mm `ev:results/wall.json#/metrics/wall_mm/limit` 만 증거에 "(proposed)"로 적혀 있어 고객 사양이 아니다.

## 1b. 실행 기록과 재현성 관찰

```
실행 대상: a0b60052acd5 (master), 시작 시 작업 트리 깨끗함(git diff sha256 e3b0c442… = 빈 diff), 공유 체크아웃
동시 세션: 없음 (hvp_session.py signals: VERDICT NONE — 다른 잠금 0, 시작 뒤 커밋 0, 다른 프로세스 0)
           [b] 최근 30분 수정 파일 7개는 전부 HEAD와 같은 추적 파일(체크아웃 시각)이라 외부 편집 신호로 보지 않음
HEAD 타임라인: a0b60052acd5 → a0b60052acd5, 실행 중 입력 변경 아니오
원격: git remote 없음 → pull/fetch 대상 없음. 브랜치는 master 하나
도구: Python 3.13.16, git 2.43.0. skill 도구 자체 시험 SELFTEST 64/64 passed
정본 증거: 건드리지 않음. 모든 검사는 HEAD의 임시 worktree에서 돌림(검사기가 results/에 바로 덮어쓰기 때문)
```

| 단계 | 목표 | 명령 | 소요 | 증거 경로 | 판정(증거 status) | 이중 실행 | 재현 여부 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| L2 여유 | 조립 | `python3 tools/check_clearance.py` | 1초 미만 | `results/clearance.json` | FAIL `ev:results/clearance.json#/status` | 시드 4개 일치 | 정본과 EQUIVALENT, 바이트 동일 |
| L3 벽 강도 | 타당성 | `python3 tools/check_wall.py` | 1초 미만 | `results/wall.json` | PASS `ev:results/wall.json#/status` | 시드 4개 일치 | 정본과 EQUIVALENT, 바이트 동일 |
| 전체 스크립트 | — | `./run_all.sh` | 1초 미만 | 위 두 파일 | 종료 코드 0(판정 아님, 아래 참고) | 시드 2개 일치 | — |

이중 실행 도구 출력 원문(축 `PYTHONHASHSEED`, 미추적 입력 복사 없음):

```
# hvp_determinism.py --cmd "python3 tools/check_clearance.py" --out results/clearance.json --seeds 1,7,42,1234 --grep tools --keep
commit a0b60052acd5 | inputs copied: none
RESULT 4 values agree (PYTHONHASHSEED=1,7,42,1234); this is not a proof of determinism
--- unordered-iteration candidates in tools: 0
# 각 실행 로그: clearance FAIL -0.15

# hvp_determinism.py --cmd "python3 tools/check_wall.py" --out results/wall.json --seeds 1,7,42,1234 --grep tools --keep
RESULT 4 values agree (PYTHONHASHSEED=1,7,42,1234); this is not a proof of determinism
--- unordered-iteration candidates in tools: 0
# 각 실행 로그: wall PASS 2.125

# hvp_determinism.py --cmd "./run_all.sh" --out results/clearance.json --seeds 1,7 --keep
PYTHONHASHSEED=1 exit 0
PYTHONHASHSEED=7 exit 0
RESULT 2 values agree (PYTHONHASHSEED=1,7); this is not a proof of determinism

# hvp_compare.py git:HEAD:results/clearance.json <재생성 결과>   → EQUIVALENT, ignored volatile keys (changed): none
# hvp_compare.py git:HEAD:results/wall.json <재생성 결과>        → EQUIVALENT, ignored volatile keys (changed): none
# hvp_stale.py                                                  → SUMMARY stale=0 missing=0
```

- **`run_all.sh`의 종료 코드는 판정이 아니다**: 여유 검사가 FAIL을 써도 스크립트는 0으로 끝난다(위 로그). 검사기가 FAIL에서 0이 아닌 코드로 끝나지 않기 때문이다. 판정은 증거 파일의 `status`로만 읽어야 한다.
- **실행 기록 위치**: 임시 worktree 결과와 로그를 저장소 안 `verification/runs/a0b6005/20261021T162125Z/`에 복사해 두었다(`manifest.json`에 HEAD, 입력·검사기·정본 증거 해시, 도구 버전). 커밋하지 않았다. 남길지는 사용자 결정이다.
- **NOT_RUN**: 없음(저장소의 검사 2개를 모두 돌림). 저장소에 검사가 없는 항목은 2절 표의 "근거 없음"이다.
- **층 순서 관련 공개**: skill 규칙상 앞 층(L2)이 FAIL이면 뒤 층(L3)을 판정에 쓰지 않는다. 이번에는 기존 정본 증거가 재현되는지 보려고 L3도 돌렸고, 그 PASS는 잠정으로만 집계했다.
- **재계산 대조**(INFERRED, 손 계산):
  - 여유 = gap 0.3 `ev:params/cam.json#L2` 에서 돌출 0.45 `ev:tools/check_clearance.py#L4` 를 뺀 값 -0.15 `ev:results/clearance.json#/metrics/min_clearance_mm/value`, 일치.
  - 벽 = envelope 4.0 `ev:params/cam.json#L3` 에서 gap의 두 배를 뺀 3.4 `ev:results/wall.json#/metrics/wall_mm/value`, 일치.
  - 안전율 = 벽 두께를 1.6 `ev:tools/check_wall.py#L5` 로 나눈 2.125 `ev:results/wall.json#/metrics/safety_factor/value`, 일치.
- **정본 갱신**: 하지 않음(재생성 결과가 정본과 바이트 동일).

| FAIL | 유형 | 완료 정의 안 | 처리 |
| --- | --- | --- | --- |
| `results/clearance.json` (L2 조립) | 설계(공칭 입력 확인 필요) | 예 | 이번에는 보고만 함. 수정은 사용자 결정 뒤 수정 루프로 |

## 2. 세 질문 커버리지

| 부품·하위 시스템 | 물리적 타당성 | 조립 가능성 | 의도 동작 |
| --- | --- | --- | --- |
| 하우징 벽 | `check_wall.py` 폐형식 — PASS 잠정(OBSERVED, mock 재료 모델) | 아래 여유 검사를 공유 | 해당 검사 없음 |
| 캠 로브–하우징 쌍 | 캠 자체 강도 검사 없음 | `check_clearance.py` 폐형식 — FAIL(OBSERVED) | 검사 없음 |
| 캠 회전·종동 기구 | 검사 없음 | 분해 경로 검사 없음 | 연결 그래프·구속 해석 없음 |

검사별 분류:

- `check_clearance.py`: 조립에 답한다. 다만 CAD 형상이 아니라 숫자 두 개의 뺄셈이다. 증거 scope에 "full rotation"이 적혀 있어도 회전 위상별 거리를 잰 것이 아니다. 그래서 이 검사가 PASS가 되더라도 "전 행정 간섭 없음"의 근거(거리 기반 연속 보장)는 되지 못한다. 공차 누적도 보지 않는다(limitations에 기록됨).
- `check_wall.py`: 타당성에 답한다. 허용치 나눗수 1.6 `ev:tools/check_wall.py#L5` 의 출처가 기록되지 않았다. 증거에는 "allowable scaled by k (mock material model)"로만 적혀 있다. 하중 크기와 재료 값도 증거에 없다.
- 두 검사 모두 known-answer 픽스처가 없고, 실측과 비교하지 않았다(verified일 뿐 validated 아님).

## 3. 실패 원인 단서 (수정 전 관찰)

여유 FAIL의 크기는 하한 대비 -0.15 mm `ev:results/clearance.json#/metrics/min_clearance_mm/value`, 목표 대비 0.30 mm 부족이다(INFERRED: 목표 0.15 `ev:results/clearance.json#/metrics/min_clearance_mm/target` 에서 현재 값을 뺌).

- 이 FAIL은 검사기에 하드코딩된 로브 돌출 0.45 mm `ev:tools/check_clearance.py#L4` 와 gap 0.3 mm `ev:params/cam.json#L2` 두 값으로만 정해진다.
- 돌출값의 출처 "cam drawing"은 저장소에 없다. 그래서 이 값이 지금 캠 형상과 같은지 확인할 수 없다(공칭–실측 불일치 대장 후보, 상태 "확인 필요"). 돌출값이 실제보다 크게 잡혀 있다면 설계 결함이 아니라 공칭 입력 문제일 수 있다. 반대로 작게 잡혀 있다면 실제 간섭은 더 크다.
- 돌출값은 파라미터 파일이 아니라 검사기 코드 안에 있다. 그래서 증거의 입력 해시(`params/cam.json`만 기록)로는 이 값의 변경이 보이지 않는다.

## 4. 사용자 결정 항목: gap 수정 후보

gap 하나가 두 검사에 반대 방향으로 들어간다: 여유는 gap에 비례해 오르고 `ev:tools/check_clearance.py#L4`, 벽 두께는 gap의 두 배만큼 줄어든다 `ev:tools/check_wall.py#L4`. 한 질문의 여유를 다른 질문의 여유와 맞바꾸는 경우라 사용자가 정한다. 아래는 후보 평가 자료이며 판정이 아니다.

`hvp_sweep.py`로 HEAD의 임시 worktree에서 gap만 바꿔 두 검사기를 돌린 출력 원문:

```
SWEEP min_clearance_mm over 0.3,0.45,0.5,0.6,0.7,0.8,0.9 (commit HEAD)
  value        metric         status
  0.3          -0.15          FAIL
  0.45         0.0            PASS_BELOW_TARGET
  0.5          0.05           PASS_BELOW_TARGET
  0.6          0.15           PASS
  0.7          0.25           PASS
  0.8          0.35           PASS
  0.9          0.45           PASS
MONOTONIC increasing
FIRST_LIMIT 0.45
FIRST_TARGET 0.6

SWEEP safety_factor over 0.3,0.45,0.5,0.6,0.7,0.8,0.9 (commit HEAD)
  value        metric         status
  0.3          2.125          PASS
  0.45         1.9375         PASS
  0.5          1.875          PASS
  0.6          1.75           PASS
  0.7          1.625          PASS
  0.8          1.5            PASS
  0.9          1.375          PASS_BELOW_TARGET
MONOTONIC decreasing
```

해석(INFERRED, 위 스윕과 검사기 식에서):

- 여유 목표에 처음 닿는 gap은 0.6 `ev:verification/runs/a0b6005/20261021T162125Z/sweep-gap-clearance.json#L64` 이다.
- 그 gap에서 벽 안전율은 1.75 `ev:verification/runs/a0b6005/20261021T162125Z/sweep-gap-wall.json#L31` 이다.
- 벽 안전율 목표를 지키는 마지막 표본 gap은 0.8 `ev:verification/runs/a0b6005/20261021T162125Z/sweep-gap-wall.json#L46` 이다.
- 두 식이 gap에 대해 선형이라(검사기 코드), 두 목표를 함께 지키는 gap 구간은 0.6–0.8 `ev:verification/runs/a0b6005/20261021T162125Z/sweep-gap-wall.json#L46` 로 추정된다(INFERRED, 표본 사이는 식으로만 보증).

| 후보 | 바꿀 것 | 대상 지표(스윕) | 부작용 | 인터페이스 | 되돌리기 | 결정 |
| --- | --- | --- | --- | --- | --- | --- |
| A | `params/cam.json`의 gap을 0.6–0.8 구간의 값으로 | 여유 목표 도달 0.6 `ev:verification/runs/a0b6005/20261021T162125Z/sweep-gap-clearance.json#L64` | 벽 안전율 하락, 0.8에서 1.5 `ev:verification/runs/a0b6005/20261021T162125Z/sweep-gap-wall.json#L47` | 없음(외곽 envelope 유지) | 쉬움 | 사용자 결정 |
| B | 캠 로브 돌출 축소(캠 형상 변경) | 스윕 안 함 | 캠 프로파일·행정 변화 가능 | 캠 형상 | 보통 | 사용자 결정 |
| C | envelope 확대 | 스윕 안 함 | 외형 변화 | 외부 인터페이스 | 어려움 | 사용자 결정 |

- **에이전트 추천(근거 포함)**: 먼저 제안 1(로브 돌출 0.45 mm `ev:tools/check_clearance.py#L4` 의 도면 확인)을 한다. 값이 맞으면 후보 A로 간다. A는 인터페이스 변경이 없고 파라미터 파일 한 줄만 바뀌어 되돌리기 쉽다.
- **A의 값 고르기**: 정확히 0.6을 고르면 공차 여유가 0이다 `ev:verification/runs/a0b6005/20261021T162125Z/sweep-gap-clearance.json#L64`. 이 검사는 공차 누적을 보지 않는다(`results/clearance.json`의 limitations). 그래서 값은 "0.6 + 공차 여유"로 정해야 하고, 그 공차 여유는 공차 해석이나 사용자 결정이 필요하다.
- **결정하지 않으면 멈추는 것**: 조립 FAIL 해소와 벽 강도 확정 판정. 기준값(하한·목표) 변경은 README상 고객이 정하는 값이라 후보에 넣지 않았다.

## 5. 검사기 신뢰와 기록의 한계

- known-answer 픽스처: 두 검사기 모두 없음. 이번 확인은 시드 4개 일치와 손 재계산뿐이다. 검사기가 "답을 아는 입력에서 답을 낸다"는 근거는 아니다.
- 증거의 `provenance`에 `command`만 있고 `commit`, `tool_sha256`이 없다. 검사기 코드를 바꿔도 `hvp_stale.py`가 낡은 증거를 잡지 못한다.
- 검사기는 FAIL에서도 종료 코드 0으로 끝난다. CI나 훅 게이트로 쓰려면 증거 status를 읽는 단계가 따로 필요하다.

## 직접 확인

| 확인할 것 | 파일 | 다시 만드는 명령(저장소 루트) | 사람이 볼 것 |
| --- | --- | --- | --- |
| 캠–하우징 최소 여유 | `results/clearance.json` | `python3 tools/check_clearance.py` | 출력 줄 `clearance FAIL -0.15` |
| 하우징 벽 안전율 | `results/wall.json` | `python3 tools/check_wall.py` | 출력 줄 `wall PASS 2.125` |
| 이번 실행 기록 | `verification/runs/a0b6005/20261021T162125Z/` | `manifest.json`, `det-*/run-*.log` | 시드별 로그, 해시 |
| gap 스윕 | 같은 폴더 `sweep-gap-*.json` | 아래 스윕 명령 | `rows` 표 |

주의: 위 두 검사기 명령은 정본 `results/*.json`을 그 자리에서 덮어쓴다. 값이 같으면 `git diff`에 변화가 없다. 정본을 건드리지 않고 돌리려면 `git worktree add ../cam-check HEAD`로 만든 폴더에서 실행한다.

아래 명령의 `<skill>`은 hardware-verification-plan skill 폴더다.

```sh
# 손으로 확인: 숫자가 어디서 오는지
cat params/cam.json; sed -n 4p tools/check_clearance.py; sed -n 4,5p tools/check_wall.py

# 값 하나의 뜻·출처·재현 여부
python3 <skill>/scripts/hvp_cite.py get results/clearance.json /metrics/min_clearance_mm/value --rerun
python3 <skill>/scripts/hvp_cite.py get results/wall.json /metrics/safety_factor/value --rerun

# 시드 이중 실행(정본 안 건드림)
python3 <skill>/scripts/hvp_determinism.py --cmd "python3 tools/check_clearance.py" --out results/clearance.json --seeds 1,7,42,1234 --grep tools

# gap 스윕(정본 안 건드림)
python3 <skill>/scripts/hvp_sweep.py --commit HEAD --cmd 'python3 -c "import json;p=json.load(open(\"params/cam.json\"));p[\"gap_mm\"]={value};json.dump(p,open(\"params/cam.json\",\"w\"),indent=1)" && python3 tools/check_wall.py' --out results/wall.json --metric safety_factor --values 0.3,0.45,0.5,0.6,0.7,0.8,0.9

# 이 보고서의 인용 검사
python3 <skill>/scripts/hvp_cite.py check docs/report.md --rerun
```

이 보고서의 인용 검사(작성 시점): `SUMMARY citations_ok=36 problems=0 uncited=0 no_context=0 reproduced=7/7`

사용자 결정이 필요한 항목:

- [ ] 로브 돌출 0.45 mm `ev:tools/check_clearance.py#L4` 가 현재 캠 도면과 같은지 확인
- [ ] gap 수정 후보 A/B/C 선택, A라면 공차 여유를 더한 값(4절)
- [ ] `verification/runs/` 실행 기록을 커밋할지 여부(지금은 미추적 파일)
- [ ] 벽 두께 하한 0.8 mm `ev:results/wall.json#/metrics/wall_mm/limit` (proposed)를 기준으로 채택할지

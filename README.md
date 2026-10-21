# engineering-verification-skill

(skill 이름은 `hardware-verification-plan`이다. 설치 경로와 skill 이름은 그대로 쓴다.)

실물 엔지니어링 프로젝트(CAD 기구, 조립체, 공정 기계, 3D 출력·가공 부품, 펌웨어·제어)를 AI 에이전트가 검증·테스트·수정할 수 있게 하는 Claude skill이다. 방법론 문서와 함께, 검증 절차와 검증 결과에 근거한 설계 수정 루프를 실제로 돌리는 도구 16종, 그 도구의 자체 시험, 에이전트 행동을 보는 평가 세트를 담고 있다.

## 무엇을 하는가

검증이 답해야 하는 질문을 세 가지로 둔다.

| 질문 | 대표 근거 |
| --- | --- |
| 물리적으로 타당한가 | 강도·체결 허용치, 힘 전달, 열회로, 실측 보정 |
| 실제로 조립 가능한가 | 정적·연속 간섭, 분해 경로, 공구 접근, 공차, 출력·가공 가능성 |
| 의도대로 동작하는가 | 연결 그래프, 구속 해석, 소재 통로, 제어 상태공간, SIL→HIL |

핵심 원칙은 네 가지다.

1. **싼 오라클이 비싼 도구를 문지기한다.** 커밋마다 CPU에서 도는 헤드리스 검사(계약·세대 감사, 연결 그래프, 구속 해석, 거리 기반 연속 간섭, 통로 복셀 BFS)를 1차 판정으로 두고, 통과한 형상 해시에서만 Fusion·Isaac 같은 원격·GPU 도구를 돌린다.
2. **시뮬레이터에는 답할 수 있는 질문만 묻는다.** 강체 엔진에 파쇄를, 처방 구동 축에 토크를, 단일 시드 궤적에 dt 수렴을 묻지 않는다.
3. **실측 보정이 VALIDATED의 유일한 경로다.** 게이지 MSA, 쿠폰, 서브 리그, 초품 검사로 모델 파라미터를 보정하고, 보정값이 바뀌면 그 값을 쓴 판정을 다시 돌린다.
4. **개발의 주체는 사용자다.** 에이전트가 보고하는 숫자와 판정에는 증거 파일과 그 안의 위치(`ev:<파일>#<포인터>`), 생성 명령이 붙는다. 사용자는 그 파일을 열거나 같은 명령을 다시 돌려 직접 확인한다. 출처를 댈 수 없는 숫자는 판정과 결정의 근거가 되지 않고, 결정은 근거와 함께 사용자에게 넘어간다.

요청에 따라 네 가지 모드로 동작한다.

| 모드 | 요청 예 | 주 산출물 |
| --- | --- | --- |
| 계획 | "검증 방법론 세워 줘", "테스트 계획" | 세 질문 커버리지 진단, 방법론·테스트 계획 문서 |
| 실행·보고 | "E2E 검증하고 정리해" | 실행한 검사의 결과와 재현성 관찰 |
| 수정 | "이 FAIL 고쳐", "안전율 목표까지 올려" | 원인, 후보 비교, 수정, 회귀 판정, 시도 이력, 갱신된 문서 |
| 제작 착수 준비 | "당장 시제품을 만들 수 있게" | 부품별 판정표, 승인 구분, 초품 검사·실측 패킷 |

## 검증 기반 수정 루프

수정 모드는 검증 결과를 입력으로 받아 설계를 바꾸고, 같은 검증 체인으로 다시 판정받는 닫힌 루프다. 에이전트는 원인을 찾고 설계를 바꾸며, 고쳐졌는지는 오라클이 판정한다.

```
분류 → 원인 규명 → 수정 대상 결정 → 후보와 크기 → 선택 → 영향 범위 → 적용 → 재검증·회귀 판정 → 반증 → 루프 제어 → 기록·커밋
```

- **원인 가설은 예측으로 쓴다**: "p를 Δ 바꾸면 지표 m이 약 Δm 바뀐다"를 수정 전에 두 점 스윕으로 확인한다.
- **무엇을 고칠지 먼저 정한다**: 설계 파라미터, 설계 구조, 해석 모델, 검사기, 공칭 입력, 요구·기준 중 어디를 고칠지와 그 결정 권한을 정한다. 기준은 사용자만 바꾼다.
- **크기는 데이터로 정한다**: `hvp_sweep.py`가 후보 값마다 1차 오라클을 돌려 하한·목표에 도달하는 값과 단조성을 낸다.
- **회귀 판정이 "고쳤다"의 근거다**: `hvp_verdicts.py`가 수정 전후를 비교한다. 대상 FIXED, 그리고 REGRESSED·LOST·CRITERIA_CHANGED·INCONSISTENT 0일 때만 ACCEPTABLE이다.
- **루프 제어**: `hvp_fixloop.py`가 시도 예산, 같은 값 반복, 두 검사가 번갈아 깨지는 결합, 진동, 정체를 감지해 멈춘다.
- **실패 유형별 수정 패턴**: 간섭, 조립 불가, 공차, 강도, 체결, 이동도, 통로, 열, 제어, 제작성 12종의 작은 변경부터의 순서, 재검증 오라클, 흔한 부작용을 담았다.

## 구성

```
hardware-verification-plan/
├── SKILL.md                     절차(0–11단계), 오라클 배정표, 함정, 범위와 안전
├── references/
│   ├── oracle-catalog.md        오라클(피로·유체·파괴·진동·크리프·ERC/DRC 포함), 판정 기록 규약, 수정 패턴, 복합 검증, V&V·정확도, 효율
│   ├── runbook.md               실행 절차: 세션 격리, 결정론, 낡은 증거, 커밋 무결성, 제작 준비, 수정 루프
│   ├── pitfalls.md              흔한 함정 31가지
│   └── templates.md             문서 템플릿: 범위표, 실행 기록, 수정 보고·후보 비교·시도 이력, 불일치 대장, 초품 검사표
└── scripts/
    ├── hvp_session.py           세션 잠금, 동시 세션 신호, worktree 격리, 종료
    ├── hvp_determinism.py       같은 커밋을 시드·순서 축만 바꿔 실행하고 비교
    ├── hvp_stale.py             기록된 입력 해시 대 현재 해시 전수 검색, 재생성 순서
    ├── hvp_compare.py           증거 JSON 의미 비교(정확한 키 이름으로만 휘발성 제외)
    ├── hvp_stagecheck.py        커밋 직전 스테이지 점검
    ├── hvp_snapshot.py          인덱스 그대로의 스냅샷에서 pre-push 스위트 실행
    ├── hvp_impact.py            바꿀 경로에 의존하는 증거(직접·전이)와 재실행 순서
    ├── hvp_sweep.py             후보 값마다 1차 오라클 실행, 하한·목표 도달 값과 단조성
    ├── hvp_verdicts.py          수정 전후 판정 비교(FIXED, REGRESSED, LOST, MARGIN_DROP, CRITERIA_CHANGED)
    ├── hvp_fixloop.py           이슈별 시도 기록, 예산·반복·결합·진동·정체 감지
    ├── hvp_redgreen.py          회귀 시험이 수정을 되돌리면 실패하는지 확인
    ├── hvp_cite.py              숫자 하나의 뜻·출처·재현 여부, 보고서 인용 검사
    ├── hvp_gci.py               메시 수렴(관측 차수, 외삽값, GCI)
    ├── hvp_consensus.py         독립 방법들의 합의(E_n)와 보수 경계 판정
    ├── hvp_fatigue.py           레인플로, 평균응력·Miner 피로 손상, Paris 균열 성장, LEFM
    ├── hvp_flow.py              배관·유로 압력 손실(층류·난류·천이)
    ├── hvp_common.py            공용 함수
    └── selftest.py              위 도구들의 known-answer 자체 시험(84건)

evals/                           (skill 패키지 밖) 행동 시나리오 9개, 발동 평가 20문항, 모의 저장소 생성기, 실행 결과(`evals/results/`)
```

도구는 Python 3.8 이상과 git 2.31 이상만 쓴다(표준 라이브러리). 대상 저장소에 파일을 만들지 않는다. 세션 잠금은 `.git` 안, 임시 worktree는 시스템 임시 디렉터리에 둔다.

## 설치

**Claude Code**: skill 디렉터리를 개인 skill 경로에 복사한다.

```sh
cp -r hardware-verification-plan ~/.claude/skills/
```

**Claude 앱(.skill 파일)**: 아래처럼 묶은 `.skill` 파일을 Claude에 올려 설치한다.

```sh
zip -r hardware-verification-plan.skill hardware-verification-plan -x '*/__pycache__/*'
```

**다른 에이전트**: `SKILL.md`를 읽는 위치(예: `~/.agents/skills/`)에 같은 디렉터리를 둔다.

## 처음 쓸 때

설치한 환경에서 도구 자체가 맞게 동작하는지 먼저 확인한다. 검사기를 믿기 전에 정답을 아는 입력으로 시험한다는 skill의 원칙을 도구에도 적용한 것이다.

```sh
python3 hardware-verification-plan/scripts/selftest.py      # SELFTEST 84/84 passed
```

## 사용 예

에이전트에게는 평소처럼 요청하면 된다.

- "이 저장소를 읽고 검증 방법론과 테스트 계획을 세워 줘"
- "현재 설계를 E2E 검증하고 결과를 문서로 정리해"
- "조립 검사에서 막힌 부분 원인 찾아서 고쳐"
- "브래킷 안전율이 목표 미달인데 다른 검사 안 깨지게 목표까지 올려"
- "시제품 제작을 시작할 수 있게 부품별 판정표와 초품 검사표를 만들어 줘"

도구를 직접 쓸 때(`$S`는 `scripts` 디렉터리):

```sh
python3 $S/hvp_session.py start --sid agent-1                 # 동시 세션이 있으면 exit 2
python3 $S/hvp_session.py isolate --sid agent-1 --commit HEAD  # 전용 worktree
python3 $S/hvp_stale.py                                        # 낡은 증거와 재생성 순서
python3 $S/hvp_compare.py git:HEAD:results/x.json results/x.json
python3 $S/hvp_determinism.py --cmd "python3 tools/check.py" --out results/check.json --seeds 1,7,42,1234
python3 $S/hvp_stagecheck.py --sid agent-1
python3 $S/hvp_snapshot.py --cmd "nix develop -c ./run_light_suite.sh"
python3 $S/hvp_session.py end --sid agent-1
```

수정 루프(자세한 순서는 `references/runbook.md` 9절):

```sh
python3 $S/hvp_fixloop.py open --sid agent-1 --issue I-1 --checks results/clearance.json --cause "캠 돌기가 간격을 0.15 mm 침범"
python3 $S/hvp_impact.py params/cam.json                                   # 재실행 목록
python3 $S/hvp_sweep.py --cmd "<파라미터 설정 {value}> && python3 tools/check_clearance.py" \
    --out results/clearance.json --metric min_clearance_mm --values 0.45,0.5,0.55,0.6,0.65
# 고른 값 적용 → hvp_stale.py ORDER대로 재생성 → 영향 범위와 L0–L2 재실행
python3 $S/hvp_verdicts.py --before git:<시작 커밋> --expect-fixed results/clearance.json --json > /tmp/v1.json
python3 $S/hvp_fixloop.py attempt --sid agent-1 --issue I-1 --change "간격 확대" --params gap_mm=0.6 --verdicts /tmp/v1.json
python3 $S/hvp_fixloop.py show --sid agent-1 --issue I-1 --md           # 시도 이력(7b)
```

## 범위와 안전

- 무기나 고에너지 발사체(장거리 발사, 격발·방출 기구, 발사용 저장 에너지와 그 충전·방출 구동)의 설계, 성능, 격발 신뢰성은 검증·수정·개선하지 않는다. 범위표에서 제외로 분류하고, 실행 전 확인 지점에서 해당 경로의 실행을 멈춘다. 같은 프로젝트의 다른 하위 시스템은 정상적으로 다룬다.
- 타인 저작물의 외형을 새로 모델링·렌더링해 원작에 맞추는 작업은 하지 않는다. 사용자가 가진 형상 파일 기준의 내부 기구·맞춤·체결·출력 분할 검증은 한다.
- 회전체, 히터, 고전류, 상용 전원 관련 실측은 물리 lockout과 사용자 승인을 전제로만 계획한다. 구매·가공·통전·push는 사용자 승인 없이 진행하지 않는다.

## 버전

| 버전 | 주요 변화 |
| --- | --- |
| v1 | 계획 모드: 오라클 배정, L0–L5 층, 작업 패키지, 테스트 계획, ADR |
| v2 | 실행·보고 / 수정 모드, 범위표, 실행 격리, 특징 의도·형상 재측정·조립 가능성 오라클 |
| v3 | 세 가지 검증 질문, 제작 착수 준비 모드, runbook, known-answer, 결정론 이중 실행, 두 단 합격 기준 |
| v3.1 | runbook 명령 오류 8건 수정(의미 비교의 거짓 EQUIVALENT 등), 시험 저장소 재검증 |
| v3.2 | 검증 도구 6종과 자체 시험 추가, SKILL.md 8·9단계를 runbook으로 이동 |
| v4 | 검증 기반 수정 루프: 수정 대상 결정, 후보와 크기(스윕), 회귀 판정, 루프 제어, 수정 패턴 12종, 도구 4종 |
| v5 | 주장–증거 표, red-green 회귀 시험 확인, 원인 규명 보강(통과 상태 비교·경계 계측), ERC·DRC 오라클, CAD 연동 도구 출력 처리, 시운전 중단 기준, 행동·발동 평가 세트 |
| v6 | 사용자가 개발 주체라는 원칙: 숫자마다 출처 인용, 직접 확인 절, 결정 넘기기, `hvp_cite.py`, 증거의 생성 명령·기준 출처 기록 |
| v6.1 | 숫자의 뜻(범위·가정·한계) 필수, 기록된 명령으로 재실행 대조, 인용 검사 휴리스틱 수정 |
| v7 | 불확도 반영 판정(INDETERMINATE), GCI, 스윕 경계 이분, 복합 검증, 피로·유체·파괴·진동·크리프, 평가 시나리오 9 실행 |

자세한 내용은 [CHANGELOG.md](CHANGELOG.md).

## 검증 상태

- `selftest.py` 84/84 통과: Linux, Python 3.13, git 2.43. 수정 루프 도구는 도구 결함 6종을 일부러 심어 시험이 그 결함을 잡는지 확인했고, 모의 저장소에서 runbook 9절 순서를 처음부터 끝까지 실행했다.
- 시험하지 못한 것: macOS의 `lsof` 경로, FreeCAD 헤드리스 드라이버, nix 환경과 실제 저장소의 pre-push 훅. 해당 환경에서 `selftest.py`를 먼저 돌린다.

- 행동 평가는 시나리오 9만 skill 있음·없음 각 1회 실행했다. 결과와 채점 원문은 `evals/results/2026-10-22-scenario9/`에 있다. skill 쪽이 인용·재현 검사를 통과했고(36개 인용, 재현 7/7), 내용의 핵심은 기준선도 해냈다. 비용은 토큰 약 2.5배였다. 나머지 시나리오와 발동 평가는 아직 실행하지 않았다.

## 라이선스

[GNU General Public License v3.0](LICENSE) (GPL-3.0-or-later). 이 저장소의 문서와 스크립트를 복사·수정·배포할 수 있고, 수정본을 배포할 때는 같은 GPL v3 조건으로 소스를 공개해야 한다. 이 skill로 만든 검증 결과나 대상 프로젝트의 파일에는 라이선스가 전염되지 않는다.

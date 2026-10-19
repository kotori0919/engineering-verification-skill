# hardware-verification-plan

실물 엔지니어링 프로젝트(CAD 기구, 조립체, 공정 기계, 3D 출력·가공 부품, 펌웨어·제어)를 AI 에이전트가 검증·테스트·수정할 수 있게 하는 Claude skill이다. 방법론 문서와 함께, 검증 절차를 실제로 돌리는 도구 6종과 그 도구의 자체 시험을 담고 있다.

## 무엇을 하는가

검증이 답해야 하는 질문을 세 가지로 둔다.

| 질문 | 대표 근거 |
| --- | --- |
| 물리적으로 타당한가 | 강도·체결 허용치, 힘 전달, 열회로, 실측 보정 |
| 실제로 조립 가능한가 | 정적·연속 간섭, 분해 경로, 공구 접근, 공차, 출력·가공 가능성 |
| 의도대로 동작하는가 | 연결 그래프, 구속 해석, 소재 통로, 제어 상태공간, SIL→HIL |

핵심 원칙은 세 가지다.

1. **싼 오라클이 비싼 도구를 문지기한다.** 커밋마다 CPU에서 도는 헤드리스 검사(계약·세대 감사, 연결 그래프, 구속 해석, 거리 기반 연속 간섭, 통로 복셀 BFS)를 1차 판정으로 두고, 통과한 형상 해시에서만 Fusion·Isaac 같은 원격·GPU 도구를 돌린다.
2. **시뮬레이터에는 답할 수 있는 질문만 묻는다.** 강체 엔진에 파쇄를, 처방 구동 축에 토크를, 단일 시드 궤적에 dt 수렴을 묻지 않는다.
3. **실측 보정이 VALIDATED의 유일한 경로다.** 게이지 MSA, 쿠폰, 서브 리그, 초품 검사로 모델 파라미터를 보정하고, 보정값이 바뀌면 그 값을 쓴 판정을 다시 돌린다.

요청에 따라 네 가지 모드로 동작한다.

| 모드 | 요청 예 | 주 산출물 |
| --- | --- | --- |
| 계획 | "검증 방법론 세워 줘", "테스트 계획" | 세 질문 커버리지 진단, 방법론·테스트 계획 문서 |
| 실행·보고 | "E2E 검증하고 정리해" | 실행한 검사의 결과와 재현성 관찰 |
| 수정 | "이 FAIL 고쳐" | 원인, 수정, 재실행 증거, 갱신된 문서 |
| 제작 착수 준비 | "당장 시제품을 만들 수 있게" | 부품별 판정표, 승인 구분, 초품 검사·실측 패킷 |

## 구성

```
hardware-verification-plan/
├── SKILL.md                     절차(0–11단계), 오라클 배정표, 함정, 범위와 안전
├── references/
│   ├── oracle-catalog.md        오라클 28종: 구현, 출력, 합격 기준, known-answer 픽스처
│   ├── runbook.md               실행 절차: 세션 격리, 결정론, 낡은 증거, 커밋 무결성, 제작 준비
│   └── templates.md             문서 템플릿: 범위표, 실행 기록, 수정 보고, 불일치 대장, 초품 검사표
└── scripts/
    ├── hvp_session.py           세션 잠금, 동시 세션 신호, worktree 격리, 종료
    ├── hvp_determinism.py       같은 커밋을 시드·순서 축만 바꿔 실행하고 비교
    ├── hvp_stale.py             기록된 입력 해시 대 현재 해시 전수 검색, 재생성 순서
    ├── hvp_compare.py           증거 JSON 의미 비교(정확한 키 이름으로만 휘발성 제외)
    ├── hvp_stagecheck.py        커밋 직전 스테이지 점검
    ├── hvp_snapshot.py          인덱스 그대로의 스냅샷에서 pre-push 스위트 실행
    ├── hvp_common.py            공용 함수
    └── selftest.py              위 도구들의 known-answer 자체 시험(26건)
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
python3 hardware-verification-plan/scripts/selftest.py      # SELFTEST 26/26 passed
```

## 사용 예

에이전트에게는 평소처럼 요청하면 된다.

- "이 저장소를 읽고 검증 방법론과 테스트 계획을 세워 줘"
- "현재 설계를 E2E 검증하고 결과를 문서로 정리해"
- "조립 검사에서 막힌 부분 원인 찾아서 고쳐"
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

자세한 내용은 [CHANGELOG.md](CHANGELOG.md).

## 검증 상태

- `selftest.py` 26/26 통과: Linux, Python 3.13, git 2.43.
- 시험하지 못한 것: macOS의 `lsof` 경로, FreeCAD 헤드리스 드라이버, nix 환경과 실제 저장소의 pre-push 훅. 해당 환경에서 `selftest.py`를 먼저 돌린다.

라이선스는 아직 정하지 않았다.

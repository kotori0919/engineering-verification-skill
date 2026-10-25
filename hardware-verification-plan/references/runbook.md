# 실행 런북

SKILL.md 1·6·7·8·9단계의 실행 절차. 반복되는 점검은 `scripts/`의 도구로 하고, 이 문서는 언제 어떤 도구를 어떻게 쓰고 결과를 어떻게 읽는지 정한다. 저장소에 같은 일을 하는 스크립트가 있으면 그것을 쓴다. 도구는 대상 저장소에 파일을 만들지 않는다(세션 잠금은 `.git` 안, 임시 worktree는 시스템 임시 디렉터리).

## 목차

0. 도구 목록과 자체 시험
1. 세션 잠금과 격리
2. 오라클 실행표와 도구별 패턴
3. 결정론 이중 실행
4. 낡은 증거 전수 검색과 의미 비교
5. 스테이지 직전 점검
6. 커밋 스냅샷 검증
7. 증거 무결성 규칙
8. 제작 착수 준비 절차
9. 설계 수정 루프
10. 보고와 인용
11. 판단 자료
12. 데이터 계층: 환경과 도구

---

## 0. 도구 목록과 자체 시험

필요한 것은 `python3`(3.8 이상)와 `git`(2.31 이상)뿐이다. Linux와 macOS에서 같이 돈다. 모든 도구는 대상 저장소(또는 그 worktree) 안에서 실행한다. 아래에서 `$S`는 이 skill의 `scripts` 디렉터리 절대 경로다.

| 도구 | 하는 일 | 종료 코드 |
| --- | --- | --- |
| `hvp_session.py` | 세션 잠금 등록, 동시 세션 신호, worktree 격리, touched 기록, 종료 | 0 정상, 1 거부, 2 동시 세션 |
| `hvp_determinism.py` | 같은 커밋을 시드·순서 축만 바꿔 실행하고 결과 비교 | 0 일치, 1 불일치·실행 실패 |
| `hvp_stale.py` | 기록된 입력 해시 대 현재 해시 전수 검색, 재생성 순서, 파일별 역참조 | 0 없음, 1 STALE·MISSING |
| `hvp_compare.py` | 증거 JSON 두 개의 의미 비교 | 0 EQUIVALENT, 1 DIFFERENT |
| `hvp_stagecheck.py` | 커밋 직전 스테이지 점검 | 0 CLEAN, 1 해결 필요 |
| `hvp_snapshot.py` | 인덱스 그대로의 스냅샷에서 스위트 실행 | 스위트 종료 코드 |
| `hvp_impact.py` | 바꿀 경로에 의존하는 증거(직접·전이)와 재실행 순서, 해시 기록 없는 의존 | 0 정상, 3 입력 오류 |
| `hvp_sweep.py` | 후보 값마다 임시 worktree에서 1차 오라클 실행, 하한·목표 도달 값과 단조성 | 0 목표 도달, 1 미도달, 2 실행 실패 |
| `hvp_verdicts.py` | 수정 전후 판정 비교: FIXED, REGRESSED, LOST, MARGIN_DROP, CRITERIA_CHANGED, INCONSISTENT | 0 ACCEPTABLE, 1 NOT_ACCEPTABLE |
| `hvp_fixloop.py` | 이슈별 시도 기록, 예산·반복·결합·진동·정체 감지 | 0 계속, 3 멈춤 |
| `hvp_redgreen.py` | 회귀 시험이 수정이 있을 때 통과하고 수정을 되돌리면 실패하는지 확인 | 0 RED_GREEN_OK, 1 그 외 |
| `hvp_gci.py` | 메시·시간 간격 3단의 관측 차수, 외삽값, GCI(수치 오차) | 0 기준 안, 1 초과·신뢰 불가 |
| `hvp_consensus.py` | 독립 방법들의 합의(E_n), 보수 경계 판정 | 0 합의·통과, 1 그 외 |
| `hvp_fatigue.py` | 레인플로, 평균응력 보정·Miner 손상, Paris 균열 성장, LEFM K 검사 | 0 통과, 1 미달 |
| `hvp_flow.py` | 레이놀즈 수, 마찰 계수(Colebrook·Haaland 대조), 압력 손실 | 0 통과, 1 초과 |
| `hvp_env.py` | 프로젝트 환경 감지(Nix, venv, conda), 데이터 환경 확인·생성, 데이터 도구 실행 | 0 정상, 2 환경 없음 |
| `hvp_ingest.py`* | MCAP·CSV → Parquet 표, 실행 메타, 해시 목록 | 0 |
| `hvp_query.py`* | 저장된 SQL 검사(metric, events) → 판정 증거, 즉석 SQL | 0 통과, 1 미달 |
| `hvp_mcp.py`* | 읽기 전용 MCP 서버(데이터 질의) | — |
| `hvp_brief.py` | 증거 전체로 판단 자료(HTML + 인용이 붙은 Markdown) 생성 | 0 생성 |
| `hvp_cite.py` | 값 하나의 출처·상태 보기(`get`), 보고서의 인용 검사와 출처 없는 숫자 찾기(`check`) | get 0/1, check 0 깨끗, 1 문제 |
| `selftest.py` | 위 도구들의 known-answer 자체 시험(임시 저장소에서) | 0 전부 통과 |
| `selftest_data.py`* | 데이터 계층의 known-answer 자체 시험(합성 MCAP) | 0 전부 통과 |

\* 표시 도구는 데이터 환경이 필요하다. `python3 $S/hvp_env.py run <도구> ...`로 실행한다(12절). 나머지는 표준 라이브러리만 쓴다.

**처음 쓰는 환경에서는 자체 시험부터 돌린다.** 검사기를 믿기 전에 정답을 아는 입력으로 확인한다는 skill의 원칙을 도구 자신에게도 적용한다.

```sh
python3 $S/selftest.py            # SELFTEST n/n passed 가 나와야 한다. -v 로 상세 출력
```

자체 시험이 확인하는 것: 물리량 키(`bolt_head_diameter_mm`, `fault_duration_s`)를 휘발성으로 오인하지 않음, 휘발성만 바뀐 증거는 EQUIVALENT, 두 기록 형식의 STALE 검출과 재생성 순서, 동시 세션 잠금·외부 프로세스 검출과 자기 명령 제외, worktree 안에서의 세션 종료, 남의 파일·대용량·원시 산출물 검출, 연결된 worktree에서의 훅 탐색, 미추적 입력 때문에 작업 트리와 스냅샷 결과가 갈리는 경우, 두 시드 일치를 결정론으로 오인하지 않음. 수정 루프 도구: 목표 미달로 내려간 판정을 REGRESSED로, 지운 증거를 LOST로, 기준 완화를 CRITERIA_CHANGED로, 지표와 어긋난 자기 보고 PASS를 INCONSISTENT로 잡음, 전이 의존과 층을 넘는 재실행 순서, 해시 기록 없는 의존, 작업 중 후보(수정 + 새 파일)를 스윕이 그대로 평가함, 비단조 응답, 번갈아 깨지는 두 검사와 반복 파라미터 감지, 대상 저장소를 바꾸지 않음.

에이전트 도구는 보통 명령마다 새 셸을 연다. 앞 명령에서 정한 변수는 남지 않는다. 세션 값(worktree 경로, 시작 HEAD)은 잠금 파일에 있으니 `hvp_session.py get`으로 다시 읽는다.

## 1. 세션 잠금과 격리

잠금은 `<git-common-dir>/hvp-sessions/<SID>.json`에 둔다. 커밋되지 않고 모든 worktree가 함께 본다. `SID`는 세션 동안 같은 값으로 정한다(예: `agent-20261019-1830`).

```sh
python3 $S/hvp_session.py start   --sid $SID          # 등록. 다른 잠금을 ACTIVE/STALE로 나열. ACTIVE가 있으면 exit 2
python3 $S/hvp_session.py signals --sid $SID          # 잠금, 시작 뒤 커밋, 최근 수정 파일, 외부 프로세스. 동시면 exit 2
python3 $S/hvp_session.py isolate --sid $SID --commit <pinned-commit>   # 전용 worktree + 브랜치 hvp/$SID, 경로 출력
cd "$(python3 $S/hvp_session.py get --sid $SID worktree)"               # 이후 모든 실행·수정은 여기서
python3 $S/hvp_session.py touch   --sid $SID <바꾼 경로...>              # 파일을 바꿀 때마다. heartbeat도 갱신
python3 $S/hvp_session.py end     --sid $SID          # 깨끗한 worktree만 제거하고 잠금 삭제. 변경이 남으면 거부(exit 1)
```

- **동시 세션 판정**: 다른 잠금의 heartbeat가 2시간 안이면 ACTIVE다. PID로 판정하지 않는다. 에이전트의 셸은 명령마다 바뀌어 PID가 세션 수명을 나타내지 못한다.
- **signals 읽기**:
  - [a] 시작 HEAD 뒤에 들어온 커밋이 있으면 동시 세션이다.
  - [b] 최근 수정 파일 중 자기 touched에 없는 것은 참고 신호다. 자기 편집을 touched에 안 적었을 수 있으니 확인한다.
  - [c] 이 worktree를 작업 디렉터리로 쓰는 다른 프로세스. 도구 자신, 그 조상, 자식, 같은 셸 아래 파이프 상대(`| head`)는 뺀다. 같은 에이전트의 백그라운드 작업은 보고되니 명령줄을 보고 판단한다. 분리 실행된 다른 세션은 PID 1 아래로 옮겨져도 잡힌다. `/proc`이 없으면 `lsof`를 쓰고, 둘 다 없으면 "unavailable"로 표시한다.
- **동시 세션이면 격리가 의무다**: `isolate` 뒤에만 실행·수정한다. 저장소 규칙이 worktree나 브랜치 생성을 막으면 진행하지 않고 사용자에게 격리 방법을 묻는다.
- **동시 세션이 없으면**: 현재 체크아웃을 쓴다. 시작 HEAD는 잠금에 기록돼 있다. 실행 뒤 HEAD가 바뀌었으면 그 결과는 "실행 중 입력 변경"으로 표시하고 판정에 쓰지 않는다.
- **종료**: 통합(병합, PR, 브랜치 정리)은 사용자가 정한 방식으로 먼저 한다. `end`는 `--force`를 쓰지 않는다. 변경이 남은 worktree와 그 잠금은 그대로 두어 다른 세션에 보이게 한다. STALE 잠금은 지우지 말고 보고한다.

## 2. 오라클 실행표와 도구별 패턴

**실행표(1단계 산출물)**

| 오라클 | 명령 | 환경 | 성공 토큰 | 보통 시간 | 대기 상한 | 증거 경로 |
| --- | --- | --- | --- | --- | --- | --- |
| 단위·픽스처 | `python3 -m <pkg>.test_<oracle>` | 시스템 Python | `OK` | 5 s | 30 s | 없음(단위) |
| 조립 간섭 | 아래 FreeCAD 드라이버 | FreeCAD | `RESULT_OK` | 4 min | 10 min | `<results>/clearance.json` |
| 구조 FE | `python3 <fe>/run_case.py` | CalculiX | 증거 status `PASS` | 3 min | 8 min | `<results>/fe_summary.json` |
| pre-push 스위트 | 훅이 돌리는 명령 그대로 | 저장소 지정 환경 | 마지막 줄 토큰 | 저장소마다 | 보통의 2배 | 훅 출력 |

- 성공 토큰은 도구 출력에서 실제로 확인한 문자열만 쓴다. 출력이 없으면 종료 코드 외에 판정 근거가 없다고 적는다.
- 대기 상한은 보통 시간의 약 2배로 잡는다. 감시가 아무 이벤트 없이 만료되면 같은 감시를 다시 걸기 전에 `ps`, 로그 끝, 출력 파일 수정 시각부터 본다.

**Python 스크립트가 실행되지 않을 때**

- `if __name__ == "__main__":` 가드가 있는 파일을 `exec`나 import로 부르면 본문이 돌지 않는다. `python3 path/to/script.py`로 실행하거나 `runpy.run_path(path, run_name="__main__")`을 쓴다.
- 패키지 안 모듈은 저장소 루트에서 `python3 -m pkg.module`로 실행한다. 경로를 점 표기로 바꿀 때 실제 디렉터리 이름을 저장소에서 확인한다.
- 형제 모듈 import가 깨지면, 해시로 고정된 소스(SKILL.md 7.6)는 고치지 않는다. 호출하는 쪽에서 `PYTHONPATH` 또는 `sys.path`로 해결한다.

**FreeCAD 헤드리스(freecadcmd)**

- 여러 줄 코드를 stdin이나 `-c`로 넘기면 콘솔 해석기가 깨진다. 드라이버 파일을 만들고, 한 줄짜리 `exec`로 부른다.
- 드라이버는 세션별 경로에 둔다. 고정 경로(`/tmp/drv.py`)는 동시 세션끼리 덮어쓴다.

```sh
DRV=${TMPDIR:-/tmp}/hvp-drv-$SID.py
cat > "$DRV" <<'E'
import runpy, sys, os, traceback
try:
    runpy.run_path(os.environ["T"], run_name="__main__"); print("RESULT_OK")
except SystemExit as e:
    print("RESULT_OK" if e.code in (0, None) else "RESULT_FAIL exit=%s" % e.code)
except BaseException:
    traceback.print_exc(); print("RESULT_FAIL")
sys.stdout.flush(); os._exit(0)
E
T=$PWD/<tests>/test_<name>.py timeout 900 freecadcmd -c "exec(open('$DRV').read())" 2>&1 | grep -E 'RESULT_|Error|assert'
```

- unittest는 성공해도 `SystemExit(0)`으로 끝난다. 드라이버가 이것을 성공으로 해석하게 한다(위 예).
- `os._exit(0)`는 FreeCAD가 종료 시 멈추는 것을 막는다. 판정은 종료 코드가 아니라 `RESULT_` 토큰으로 한다.
- OCC 거리 질의 일부는 특정 형상에서 segfault가 난다. 같은 호출을 반복하지 말고 질의를 바꾼다(oracle-catalog 24절).

**MCP·GUI 연동 CAD(Fusion 커넥터, FreeCAD MCP 등)**

- 이런 연동은 대화로 형상을 만들고 바꾸는 데 쓴다. 돌려받는 것은 명령 성공 상태나 뷰 스크린샷이고, 형상의 수치 검증이 아니다. 그래서 판정 도구가 아니라 2차 확인·작성 도구로 다룬다.
- 형상을 바꾼 뒤에는 STEP(또는 저장소가 쓰는 형식)으로 내보내고, 파일 해시를 증거에 기록한 다음, 그 파일에서 헤드리스 1차 오라클을 돌린다. 판정은 그 증거로만 한다(SKILL.md 6단계 5항).
- 내보낸 파일은 증거 입력이므로 `hvp_stale.py`가 볼 수 있게 해시 기록 형식으로 남긴다. 연동 도구 안에서만 존재하는 형상은 어떤 검사의 입력도 될 수 없다.
- 연동 도구가 주는 해석 결과(예: FreeCAD MCP의 FEM 최대 응력, 최대 변위)는 메시 수렴과 경계조건 확인 없이는 진단값이다. L3 판정에는 oracle-catalog 13절 절차를 쓴다.
- 스크린샷 피드백은 토큰을 많이 쓴다. 가능한 경우 텍스트 피드백 모드를 쓰고, 사람 검토용 렌더만 따로 저장한다.
- 실행 위치: 대부분 GUI가 필요해 CI 게이트가 될 수 없다. 1단계 실행표의 "환경" 열에 GUI 필요를 적는다.

**nix·conda 환경**

- 환경 빌드가 실패하면 먼저 같은 명령을 한 번 다시 실행해 일시적 실패인지 본다(의존 경로 다운로드 실패는 재시도로 풀린다).
- 두 번째도 실패하면 로그의 첫 오류를 읽고 원인을 기록한다. 환경 밖에서 훅을 돌리거나 `--no-verify`로 우회하지 않는다.

## 3. 결정론 이중 실행

```sh
python3 $S/hvp_determinism.py --cmd "python3 <tools>/<oracle>.py" --out <results>/<oracle>.json \
    --seeds 1,7,42,1234 [--inputs <미추적 입력...>] [--grep <tools>] [--keep]
```

- 각 값마다 임시 worktree를 만들어 같은 커밋을 돌리고, 첫 값의 결과와 나머지를 의미 비교한다. 정본 증거는 건드리지 않는다.
- **시드 개수**: 싼 검사는 4개 이상, 비싼 검사는 최소 2개. 원소가 적은 set은 서로 다른 두 시드에서 같은 순서가 나올 수 있다(예: 5원소 문자열 set의 첫 원소가 시드 1과 7에서 같고 42에서 다를 수 있다). 그래서 결과는 "n values agree"로 나오고, 문서에도 "시드 n개 일치"로 적는다. "결정론 확인"이라고 쓰지 않는다.
- **다른 축**: `--axis OMP_NUM_THREADS --seeds 1,8`, 또는 검사기가 읽는 난수 seed 환경 변수 이름을 `--axis`로 준다.
- **미추적 입력**: 깨끗한 worktree에는 미추적·무시 파일(빌드된 CAD, 내려받은 데이터, 로컬 설정)이 없다. 검사가 그런 파일을 읽으면 실행이 실패(RUN_FAILED)하거나, 더 나쁘게는 입력 없이 조용히 다른 결과를 낸다. `--inputs`로 복사하고 그 목록을 실행 기록에 남긴다. 목록을 모르면 먼저 빈 목록으로 돌려 로그의 "파일 없음" 오류에서 찾는다.
- **결과 읽기**:
  - `RESULT DIFFERENT`: 검사기 버그. 정렬된 순회로 고치고 known-answer 픽스처와 이 실행을 다시 통과시킨다. 그 전까지 해당 판정은 BLOCKED다.
  - `RESULT RUN_FAILED`: 일치 여부를 말할 수 없다. 로그부터 본다.
- **코드 확인(`--grep`)**: 판정 경로에서 set·dict 순회 결과로 대표 항목·첫 항목·동률 승자를 고르는 코드 후보를 나열한다. 일치해도 이런 코드가 판정 경로에 있으면 고친다. 흔한 원인은 `next(iter(set))`, set에서 만든 dict의 첫 항목, 키가 부족한 동률 정렬, 정렬하지 않은 `listdir`·`glob` 결과다.
- 너무 오래 걸리는 검사는 축소 입력으로 돌리고 그 사실을 적는다.

## 4. 낡은 증거 전수 검색과 의미 비교

```sh
python3 $S/hvp_stale.py --file <바뀐 소스>      # 수정 전: 이 파일을 기록한 증거(경로 또는 HEAD 해시)
python3 $S/hvp_stale.py                         # 수정 후: 전수 검색. STALE/MISSING 목록과 재생성 ORDER
python3 $S/hvp_compare.py git:HEAD:<results>/x.json <results>/x.json   # 재생성 전후 의미 비교
```

**전수 검색과 재생성**

- 인식하는 기록 형식은 네 가지다: `{"<path>": "<sha256>"}`, `{"<path>": {"sha256": ...}}`, `{"path": ..., "sha256": ...}`, `{"x_path": ..., "x_sha256": ...}`. 키 이름이 다르면 `--path-key`, `--hash-key`로 바꾼다.
- ORDER 순서대로 재생성한다. 다른 증거를 입력으로 기록한 증거는 그 증거 뒤에 온다.
- 재생성 뒤 STALE 0이 될 때까지 다시 돌린다. 증거가 증거를 기록하면 한 번에 다 잡히지 않는다.
- MISSING은 증거가 저장소 루트가 아닌 다른 기준으로 경로를 적었거나 실제로 파일이 없는 경우다. 확인한 뒤 보고한다.
- 해시를 기록하지 않는 의존(파일 이름만 읽는 생성기)은 이 검색에 보이지 않는다. 저장소의 manifest나 gate가 담당하는지 확인한다.

**의미 비교 규칙**

- 휘발성 키는 키 이름이 정확히 일치할 때만 뺀다(타임스탬프, `elapsed_s`, 호스트 이름, 임시 경로, `volatile` 아래 전부). 이름 일부로 빼면 `bolt_head_diameter_mm`, `fault_duration_s` 같은 물리량 변화를 놓친다.
- `commit`, `head`, `sha256`, `*_sha256` 같은 ID 키는 두 값이 모두 해시 모양일 때만 뺀다. `head`가 실제 값(볼트 머리 형식)이면 비교한다.
- 수치는 상대 1e-9, 절대 1e-12로 비교한다. 0 근처 잔차(1e-13 대 3e-13)는 변화로 읽지 않는다. 오라클이 허용치를 정했으면 `--rel`, `--abs`로 그 값을 쓴다.
- 저장소 고유의 휘발성 키는 `--ignore-keys`로 더한다.
- EQUIVALENT여도 출력의 "ignored volatile keys" 목록을 보고서에 함께 적는다. 목록에 물리량이 보이면 무시 목록이 잘못된 것이다. "hash-like values changed" 필드는 사람이 확인한다.

## 5. 스테이지 직전 점검

```sh
cd "$(python3 $S/hvp_session.py get --sid $SID worktree)"
python3 $S/hvp_stagecheck.py --sid $SID [--max-mb 25]
```

- [1] 남의 파일, [2] 크기 상한 초과, [3] 원시 해석 산출물(메시·결과 덤프·로그)이 비어 있고, [4]의 커밋이 모두 자기 것이고, [5]가 브랜치 이름일 때만(VERDICT CLEAN) 커밋한다.
- [1]의 기준은 잠금의 touched 목록이다. 파일을 바꿀 때마다 `hvp_session.py touch`로 적는다.
- 원시 산출물 패턴은 `.gitignore`에 추가하고, 그 변경도 경로 지정으로 커밋한다. 저장소 규칙이 다르면 `--raw-pattern`, `--max-mb`로 맞춘다.
- 스테이지는 `git add -- <path> ...`로만 한다. `git add -A`나 `git add .`은 쓰지 않는다.
- 파일 하나를 되돌릴 때는 `git restore --source=<rev> -- <path>`를 쓴다. 경로 없는 `git checkout <rev> --`는 HEAD를 분리시킨다. 이미 분리됐다면 `git switch <branch>`로 돌아가고, 추적 파일 충돌이 나면 재생성 가능한 파일만 스크래치로 옮긴 뒤 돌아간다.

## 6. 커밋 스냅샷 검증

```sh
python3 $S/hvp_snapshot.py --show-hook                                 # 훅 위치와 본문: 훅이 실제로 돌리는 명령 확인
python3 $S/hvp_snapshot.py --cmd "<훅이 돌리는 명령 그대로, 같은 환경>"     # 예: "nix develop -c <suite>"
```

- 인덱스로 임시 커밋 객체를 만들어 깨끗한 worktree에서 명령을 돌린다. ref는 움직이지 않는다. 미추적 파일 덕에 작업 트리에서만 통과하는 경우가 여기서 실패로 드러난다.
- 훅 위치는 `core.hooksPath`를 먼저 보고, 없으면 `git rev-parse --git-path hooks`로 찾는다. 연결된 worktree에서는 `.git`이 파일이라 `.git/hooks`를 직접 쓰면 실패한다.
- 훅 스크립트를 직접 부를 때는 git이 넘기는 인자와 stdin을 맞춘다. 스냅샷 안에서는 `$HVP_HOOKS_DIR`, `$HVP_SNAPSHOT_COMMIT`을 쓸 수 있다.

  ```sh
  python3 $S/hvp_snapshot.py --cmd 'echo "refs/heads/<b> $HVP_SNAPSHOT_COMMIT refs/heads/<b> $(git rev-parse origin/<b>)" | "$HVP_HOOKS_DIR/pre-push" origin <url>'
  ```

- 실패하면 원인 유형(SKILL.md 6단계 분류)을 정하고 고친다. 인덱스를 다시 만들고 반복한다. push는 여기서 통과한 다음에만 한다.
- 커밋을 여러 묶음으로 나눌 때는 묶음마다 확인한다. 출력의 커밋 객체를 다음 묶음의 `--parent`로 주면 순서대로 확인할 수 있다.

## 7. 증거 무결성 규칙

판정이 어느 형상의 것인지 보증하기 위한 규칙이다(SKILL.md 8단계).

1. **스테이지 직전 점검**: 5절. 스테이지된 목록이 자기 변경 목록과 같아야 하고, 크기 상한(기본 25 MB, 저장소 규칙 우선), 원시 산출물 혼입, 낯선 커밋, 분리된 HEAD가 없어야 한다.
2. **되돌리기**: 파일 단위로 `git restore --source=<rev> -- <path>`. 경로 없는 checkout을 쓰지 않는다.
3. **커밋 스냅샷 검증**: 6절. 작업 트리 PASS는 커밋 스냅샷 PASS가 아니다. 통과한 뒤에만 커밋·push한다.
4. **우회 금지**: 훅 생략(`--no-verify`), 게이트 목록 축소, 기대값 수정으로 통과시키지 않는다.
5. **검토하지 않은 변경**: 자기가 만들지 않았거나 검토하지 않은 변경의 커밋 여부를 사용자에게 물을 때는 묶음별 검토안(경로 묶음, 변경 내용, 해시 의존, 위험)을 먼저 내고, 선택지에 추천 표시를 붙이지 않는다.
6. **원격 작업**: push, PR 생성·병합, 원격 삭제는 사용자의 명시적 승인이 있을 때만 한다.
7. **제작 패키지가 HEAD에 묶일 때**: 패키지와 그 검증 결과는 추적하지 않는 출력 디렉터리에 두거나, 마지막 게이트가 자기 출력을 판정 입력에서 빼게 한다. 그렇지 않으면 검증 결과를 커밋할 때마다 패키지가 다시 낡는다.

## 8. 제작 착수 준비 절차

목표는 "사람이 재고를 세고 첫 부품을 만들고, 그 결과로 설계 판정을 확인할 수 있는 상태"다(SKILL.md 9단계). 양식은 templates.md 9a·9b·9c절.

1. **부품별 세 질문 판정표(9a)**: BOM 각 행에 CAD 솔리드 유무, 타당성·조립·동작 판정과 그 근거 검사, 상태를 적는다. PASS_BELOW_TARGET과 PASS_BY_MODEL_CHANGE는 따로 표시한다.
   - BOM 행, CAD 솔리드(이름·해시), 그 솔리드를 입력으로 쓴 검증 목록을 대조한다(oracle-catalog 28절). CAD 솔리드가 없는 부품이나 검증 대상에서 빠진 하위 시스템이 있으면 "제작 가능"이라고 쓰지 않는다. "모델 없음 N건, 검증 없음 M건"으로 쓴다.
   - 미결 공칭–실측 불일치(7a)가 걸린 판정은 그 표에 함께 표시한다.
2. **조립 순서와 공구 접근**: 분해 경로 탐색(oracle-catalog 23절)의 역순으로 조립 순서를 내고, 각 단계의 체결 공구 진입 경로와 필요한 지그를 적는다. 탐색이 다루지 못하는 부분(회전 삽입, 유연 부품)은 수동 확인 항목으로 둔다.
3. **승인 구분**: 승인 없이 할 수 있는 작업(무가공 재고 조사, 치수 측정, 문서 작업)과 각각 승인이 필요한 작업(구매, 가공·절단, 통전, 회전체 시운전)을 나눈다.
4. **L5 실측 패킷**: "물리 시험 NOT_RUN"으로 끝내지 않고 바로 쓸 수 있는 양식을 낸다.
   - **초품 검사표(9b)**: CAD에서 기능 치수(맞춤, 축간, 베어링 자리, 체결 위치, 여유가 작은 간섭 쌍)를 골라 공칭, 공차, 게이지, 측정 방법을 적는다.
   - **측정 기록 양식(9c)**: 항목, 게이지, 게이지 %GRR 전제, 측정값, 측정자, 시각, 기록 JSON 경로. %GRR을 모르는 게이지로 낸 판정은 "판정 불가"다.
   - **기능 시험**: 의도 동작을 실물로 확인하는 최소 시험(손 구동 행정, 무부하 회전, 소재 투입)을 lockout 조건과 함께 적는다.
   - **중단 기준은 시험 전에 정한다**: 통전·회전 시험마다 즉시 멈출 기준(전류, 표면 온도, 소음·진동, 이상 냄새, 행정 걸림, 시간 상한)과 멈추는 방법(비상 정지, 전원 차단 위치)을 시험표에 적는다. 시험 중에 기준을 정하면 "조금만 더"로 넘어가기 쉽다.
   - **실측이 판정과 어긋나면**: 9d 실패 분석 양식으로 원인을 적고(누구 탓이 아니라 어느 모델·입력·절차가 틀렸는지), 해당 판정과 모델 입력을 불일치 대장(7a)과 수정 루프(SKILL.md 7.1)로 넘긴다.
   - **재판정 연결**: 측정 항목마다 그 값을 입력으로 쓰는 판정(간섭 쌍, 안전율, 열 계수)을 적고, 실측값이 들어오면 그 판정을 다시 돌린다. 저장소에 연결 장치가 없으면 재실행 명령 목록을 적는다. 실측값은 불일치 대장(7a)에도 들어간다.
5. **제작 패키지**: 7절 7항.

## 9. 설계 수정 루프

SKILL.md 7단계의 명령 순서다. 이슈 하나(근본 원인 하나)에 대해 아래를 한 바퀴 돈다. `$SID`는 세션 ID, `$I`는 이슈 ID(예: `I-clear-cam-1`), `$T`는 세션 스크래치 디렉터리(저장소 밖)다.

**9.1 열기**

```sh
python3 $S/hvp_session.py start --sid $SID          # 동시 세션이면 isolate 후 그 worktree에서
python3 $S/hvp_fixloop.py open --sid $SID --issue $I --checks <대상 검사 id,...> \
    --cause "<원인 한 줄, 수치 포함>" [--budget 3]   # 시작 커밋이 기록된다
```

- 대상 검사 id는 증거 파일 경로(목록 항목이면 `경로#id`)다. `hvp_verdicts.py --json`의 키와 같다.
- 증거를 git이 추적하지 않으면(무시된 결과 디렉터리) 지금 스냅샷을 떠 둔다. 이후 비교는 `--before dir:$T/before-$I --after dir:.`로 한다.

  ```sh
  mkdir -p $T/before-$I && tar -cf - <결과 디렉터리> | tar -xf - -C $T/before-$I
  ```

**9.2 원인 가설 확인(수정 전)**

- 7.2의 예측("p를 Δ 바꾸면 m이 약 Δm")을 두 점 스윕으로 확인한다.

  ```sh
  python3 $S/hvp_sweep.py --cmd "<빌더 --p {value}> && <1차 오라클>" --out <증거> --metric <m> \
      --values <현재값>,<현재값+Δ> --report $T/probe-$I.json
  ```

- 방향이나 크기가 예측과 크게 다르면(부호가 반대이거나 크기가 절반 미만·두 배 초과) 가설을 버린다.
- 빌더가 파라미터를 명령줄로 받지 않으면, 파라미터 파일을 고치는 한 줄 명령을 `--cmd` 앞에 둔다(예: `python3 -c "..." && ...`). worktree는 임시라 작업 트리는 바뀌지 않는다.

**9.3 후보와 크기**

```sh
python3 $S/hvp_impact.py <후보가 바꿀 경로...>       # 후보마다: 재실행 목록과 UNTRACKED_DEPENDENCY
python3 $S/hvp_sweep.py --cmd "..." --out <증거> --metric <m> --values v1,v2,v3,v4,v5 \
    --report $T/sweep-$I-<후보>.json
```

- 출력의 FIRST_LIMIT, FIRST_TARGET, MONOTONIC, TRANSITION을 1d 비교표에 옮긴다. 경계를 좁혀야 하면 값을 촘촘히 늘리지 말고 `--refine N`으로 상태가 바뀌는 구간만 이분한다(BOUNDARY 줄이 그 결과다). 값 고르기와 해석은 oracle-catalog 31절에 있다.
- 부작용이 예상되는 지표(30절 표의 "흔한 부작용")도 같은 값으로 스윕해 둔다.
- 후보 선택이 사용자 결정 항목(SKILL.md 7.5)이면 여기서 멈추고 비교표를 낸다.

**9.4 적용**

```sh
python3 $S/hvp_stale.py --file <바꿀 파일>           # 해시 고정 확인
# 빌더·파라미터 파일 수정(단일 출처)
python3 $S/hvp_session.py touch --sid $SID <바꾼 경로...>
```

**9.5 재검증**

```sh
python3 $S/hvp_stale.py                              # ORDER대로 재생성, STALE 0까지 반복
# IMPACTED 목록(앞 층부터) + 1차 층 L0–L2 전체 + 부작용 검사 재실행
python3 $S/hvp_verdicts.py --before git:<시작 커밋> --expect-fixed <대상 id> --json > $T/v-$I-<n>.json
python3 $S/hvp_verdicts.py --before git:<시작 커밋> --expect-fixed <대상 id>   # 사람이 읽는 출력
```

- 시작 커밋은 `hvp_fixloop.py show --sid $SID --issue $I`의 `start_commit`이다. 모든 시도를 같은 기준에서 비교한다.
- 출력 읽기:
  - REGRESSED·LOST가 있으면 그 시도는 수락하지 않는다. 고칠 대상이 늘어난 것이다. 같은 이슈의 다음 시도에서 함께 다루거나, 원인이 다르면 새 이슈를 연다.
  - CRITERIA_CHANGED는 기준을 바꾼 증거가 있다는 뜻이다. 사용자가 승인한 요구 변경일 때만 `--allow-criteria`를 쓰고 그 승인을 보고에 적는다.
  - INCONSISTENT는 검사기가 자기 지표와 다른 상태를 썼다는 뜻이다. 검사기 결함으로 분류한다(SKILL.md 6단계).
  - MARGIN_DROP은 원인과 남은 여유를 보고에 적는다.
- 개선 수정(7.14)은 `--strict-margin`을 더한다.
- 바꾼 검사기는 3절 결정론 이중 실행을 다시 한다. ACCEPTABLE인 형상에서만 2차 도구를 돌린다.

**9.6 시도 기록과 루프 제어**

```sh
python3 $S/hvp_fixloop.py attempt --sid $SID --issue $I --change "<무엇을 바꿨나>" \
    --params <p>=<값> ... --verdicts $T/v-$I-<n>.json      # 기록 후 바로 CONTINUE/STOP 판정
```

- CONTINUE이고 판정이 NOT_ACCEPTABLE이면 9.2(가설) 또는 9.3(후보)으로 돌아간다. 같은 파라미터 값을 다시 쓰지 않는다.
- STOP이면 더 고치지 않는다. 사유별 다음 행동은 SKILL.md 7.10 표에 있다. COUPLED는 oracle-catalog 31절의 격자 스윕으로 넘어간다.
- 실패한 시도의 변경은 파일 단위로 되돌린다(`git restore --source=<시작 커밋> -- <path>`). 그 시도로 재생성한 증거도 함께 되돌린다. 남겨 둔 채 다음 시도를 덧붙이지 않는다.

**9.7 반증, 기록, 커밋, 닫기**

- 반증(SKILL.md 7.9): 고친 판정의 입력 극단(공차 Monte Carlo 극단값, 표본 사이 위상, 다른 시드)으로 같은 오라클을 돌린다. 찾은 실패는 테스트 케이스로 추가한다.
- 회귀 시험 확인:

  ```sh
  python3 $S/hvp_redgreen.py --test "<그 테스트 명령>" --fix <수정 경로...> --base <시작 커밋> --expect "<그 결함의 실패 메시지 일부>"
  ```

  RED_GREEN_OK가 아니면 테스트를 고친다. TEST_DOES_NOT_DETECT는 테스트가 결함을 보지 못한다는 뜻이고, RED_FOR_OTHER_REASON은 수정 없이 실패하지만 다른 이유(입력 파일 없음 등) 때문이라는 뜻이다.
- 기록: 1c 수정 보고, 1d 비교표, 7b 시도 이력(`hvp_fixloop.py show --sid $SID --issue $I --md`), 7a 불일치 대장, 형상이 바뀐 부품의 검증 상태 하향(VALIDATED → VERIFIED)과 L5 재측정 목록(9c).
- 커밋: 5·6절(스테이지 점검, 스냅샷 검증). 근본 원인 하나당 커밋 하나다.
- 닫기: `python3 $S/hvp_fixloop.py close --sid $SID --issue $I --result fixed|escalated|abandoned --note "<요약>"`.
- 시도 기록은 세션 잠금 옆(`<git-common-dir>/hvp-sessions/$SID.fixlog.json`)에 있다. `hvp_session.py end`가 지우지 않지만 저장소에 커밋되지도 않는다. 남길 내용은 7b로 옮긴다.

## 10. 보고와 인용

SKILL.md 12단계의 절차다. 목적은 사용자가 에이전트의 말을 믿지 않고도 모든 숫자의 뜻과 출처를 확인하고, 필요하면 직접 다시 만들어 볼 수 있게 하는 것이다.

**값 하나 확인하기**

```sh
python3 $S/hvp_cite.py get results/wall.json /metrics/safety_factor/value [--rerun]
```

```
VALUE    2.125
CRITERIA limit 1.0, target 1.5, sense min
SCOPE    "housing wall, static closing load, room temperature"
ASSUMPTI ["wall left = envelope - 2*gap", ...]
LIMITATI ["no fatigue", "no creep", "no print orientation effect"]
CRIT_SRC {"safety_factor": "customer spec (README)"}
CITE     `ev:results/wall.json#/metrics/safety_factor/value`
FILE     results/wall.json  sha256 …
COMMIT   <sha> <날짜> <커밋 제목>
COMMAND  python3 tools/check_wall.py
INPUT    OK      params/cam.json
RERUN    REPRODUCED (file 2.125, regenerated 2.125)
```

- 맨 위 세 줄(SCOPE, ASSUMPTIONS, LIMITATIONS)이 그 숫자의 뜻이다. "not recorded"이면 숫자는 있어도 뜻을 알 수 없다. 검사기에 그 필드를 기록하는 작업을 먼저 제안한다(oracle-catalog 29절).
- `INPUT STALE`이 보이면 그 값은 지금 설계의 값이 아닐 수 있다. 보고에 쓰기 전에 재생성한다(4절).
- `RERUN NOT_REPRODUCED`면 파일의 값을 기록된 명령이 만들지 않았다. 손으로 고쳤거나, 코드가 바뀌었거나, 입력이 빠졌다. 그 값은 보고에 쓰지 않는다. 미추적 입력이 필요하면 `--inputs`로 넘긴다.
- 사용자에게도 이 명령을 알려 준다. 에이전트가 옮긴 숫자를 사용자가 한 줄로 확인할 수 있다.

**보고서 쓰기**

- 숫자에는 사람이 읽는 맥락 한 줄을 붙이고, 출처를 인용한다. 인용은 세 형식이다.
  - 인라인: ``하우징 벽 SF 1.32(정적 닫힘 하중, 피로 미검토) `ev:results/wall.json#/metrics/safety_factor/value` ``
  - 각주(읽기 편한 형식): `SF 1.32[^sf]`와 문서 끝의 `[^sf]: ev:results/wall.json#/metrics/safety_factor/value`
  - 사양·BOM 줄: ``M3x10 볼트 4개 `ev:bom.csv#L3` ``. 쓴 숫자가 그 줄에 있어야 한다.
- 기준을 같은 문장에 써도 된다(`SF 1.32 (목표 1.5) `ev:…``). 검사는 문장 안의 숫자 중 하나가 인용 값과 같은지를 본다.
- 보이는 자릿수까지만 맞으면 된다(1.3241을 1.32로 쓰는 것은 일치).
- 에이전트가 계산한 값은 근거 상태와 계산식을 쓰고, 계산 입력의 인용을 같은 줄에 둔다.
- 도구 출력은 원문 줄을 코드 블록으로 함께 둔다. 코드 블록 안과 제목 줄은 검사하지 않는다.
- "직접 확인" 절: 증거 파일 목록, 재현 명령, 렌더 경로, `hvp_cite.py get` 사용 예.

**보고서 검사**

```sh
python3 $S/hvp_cite.py check docs/verification/2026-10-22-report.md --rerun
```

| 표시 | 뜻 | 할 일 |
| --- | --- | --- |
| `MISMATCH` | 문장의 어떤 숫자도 인용 값과 맞지 않음, 또는 인용한 상태어가 문장에 없음 | 증거를 다시 읽고 고친다 |
| `MISSING` | 파일, 포인터, 줄, 각주 정의가 없음 | 경로를 고치거나 증거를 저장소로 가져온다 |
| `NO_CONTEXT` | 인용한 증거에 범위·가정·한계가 없음 | 검사기가 기록하게 하거나, 보고에 "맥락 미기록"을 밝힌다 |
| `UNCITED` | 단위가 붙은 숫자, 여유·비율·계수·확률 숫자에 출처 없음 | 인용하거나 추정으로 표시 |
| `UNCITED_JUDGMENT` | "충분", "문제없음", "safe" 같은 판단에 출처 없음 | 판단의 근거를 인용하거나 문장을 상태와 근거로 바꾼다 |
| `NOT_REPRODUCED` | 기록된 명령이 다른 값을 만듦 | 재생성하고 보고를 고친다 |

- 정말 출처가 필요 없는 숫자만 남기고 `--allow-uncited`를 쓴다. 쓴 경우 보고에 그 사실을 적는다.
- 인용 검사가 통과했다는 것은 보고와 증거 파일이 일치한다는 뜻이다. 증거 자체가 맞는지는 `--rerun`의 REPRODUCED와 검사기 known-answer 시험으로 본다. 보고에서 둘을 섞어 쓰지 않는다.
- 채팅으로만 보고할 때도 같은 형식으로 쓴다.

## 11. 판단 자료

SKILL.md 12단계의 "판단 자료를 만들어 보여 준다"의 절차다.

```sh
python3 $S/hvp_brief.py --title "<프로젝트 이름>" [--glob 'results/**/*.json'] [--decisions verification/decisions.json]
python3 $S/hvp_cite.py check docs/verification/brief.md --rerun     # 판단 자료 자체 검사
```

- 출력은 `docs/verification/brief.html`(읽는 판)과 `brief.md`(인용이 붙은 판)다. `--out-dir`로 바꿀 수 있다. 커밋할지는 사용자가 정한다. 커밋하지 않으면 `.gitignore`에 넣기를 제안한다.
- 터미널 출력의 "needing attention" 목록이 판단 자료의 "먼저 볼 것"과 같다. 사용자에게는 이 목록과 HTML 경로를 준다.
- 판단 자료를 읽는 순서:
  1. 먼저 볼 것
  2. 결정 대기
  3. 커버리지의 "검사 없음" 칸
  4. 검사별 카드의 "보지 않은 것"과 입력 상태
  5. 설계 입력 표: 어떤 파라미터가 어떤 판정을 움직이는지
- 판정 표시는 기록된 status와 지표에서 유도한 status 중 더 나쁜 쪽이다. 둘이 다르면 INCONSISTENT로 표시하고 기록된 값도 함께 보여 준다. 예를 들어 불확도를 넣으면 기록된 PASS가 목표 미달로 바뀔 수 있다.
- 판단 자료의 인용 검사에서 나오는 NO_CONTEXT, NO_COMMAND는 판단 자료의 결함이 아니라 증거의 빈 곳이다. 그 검사기에 맥락·생성 명령을 기록하는 작업을 제안한다.
- 결정 파일의 결정 문구에 있는 숫자는 근거가 아니다. 근거는 `evidence`의 `ev:` 참조다. 문구 안의 추정치에는 "(추정)"을 붙인다. 인용 검사는 그것을 ESTIMATE로 따로 센다.

## 12. 데이터 계층: 환경과 도구

oracle-catalog 41절의 절차다.

**환경**

```sh
python3 $S/hvp_env.py detect      # 프로젝트 표지(flake.nix, .venv, requirements, environment.yml)와 데이터 python
python3 $S/hvp_env.py check       # 잠금의 최상위 고정 버전과 일치하는 python이 있는지. --strict: 잠금 전체
python3 $S/hvp_env.py setup       # 잠금대로 공유 venv 생성: ~/.cache/hvp/venv-py<버전>-<잠금 해시> (저장소 밖)
python3 $S/hvp_env.py run hvp_query.py --tables
```

- 데이터 python은 다음 순서로 찾는다. 셋 중 잠금의 최상위 고정 버전과 일치하는 첫 번째를 쓴다.
  1. `$HVP_PY`
  2. 현재 인터프리터
  3. 이 잠금의 공유 venv
- **Nix 프로젝트**:
  - 프로젝트 검사는 지금처럼 `nix develop -c <cmd>`로 돌린다.
  - 데이터 도구는 `env/flake.nix`의 devShell을 프로젝트 flake에 합친다. 그 셸이 python을 제공하고, shellHook이 같은 잠금으로 venv를 맞춘 뒤 `HVP_PY`를 내보낸다. 그래서 Nix 경로와 venv 경로는 같은 버전을 쓴다.
  - 순수 Nix(`python3.withPackages`)로 바꾸면 버전이 nixpkgs를 따라가 잠금과 어긋날 수 있다. 바꾼다면 잠금도 그 버전으로 다시 만든다.
- **venv 프로젝트**: 프로젝트 venv에 데이터 패키지를 섞지 않는다. 버전 충돌이 나기 쉽다. 공유 venv를 쓰고 `hvp_env.py run`으로 실행한다.
- **잠금을 바꿀 때**: `env/requirements-data.in`만 고친 뒤 기준일을 2주 이상 지난 날짜로 정해 다시 해석한다.

  ```sh
  python3 -m venv /tmp/lockenv && /tmp/lockenv/bin/pip install --uploaded-prior-to <YYYY-MM-DD>T00:00:00Z -r env/requirements-data.in
  /tmp/lockenv/bin/pip freeze   # 머리말 3줄(기준일, 해석 python, 설치 명령)과 함께 requirements-data.lock에 쓴다
  ```

  그다음 `selftest_data.py`를 통과시킨다. 최상위만 고정하면 간접 의존성이 바뀌어 깨진다. 실제로 `mcp 1.12.4`는 최신 `pydantic`과 import 단계에서 깨졌다.

**수집과 판정**

```sh
python3 $S/hvp_env.py run hvp_ingest.py mcap data/raw/*.mcap
python3 $S/hvp_env.py run hvp_ingest.py csv rig/bench-03.csv --topic bench/motor --run-id bench-03 --time-col t --time-unit ms
python3 $S/hvp_env.py run hvp_query.py --tables                       # 뷰, 열, 실행별 행 수
python3 $S/hvp_env.py run hvp_query.py --sql "SELECT run_id, max(current_A) FROM drive__motor GROUP BY 1"
python3 $S/hvp_env.py run hvp_query.py checks/motor_peak.json         # 판정 증거 results/<id>.json
python3 $S/hvp_cite.py get results/motor-peak.json /metrics/peak_current_A/value --rerun --inputs data/tables data/runs
```

- 표 이름은 주제에서 `/`를 `__`로 바꾼 것이다(`/drive/motor` → `drive__motor`).
- `INPUT STALE`이 보이면 기록을 다시 수집한 것이다. 검사를 다시 돌린다.
- `SKIP` 줄은 해석하지 못한 채널이다. ros2/cdr 메시지는 `mcap-ros2-support`를 잠금에 더해야 해석된다. 건너뛴 채널은 `runs/<run>.json`에 남는다.
- `data/tables`를 커밋하지 않으면 재실행할 때 `--inputs data/tables data/runs`를 준다.
- 원본 `.mcap`은 `hvp_stagecheck.py`가 원시 산출물로 막는다.

**MCP 등록(Claude Code)**

프로젝트 루트의 `.mcp.json`:

```json
{"mcpServers": {"hvp-data": {"command": "python3",
  "args": ["<skill>/scripts/hvp_env.py", "run", "hvp_mcp.py", "--data", "data"]}}}
```

- 도구: `list_runs`, `schema`, `query`, `series`, `run_check`, `cite`
- 서버는 시작할 때 표를 읽으므로, 다시 수집한 뒤에는 서버를 다시 띄운다.
- MCP 답을 보고에 옮길 때는 답에 붙은 `reproduce` 명령을 함께 적는다. 판정은 저장된 검사로만 낸다.

**자체 시험**: 처음 쓰는 환경에서 `python3 $S/hvp_env.py run selftest_data.py`. 합성 MCAP으로 수집, 지표, 사건, 재실행, STALE, MCP 읽기 전용, 판단 자료 연동을 확인한다.

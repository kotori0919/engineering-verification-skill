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
| `selftest.py` | 위 도구들의 known-answer 자체 시험(임시 저장소에서) | 0 전부 통과 |

**처음 쓰는 환경에서는 자체 시험부터 돌린다.** 검사기를 믿기 전에 정답을 아는 입력으로 확인한다는 skill의 원칙을 도구 자신에게도 적용한다.

```sh
python3 $S/selftest.py            # SELFTEST n/n passed 가 나와야 한다. -v 로 상세 출력
```

자체 시험이 확인하는 것: 물리량 키(`bolt_head_diameter_mm`, `fault_duration_s`)를 휘발성으로 오인하지 않음, 휘발성만 바뀐 증거는 EQUIVALENT, 두 기록 형식의 STALE 검출과 재생성 순서, 동시 세션 잠금·외부 프로세스 검출과 자기 명령 제외, worktree 안에서의 세션 종료, 남의 파일·대용량·원시 산출물 검출, 연결된 worktree에서의 훅 탐색, 미추적 입력 때문에 작업 트리와 스냅샷 결과가 갈리는 경우, 두 시드 일치를 결정론으로 오인하지 않음.

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
- 형제 모듈 import가 깨지면, 해시로 고정된 소스(SKILL.md 7.3)는 고치지 않는다. 호출하는 쪽에서 `PYTHONPATH` 또는 `sys.path`로 해결한다.

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
   - **재판정 연결**: 측정 항목마다 그 값을 입력으로 쓰는 판정(간섭 쌍, 안전율, 열 계수)을 적고, 실측값이 들어오면 그 판정을 다시 돌린다. 저장소에 연결 장치가 없으면 재실행 명령 목록을 적는다. 실측값은 불일치 대장(7a)에도 들어간다.
5. **제작 패키지**: 7절 7항.

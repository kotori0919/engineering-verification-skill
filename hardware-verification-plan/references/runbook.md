# 실행 런북

SKILL.md 1·6·7·8단계에서 쓰는 명령과 도구별 실행 패턴. 저장소에 같은 일을 하는 스크립트가 있으면 그것을 쓴다. 아래 명령은 셸에 바로 붙여 쓰는 짧은 형태이고 저장소에 새 파일을 만들지 않는다. 경로, 파일 이름, glob은 모두 예시이며 저장소에 맞게 바꾼다. 명령은 bash 기준이며 Linux와 macOS에서 같이 돈다.

## 목차

0. 공통 머리줄
1. 세션 잠금 파일과 격리
2. 오라클 실행표와 도구별 패턴
3. 결정론 이중 실행
4. 낡은 증거 전수 검색과 의미 비교
5. 스테이지 직전 점검
6. 커밋 스냅샷 검증

---

## 0. 공통 머리줄

에이전트 도구는 보통 명령마다 새 셸을 연다. 앞 명령에서 정한 변수는 다음 명령에 남지 않는다. 그래서 세션 변수는 잠금 파일에 적어 두고, 아래 블록을 쓸 때마다 이 머리줄을 먼저 붙인다.

```sh
SID=<session-id>                                   # 예: agent-20261019-1830. 세션 동안 같은 값
L=$(git rev-parse --path-format=absolute --git-common-dir)/hvp-sessions; LOCK=$L/$SID.json
lk() { python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],""))' "$LOCK" "$1"; }   # lk worktree, lk start_head
```

`--git-common-dir`는 연결된 worktree 안에서도 같은 경로를 돌려준다. 그래서 어느 worktree에서 실행해도 같은 잠금 파일을 본다.

## 1. 세션 잠금 파일과 격리

잠금은 `<git-common-dir>/hvp-sessions/`에 둔다. 커밋되지 않고 모든 worktree가 함께 본다.

**시작할 때: 다른 잠금 확인 후 등록**

```sh
# (0절 머리줄)
mkdir -p "$L"; NOW=$(date +%s)
for f in "$L"/*.json; do [ -e "$f" ] || continue; [ "$f" = "$LOCK" ] && continue
  python3 -c 'import json,sys,time;d=json.load(open(sys.argv[1]));age=int(time.time())-d.get("heartbeat",0);print(("ACTIVE " if age<7200 else "STALE  ")+d["session"],"age=%ds"%age,d.get("worktree",""))' "$f"
done
cat > "$LOCK" <<E
{"session":"$SID","host":"$(hostname)","main_worktree":"$(git rev-parse --show-toplevel)",
 "worktree":"$(git rev-parse --show-toplevel)","branch":"$(git symbolic-ref -q --short HEAD)",
 "start_head":"$(git rev-parse HEAD)","started":$NOW,"heartbeat":$NOW,"touched":[]}
E
```

- **동시 세션 판정**: 다른 잠금 중 `ACTIVE`(heartbeat 2시간 이내)가 하나라도 있으면 동시 세션이다. 호스트나 PID로 판정하지 않는다. 에이전트의 셸은 명령마다 바뀌어 PID가 세션 수명을 나타내지 못한다.
- **heartbeat 갱신**: 단계를 바꿀 때마다, 그리고 `touched`를 갱신할 때 함께 갱신한다(아래 명령).
- `STALE` 잠금은 지우지 말고 보고한다. 끝난 세션이 정리를 못 한 것인지 사용자가 판단한다.

**잠금이 없어도 아래 신호가 있으면 동시 세션으로 본다**

```sh
# (0절 머리줄)
git log --format='%h %an %ar %s' "$(lk start_head)"..HEAD               # [a] 작업 시작 뒤 들어온 커밋
find . -path ./.git -prune -o -type f -newermt '-30 minutes' -print | head   # [b] 최근 수정 파일(자기 touched와 대조)
ROOT=$(pwd -P); SKIP=" $$ "; p=$$                                       # [c] 이 디렉터리를 cwd로 쓰는 다른 프로세스
while [ "${p:-1}" -gt 1 ]; do p=$(ps -o ppid= -p "$p" | tr -d ' '); SKIP="$SKIP${p:-1} "; done
lsof -a -d cwd -F pn 2>/dev/null | awk -v r="$ROOT" '/^p/{pid=substr($0,2)} /^n/{d=substr($0,2); if (d==r || index(d, r"/")==1) print pid}' |
while read -r pid; do
  case "$SKIP" in *" $pid "*) continue;; esac
  pp=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
  [ "$pp" = "$$" ] && continue
  [ "${pp:-1}" != 1 ] && case "$SKIP" in *" $pp "*) continue;; esac
  ps -o pid=,etime=,command= -p "$pid" 2>/dev/null
done
```

- [c]는 이 명령을 돌린 셸과 그 조상, 그 자식(lsof·awk 자신), 같은 조상 아래의 형제(`| head` 같은 파이프 상대)를 뺀다. PID 1의 자식은 빼지 않는다. 분리 실행된 다른 세션은 PID 1 아래로 옮겨지기 때문이다. 출력이 비어 있으면 다른 프로세스가 없다.
- [b]의 파일이 모두 자기 `touched`에 있으면 신호가 아니다.

**동시 세션이면 격리한다(의무)**

```sh
# (0절 머리줄)
BASE=$(lk main_worktree); WT=$(dirname "$BASE")/$(basename "$BASE")-$SID
git -C "$BASE" worktree add -b "hvp/$SID" "$WT" <pinned-commit>
python3 -c 'import json,sys,time;p=sys.argv[1];d=json.load(open(p));d.update(worktree=sys.argv[2],branch="hvp/"+d["session"],start_head=sys.argv[3],heartbeat=int(time.time()));json.dump(d,open(p,"w"))' "$LOCK" "$WT" "$(git -C "$WT" rev-parse HEAD)"
```

이후 모든 실행·수정은 `cd "$(lk worktree)"` 뒤에 한다. worktree 경로는 잠금 파일에만 있으니 매번 `lk worktree`로 읽는다.

**파일을 바꿀 때마다 `touched`에 추가하고 heartbeat 갱신**(5절 점검의 기준이 된다)

```sh
# (0절 머리줄)
python3 -c 'import json,sys,time;p=sys.argv[1];d=json.load(open(p));d["touched"]=sorted(set(d["touched"])|set(sys.argv[2:]));d["heartbeat"]=int(time.time());json.dump(d,open(p,"w"))' "$LOCK" path/a.py path/b.json
```

경로는 worktree 루트 기준 상대 경로로 적는다.

**끝낼 때**(통합은 사용자가 정한 방식으로 먼저 한다)

```sh
# (0절 머리줄)
BASE=$(lk main_worktree); WT=$(lk worktree); cd "$BASE"
[ "$WT" != "$BASE" ] && git -C "$WT" status --porcelain | head    # 비어 있어야 한다
if [ "$WT" = "$BASE" ] || git worktree remove "$WT"; then rm -f "$LOCK"; echo "세션 종료"
else echo "worktree에 변경이 남아 잠금을 유지한다: $WT (멈추고 보고)"; fi
```

`git worktree remove`는 미커밋 변경이 있으면 거부한다. `--force`를 쓰지 않고, 그때는 잠금도 지우지 않는다. 그래야 남은 worktree가 다른 세션에 보인다.

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
# (0절 머리줄)
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

같은 커밋을 해시 시드 여러 개로 돌리고 의미 필드를 비교한다. 정본 경로를 덮어쓰지 않도록 각 실행을 별도 worktree에서 한다.

**미추적 입력**: 깨끗한 worktree에는 미추적·무시 파일(빌드된 CAD, 내려받은 데이터, 로컬 설정)이 없다. 검사가 그런 파일을 읽으면 worktree 실행이 실패하거나 다른 결과를 낸다. 검사가 읽는 미추적 입력을 `INPUTS`에 적어 복사하고, 그 목록을 실행 기록에 남긴다. 목록을 모르면 먼저 빈 목록으로 한 번 돌려 "파일 없음" 오류에서 찾는다.

```sh
SRC=$(git rev-parse --show-toplevel); C=$(git rev-parse HEAD)
OUT=<results>/<oracle>.json; CMD="python3 <tools>/<oracle>.py"
INPUTS=""                               # 예: "cad/build/part.step data/raw/table.csv" (SRC 기준 상대 경로)
SEEDS="1 7 42 1234"                     # 싼 검사는 4개 이상, 비싼 검사는 최소 2개
R=$(mktemp -d)
for s in $SEEDS; do
  D=$R/wt-$s; git worktree add -q --detach "$D" "$C"
  [ -n "$INPUTS" ] && (cd "$SRC" && tar cf - $INPUTS) | (cd "$D" && tar xf -)
  (cd "$D" && PYTHONHASHSEED=$s timeout 1800 $CMD) > "$R/seed-$s.log" 2>&1; echo "seed $s exit $?"
  cp "$D/$OUT" "$R/seed-$s.json" 2>/dev/null || echo "seed $s: $OUT 없음 (로그: $R/seed-$s.log)"
  git worktree remove --force "$D"
done
echo "결과 디렉터리: $R"; echo "복사한 미추적 입력: ${INPUTS:-없음}"
```

`--force`는 이 임시 worktree에만 쓴다. 비교는 4절의 의미 비교 명령으로, 첫 시드 결과와 나머지 각각을 비교한다(`A=$R/seed-1.json B=$R/seed-<s>.json`).

- **일치는 결정론의 증명이 아니다**: 원소가 적은 set은 서로 다른 두 시드에서 같은 순서가 나올 수 있다. 예를 들어 5원소 문자열 set `{alpha, beta, gamma, delta, eps}`의 첫 원소는 CPython에서 시드 1과 7이 같고 42에서 다를 수 있다. 그래서 싼 검사는 시드를 4개 이상 쓴다. 비싼 검사는 2개로 돌리되, 결과를 "시드 n개 일치"로 기록하고 "결정론 확인"이라고 쓰지 않는다.
- **코드 확인 병행**: 판정 경로에서 set·dict 순회 결과로 대표 항목·첫 항목·동률 승자를 고르는 코드를 grep으로 찾아 정렬로 바꾼다(`grep -nE 'next\(iter\(|\bset\(|frozenset|listdir|glob\(' <tools>`). 이중 실행이 일치해도 이런 코드가 판정 경로에 있으면 고친다.
- **흔한 원인**:
  - `set`·`frozenset` 순회 뒤 `next(iter(...))`
  - set에서 만든 dict의 첫 항목 선택
  - 동률 정렬에서 키가 부족함
  - `os.listdir`·`glob` 결과를 정렬하지 않음
- 고친 뒤에는 known-answer 픽스처와 이 이중 실행을 다시 통과시킨다.
- 시드에 무관한 비결정성(스레드, GPU, 난수)은 그 축을 바꿔 같은 방식으로 본다(`OMP_NUM_THREADS=1` 대 기본값, 난수 seed 두 개).

## 4. 낡은 증거 전수 검색과 의미 비교

**바뀐 파일을 기록한 증거 찾기**(수정 전 해시 고정 확인과 수정 후 전수 검색에 같이 쓴다)

```sh
F=<바뀐 소스 경로>
sha() { if command -v sha256sum >/dev/null; then sha256sum | cut -c1-64; else shasum -a 256 | cut -c1-64; fi; }
OLD=$(git show HEAD:"$F" | sha); NEW=$(sha < "$F")
git grep -l -e "$OLD" -e "$F" -- '*.json'          # HEAD 해시 또는 경로를 기록한 증거
```

**모든 증거의 기록 해시 대 현재 해시 대조**(저장소 루트에서 실행. 경로를 키로, sha256을 값으로 기록하는 형식과 `path`/`sha256` 쌍 형식을 본다. 저장소 형식이 다르면 키 이름을 바꾼다)

```sh
python3 - <<'E'
import hashlib, json, os, re, subprocess
H = re.compile(r"^[0-9a-f]{64}$"); cache = {}
def sha(p):
    if p not in cache: cache[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return cache[p]
def is_path(s): return isinstance(s, str) and ("/" in s or "." in s) and len(s) < 1024
def pairs(n):
    if isinstance(n, dict):
        for k, v in n.items():
            if isinstance(v, str) and H.match(v) and is_path(k): yield k, v
            if isinstance(v, dict) and isinstance(v.get("sha256"), str) and is_path(k): yield k, v["sha256"]
        for pk in [k for k in n if k == "path" or k.endswith("_path")]:
            hk = "sha256" if pk == "path" else pk[:-5] + "_sha256"
            if is_path(n.get(pk)) and isinstance(n.get(hk), str): yield n[pk], n[hk]
        for v in n.values(): yield from pairs(v)
    elif isinstance(n, list):
        for v in n: yield from pairs(v)
files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "*.json"], capture_output=True, text=True).stdout.split()
for ev in files:
    try: d = json.load(open(ev))
    except Exception: continue
    for p, h in pairs(d):
        if not os.path.isfile(p): print("MISSING", ev, "->", p)
        elif sha(p) != h: print("STALE  ", ev, "->", p)
E
```

- **재생성 순서**: 출력된 증거 중 다른 증거 파일을 입력으로 기록한 것은 그 증거 뒤에 재생성한다.
- **재검색**: 재생성 뒤 같은 검색을 다시 돌려 STALE 0을 확인한다. 증거가 증거를 기록하면 한 번에 다 잡히지 않는다.
- **MISSING**: 증거가 저장소 루트가 아닌 다른 기준(절대 경로, 증거 파일 기준 상대 경로)으로 경로를 적었을 수 있다. 실제로 없는 파일인지 확인한 뒤 보고한다.
- **찾지 못하는 것**: 이 검색은 해시를 기록하지 않는 의존(파일 이름만 읽는 생성기)을 찾지 못한다. 그런 의존은 저장소의 manifest나 gate가 담당하는지 확인한다.

**재생성 전후 의미 비교**

무시하는 필드는 **키 이름이 정확히 일치할 때만** 뺀다. 이름 일부(예: `head`, `duration`)로 빼면 `bolt_head_diameter_mm`, `fault_duration_s` 같은 물리량 변화를 놓친다. 무시한 키와 해시 모양 값의 차이는 따로 출력해 사람이 확인할 수 있게 한다.

```sh
cp <results>/x.json /tmp/x.before.json    # 재생성 전에 복사
A=/tmp/x.before.json B=<results>/x.json python3 - <<'E'
import json, math, os, re
# 휘발성 키: 마지막 경로 성분이 정확히 이 이름이거나 이 접미사로 끝날 때만 뺀다. 저장소에 맞게 IGNORE_KEYS로 추가한다.
# ID_NAMES는 값이 해시 모양(40–64자리 16진수)일 때만 뺀다. "head"가 볼트 머리 형식 같은 실제 값일 수 있기 때문이다.
ID_NAMES = {"sha256", "hash", "digest", "commit", "design_commit", "git_commit", "head", "head_commit"}
NAMES = {"timestamp", "generated_at", "created_at", "started_at", "finished_at", "elapsed", "elapsed_s",
         "wall_time_s", "runtime_s", "hostname", "host", "tmpdir", "tmp_dir", "cwd", "pid"}
NAMES |= {k for k in os.environ.get("IGNORE_KEYS", "").split(",") if k}
SUFFIXES = ("_sha256", "_hash", "_digest")
HEX = re.compile(r"^[0-9a-f]{40,64}$")
def hexlike(v): return v is None or (isinstance(v, str) and bool(HEX.match(v)))
def flat(x, p=""):
    if isinstance(x, dict):
        for k, v in x.items(): yield from flat(v, p + "/" + k)
    elif isinstance(x, list):
        for i, v in enumerate(x): yield from flat(v, "%s[%d]" % (p, i))
    else: yield p, x
def segs(p): return [re.sub(r"(\[\d+\])+$", "", s) for s in p.split("/") if s]
def volatile(p, x, y):
    s = segs(p)
    if not s: return False
    if "volatile" in s or s[-1] in NAMES: return True
    return (s[-1] in ID_NAMES or s[-1].endswith(SUFFIXES)) and hexlike(x) and hexlike(y)
def num(x): return isinstance(x, (int, float)) and not isinstance(x, bool)
def same(x, y):
    if num(x) and num(y): return math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12)
    return x == y
a, b = (dict(flat(json.load(open(os.environ[k])))) for k in "AB")
keys = sorted(set(a) | set(b)); diff, hexd, ign = [], [], set()
for k in keys:
    x, y = a.get(k), b.get(k)
    if volatile(k, x, y):
        if not same(x, y): ign.add(segs(k)[-1])
        continue
    if same(x, y): continue
    if isinstance(x, str) and isinstance(y, str) and HEX.match(x) and HEX.match(y): hexd.append(k)
    else: diff.append(k)
print("EQUIVALENT" if not diff else "DIFFERENT %d" % len(diff))
for k in diff[:50]: print(" ", k, a.get(k), "->", b.get(k))
print("무시한 휘발성 키(값이 바뀐 것):", ", ".join(sorted(ign)) or "없음")
if hexd: print("해시 모양 값만 바뀐 필드 %d개(확인 필요):" % len(hexd), ", ".join(hexd[:10]))
E
```

- 커밋된 버전과 비교하려면 `git show HEAD:<results>/x.json > /tmp/x.before.json`을 먼저 실행한다.
- 결과가 EQUIVALENT면 "의미 변화 없음"으로 기록하되, "무시한 휘발성 키" 목록을 함께 적는다. 목록에 물리량이 보이면 `NAMES`가 너무 넓은 것이니 고친다.
- DIFFERENT면 필드와 전후 값을 수정 보고에 옮긴다.
- 0 근처 잔차(예: 1e-13 대 3e-13)는 절대 하한 덕분에 변화로 잡히지 않는다. 저장소나 오라클이 허용치를 정했으면 그 값을 쓴다.

## 5. 스테이지 직전 점검

```sh
# (0절 머리줄)
cd "$(lk worktree)"
OWN=$(mktemp); STG=$(mktemp)
python3 -c 'import json,sys;print("\n".join(json.load(open(sys.argv[1]))["touched"]))' "$LOCK" | sort > "$OWN"
git diff --cached --name-only | sort > "$STG"
echo "[1] 남의 파일(내릴 것: git restore --staged -- <path>)"; comm -23 "$STG" "$OWN"
echo "[1'] 자기 변경인데 스테이지 안 됨(의도 확인)";        comm -13 "$STG" "$OWN"
echo "[2] 25 MB 초과"
git diff --cached --name-only --diff-filter=d | while IFS= read -r f; do
  s=$(git cat-file -s ":$f"); [ "$s" -gt 26214400 ] && echo "$s $f"; done; true
echo "[3] 원시 산출물"
git diff --cached --name-only --diff-filter=d | grep -E '(_raw/|/raw/|\.(vtu|vtk|frd|rmed|h5|npz|npy|log|skill)$)'; true
echo "[4] 작업 시작 뒤 HEAD에 들어온 커밋(모두 자기 것이어야 함)"
git log --format='%h %an %s' "$(lk start_head)"..HEAD
echo "[5] HEAD 상태"; git symbolic-ref -q --short HEAD || echo "DETACHED: git switch <branch>"
```

- 각 항목은 출력 줄로 판정한다. 종료 코드로 판정하지 않는다(`grep`은 일치가 없을 때 1을 낸다).
- [1]·[2]·[3]이 비어 있고, [4]가 모두 자기 커밋이고, [5]가 브랜치 이름일 때만 커밋한다.
- 원시 산출물 패턴은 `.gitignore`에 추가하고, 그 변경도 경로 지정으로 커밋한다.
- 스테이지는 `git add -- <path> ...`로만 한다. `git add -A`나 `git add .`은 쓰지 않는다.
- 파일 하나를 되돌릴 때는 `git restore --source=<rev> -- <path>`를 쓴다. 이미 HEAD가 분리됐다면 `git switch <branch>`로 돌아간다. 이때 추적 파일 충돌이 나면, 재생성 가능한 파일만 스크래치로 옮긴 뒤 돌아간다.

## 6. 커밋 스냅샷 검증

인덱스로 임시 커밋 객체를 만든다. ref는 움직이지 않는다. 그 커밋을 깨끗한 worktree에 풀고 pre-push 스위트를 돌린다.

```sh
TOP=$(git rev-parse --show-toplevel)
HP=$(git config core.hooksPath)                                 # 훅 위치: 설정값 → 상대면 저장소 루트 기준
case "$HP" in "") HP=$(git rev-parse --path-format=absolute --git-path hooks);; /*) ;; *) HP="$TOP/$HP";; esac
sed -n '1,80p' "$HP/pre-push"                                   # 훅이 실제로 돌리는 명령 확인
T=$(git write-tree); C=$(git commit-tree "$T" -p HEAD -m snapshot-candidate)
S=$(mktemp -d)/snap; git worktree add -q --detach "$S" "$C"
(cd "$S" && <훅이 돌리는 명령 그대로, 같은 환경>); echo "exit $?"   # 예: nix develop -c <suite>, conda run -n <env> <suite>
git worktree remove --force "$S"
```

- 연결된 worktree에서는 `.git`이 디렉터리가 아니라 파일이라 `.git/hooks`를 직접 쓰면 실패한다. 위처럼 `--git-path hooks`로 찾는다.
- 훅을 직접 부르려면 git이 넘기는 인자와 stdin을 맞춘다.

  ```sh
  echo "refs/heads/<b> $C refs/heads/<b> $(git rev-parse origin/<b>)" | (cd "$S" && "$HP/pre-push" origin <url>)
  ```

- 여기서 실패하면 원인 유형(SKILL.md 6단계 분류)을 정하고 고친다. 그 뒤 인덱스를 다시 만들어 이 절을 반복한다. push는 여기서 통과한 다음에만 한다.
- 커밋을 여러 개로 나눌 때는 묶음마다 같은 방식으로 확인한다. `git commit-tree`의 부모를 직전 묶음의 임시 커밋으로 주면 순서대로 확인할 수 있다.

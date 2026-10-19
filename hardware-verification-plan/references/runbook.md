# 실행 런북

SKILL.md 1·6·7·8단계에서 쓰는 명령과 도구별 실행 패턴. 저장소에 같은 일을 하는 스크립트가 있으면 그것을 쓴다. 아래 명령은 셸에 바로 붙여 쓰는 짧은 형태이고 저장소에 새 파일을 만들지 않는다. 경로, 파일 이름, glob은 모두 예시이며 저장소에 맞게 바꾼다.

## 목차

1. 세션 잠금 파일과 격리
2. 오라클 실행표와 도구별 패턴
3. 결정론 이중 실행
4. 낡은 증거 전수 검색과 의미 비교
5. 스테이지 직전 점검
6. 커밋 스냅샷 검증

---

## 1. 세션 잠금 파일과 격리

잠금은 `<git-common-dir>/hvp-sessions/`에 둔다. 커밋되지 않고 모든 worktree가 함께 본다.

**시작할 때: 다른 잠금 확인 후 등록**

```sh
SID=<session-id>                      # 예: agent-<날짜>-<시각>
L=$(git rev-parse --path-format=absolute --git-common-dir)/hvp-sessions; mkdir -p "$L"
ls "$L"/*.json 2>/dev/null && cat "$L"/*.json      # 기존 잠금 확인
cat > "$L/$SID.json" <<E
{"session":"$SID","host":"$(hostname)","pid":$PPID,"worktree":"$(git rev-parse --show-toplevel)",
 "branch":"$(git symbolic-ref -q --short HEAD)","head":"$(git rev-parse HEAD)",
 "started":$(date +%s),"heartbeat":$(date +%s),"touched":[]}
E
```

- **동시 세션 판정**: 다른 잠금이 하나라도 다음에 해당하면 동시 세션으로 본다.
  - 같은 호스트이고 `kill -0 <pid>`가 성공한다.
  - 다른 호스트이고 `heartbeat`가 2시간 안이다.
- 잠금이 없어도 아래 신호가 있으면 동시 세션으로 본다.

```sh
git log --format='%h %an %ar %s' <작업 시작 HEAD>..HEAD          # 자기가 만들지 않은 커밋
find . -path ./.git -prune -o -type f -newermt '-30 minutes' -print | head   # 최근 수정 파일
lsof -a -d cwd 2>/dev/null | grep "$(pwd)"                        # 이 디렉터리를 cwd로 쓰는 프로세스
```

**동시 세션이면 격리한다(의무)**

```sh
git worktree add -b hvp/$SID ../$(basename "$PWD")-$SID <pinned-commit>
cd ../$(basename "$PWD")-$SID          # 이후 모든 실행·수정은 여기서. 잠금을 이 worktree 기준으로 다시 쓴다.
```

**파일을 바꿀 때마다 `touched`에 추가**(5절 점검의 기준이 된다)

```sh
python3 -c 'import json,sys,time;p=sys.argv[1];d=json.load(open(p));d["touched"]=sorted(set(d["touched"])|set(sys.argv[2:]));d["heartbeat"]=int(time.time());json.dump(d,open(p,"w"))' "$L/$SID.json" path/a.py path/b.json
```

**끝낼 때**

```sh
rm -f "$L/$SID.json"; git worktree remove ../$(basename "$PWD")-$SID   # 통합은 사용자가 정한 방식으로 먼저
```

## 2. 오라클 실행표와 도구별 패턴

**실행표(1단계 산출물)**

| 오라클 | 명령 | 환경 | 성공 토큰 | 보통 시간 | 대기 상한 | 증거 경로 |
| --- | --- | --- | --- | --- | --- | --- |
| 단위·픽스처 | `python3 -m <pkg>.test_<oracle>` | 시스템 Python | `OK` | 5 s | 30 s | 없음(단위) |
| 조립 간섭 | `freecadcmd -c "exec(open('/tmp/drv.py').read())"` | FreeCAD | `RESULT_OK` | 4 min | 10 min | `<results>/clearance.json` |
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

```sh
cat > /tmp/drv.py <<'E'
import runpy, sys, os, traceback
try:
    runpy.run_path(os.environ["T"], run_name="__main__"); print("RESULT_OK")
except SystemExit as e:
    print("RESULT_OK" if e.code in (0, None) else "RESULT_FAIL exit=%s" % e.code)
except BaseException:
    traceback.print_exc(); print("RESULT_FAIL")
sys.stdout.flush(); os._exit(0)
E
T=$PWD/<tests>/test_<name>.py timeout 900 freecadcmd -c "exec(open('/tmp/drv.py').read())" 2>&1 | grep -E 'RESULT_|Error|assert'
```

- unittest는 성공해도 `SystemExit(0)`으로 끝난다. 드라이버가 이것을 성공으로 해석하게 한다(위 예).
- `os._exit(0)`는 FreeCAD가 종료 시 멈추는 것을 막는다. 판정은 종료 코드가 아니라 `RESULT_` 토큰으로 한다.
- OCC 거리 질의 일부는 특정 형상에서 segfault가 난다. 같은 호출을 반복하지 말고 질의를 바꾼다(oracle-catalog 24절).

**nix·conda 환경**

- 환경 빌드가 실패하면 먼저 같은 명령을 한 번 다시 실행해 일시적 실패인지 본다(의존 경로 다운로드 실패는 재시도로 풀린다).
- 두 번째도 실패하면 로그의 첫 오류를 읽고 원인을 기록한다. 환경 밖에서 훅을 돌리거나 `--no-verify`로 우회하지 않는다.

## 3. 결정론 이중 실행

같은 커밋을 해시 시드 여러 개로 돌리고 의미 필드를 비교한다. 정본 경로를 덮어쓰지 않도록 각 실행을 별도 worktree에서 한다.

```sh
C=$(git rev-parse HEAD); OUT=<results>/<oracle>.json; CMD="python3 <tools>/<oracle>.py"
SEEDS="1 7 42 1234"                     # 최소 2개. 싼 검사는 4개 이상
for s in $SEEDS; do
  git worktree add -q --detach /tmp/det-$s $C
  (cd /tmp/det-$s && PYTHONHASHSEED=$s timeout 1800 $CMD) > /tmp/det-$s.log 2>&1; echo "seed $s exit $?"
  cp /tmp/det-$s/$OUT /tmp/det-$s.json; git worktree remove --force /tmp/det-$s
done
```

비교는 4절의 의미 비교 명령으로, 첫 시드 결과와 나머지 각각을 비교한다(`A=/tmp/det-1.json B=/tmp/det-<s>.json`).

- **일치는 결정론의 증명이 아니다**: 원소가 적은 set은 서로 다른 두 시드에서 같은 순서가 나올 수 있다(예: 5원소 문자열 set의 첫 원소가 시드 1과 7에서 같고 42에서 다를 수 있다). 그래서 싼 검사는 시드를 4개 이상 쓴다. 비싼 검사는 2개로 돌리되, 결과를 "시드 n개 일치"로 기록하고 "결정론 확인"이라고 쓰지 않는다.
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
OLD=$(git show HEAD:$F | shasum -a 256 | cut -c1-64); NEW=$(shasum -a 256 < $F | cut -c1-64)
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
- **찾지 못하는 것**: 이 검색은 해시를 기록하지 않는 의존(파일 이름만 읽는 생성기)을 찾지 못한다. 그런 의존은 저장소의 manifest나 gate가 담당하는지 확인한다.

**재생성 전후 의미 비교**(해시·커밋·시각·소요·`volatile`을 빼고 수치는 상대 1e-9, 절대 1e-12 허용)

```sh
cp <results>/x.json /tmp/x.before.json    # 재생성 전에 복사
A=/tmp/x.before.json B=<results>/x.json python3 - <<'E'
import json, math, os, re
IGN = re.compile(r"(sha256|hash|digest|commit|head|timestamp|generated_at|_at$|elapsed|duration|hostname|volatile|tmp)", re.I)
HEX = re.compile(r"^[0-9a-f]{40,64}$")
def flat(x, p=""):
    if isinstance(x, dict):
        for k, v in x.items(): yield from flat(v, p + "/" + k)
    elif isinstance(x, list):
        for i, v in enumerate(x): yield from flat(v, "%s[%d]" % (p, i))
    else: yield p, x
a, b = (dict(flat(json.load(open(os.environ[k])))) for k in "AB")
def same(x, y):
    if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
        return math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12)
    return x == y
d = [k for k in sorted(set(a) | set(b)) if not IGN.search(k)
     and not (isinstance(a.get(k), str) and HEX.match(a.get(k, "")) and isinstance(b.get(k), str) and HEX.match(b.get(k, "")))
     and not same(a.get(k), b.get(k))]
print("EQUIVALENT" if not d else "DIFFERENT %d" % len(d))
for k in d[:50]: print(" ", k, a.get(k), "->", b.get(k))
E
```

- 커밋된 버전과 비교하려면 `git show HEAD:<results>/x.json > /tmp/x.before.json`을 먼저 실행한다.
- 결과가 EQUIVALENT면 "의미 변화 없음"으로 기록한다. DIFFERENT면 필드와 전후 값을 수정 보고에 옮긴다.
- 0 근처 잔차(예: 1e-13 대 3e-13)는 절대 하한 덕분에 변화로 잡히지 않는다. 저장소나 오라클이 허용치를 정했으면 그 값을 쓴다.

## 5. 스테이지 직전 점검

```sh
OWN=/tmp/own-paths.txt                       # 자기 변경 목록(한 줄 한 경로): 잠금의 touched 또는 작업 기록
python3 -c 'import json,sys;print("\n".join(json.load(open(sys.argv[1]))["touched"]))' "$L/$SID.json" | sort > $OWN
git diff --cached --name-only | sort > /tmp/staged.txt
echo "[1] 남의 파일(내릴 것: git restore --staged -- <path>)"; comm -23 /tmp/staged.txt $OWN
echo "[1'] 자기 변경인데 스테이지 안 됨(의도 확인)";        comm -13 /tmp/staged.txt $OWN
echo "[2] 25 MB 초과"
git diff --cached --name-only -z --diff-filter=d | xargs -0 -I{} sh -c 's=$(git cat-file -s ":{}"); [ "$s" -gt 26214400 ] && echo "$s {}"'
echo "[3] 원시 산출물"
git diff --cached --name-only --diff-filter=d | grep -E '(_raw/|/raw/|\.(vtu|vtk|frd|rmed|h5|npz|npy|log|skill)$)'
echo "[4] 작업 시작 뒤 HEAD에 들어온 커밋(모두 자기 것이어야 함)"
git log --format='%h %an %s' $(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["head"])' "$L/$SID.json")..HEAD
echo "[5] HEAD 상태"; git symbolic-ref -q --short HEAD || echo "DETACHED: git switch <branch>"
```

- [1]·[2]·[3]이 비어 있고, [4]가 모두 자기 커밋이고, [5]가 브랜치 이름일 때만 커밋한다.
- 원시 산출물 패턴은 `.gitignore`에 추가하고, 그 변경도 경로 지정으로 커밋한다.
- 스테이지는 `git add -- <path> ...`로만 한다. `git add -A`나 `git add .`은 쓰지 않는다.
- 파일 하나를 되돌릴 때는 `git restore --source=<rev> -- <path>`를 쓴다. 이미 HEAD가 분리됐다면 `git switch <branch>`로 돌아간다. 이때 추적 파일 충돌이 나면, 재생성 가능한 파일만 스크래치로 옮긴 뒤 돌아간다.

## 6. 커밋 스냅샷 검증

인덱스로 임시 커밋 객체를 만든다. ref는 움직이지 않는다. 그 커밋을 깨끗한 worktree에 풀고 pre-push 스위트를 돌린다.

```sh
T=$(git write-tree); C=$(git commit-tree "$T" -p HEAD -m snapshot-candidate)
git worktree add -q --detach /tmp/snap $C
git config core.hooksPath || echo .git/hooks                    # 훅 위치
sed -n '1,80p' "$(git config core.hooksPath || echo .git/hooks)/pre-push"   # 훅이 실제로 돌리는 명령 확인
(cd /tmp/snap && <훅이 돌리는 명령 그대로, 같은 환경>)          # 예: nix develop -c <suite>, conda run -n <env> <suite>
echo "exit $?"; git worktree remove --force /tmp/snap
```

- 훅을 직접 부르려면 git이 넘기는 인자와 stdin을 맞춘다.

  ```sh
  echo "refs/heads/<b> $C refs/heads/<b> $(git rev-parse origin/<b>)" | (cd /tmp/snap && <hooksPath>/pre-push origin <url>)
  ```

- 여기서 실패하면 원인 유형(SKILL.md 6단계 분류)을 정하고 고친다. 그 뒤 인덱스를 다시 만들어 이 절을 반복한다. push는 여기서 통과한 다음에만 한다.
- 커밋을 여러 개로 나눌 때는 묶음마다 같은 방식으로 확인한다. `git commit-tree`의 부모를 직전 묶음의 임시 커밋으로 주면 순서대로 확인할 수 있다.

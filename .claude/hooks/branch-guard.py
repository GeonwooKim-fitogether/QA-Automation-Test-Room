#!/usr/bin/env python3
"""세션 하나가 브랜치 하나만 만들고 PR 하나만 열게 막는 PreToolUse 훅.

## 왜 이 훅이 필요한가

브랜치·PR 이름 규칙(`.claude/rules/branch-pr-naming.md`)은 2026-09-22 에 만들어졌지만
글로만 있었습니다. 일주일 뒤 창고 자신을 재니 원격 브랜치 50개 중 24개가 머지된 채
남아 있었고, 한 브랜치에서 PR 을 5~7개씩 연 세션이 셋이었습니다. 규칙이 지켜지지
않은 것이 아니라, 지켜지게 만드는 장치가 없었습니다. 이 훅이 그 장치입니다.

## 무엇을 보나 — `Bash` 명령 다섯 가지만

| 명령 | 판정 |
|---|---|
| 브랜치 생성 (`git checkout -b` · `git switch -c` · `git branch <이름>`) | 이름이 `<종류>/<슬러그>` 형식이 아니면 차단. 이 세션이 이미 브랜치를 만들었으면 차단 |
| 브랜치 이름 바꾸기 (`git branch -m`) | 차단 — 바꾸면 열린 PR 이 닫힌다 |
| `main` 으로의 push (`git push origin main`, 또는 main 에 서서 `git push`) | 차단 |
| `gh pr create` — main 에서 | 차단 |
| `gh pr create` — 이미 머지된 PR 이 있는 브랜치에서 | 차단 — 브랜치 재사용으로 PR 이 번식하던 경로 |

**그 밖의 모든 명령에는 아무 판정도 내지 않습니다.** 셸은 세션이 거의 모든 일을
하는 통로라, 애매한 것까지 막으면 관계없는 작업이 멈추고 결국 사람이 훅을 꺼 버립니다.

## 세션을 어떻게 식별하나

훅 입력에 `session_id` 가 들어옵니다. 브랜치를 만들 때 그 값으로
`.claude/state/branch-guard/<session_id>` 파일에 브랜치 이름을 적어 두고, 같은 세션이
다른 이름으로 또 만들려 하면 막습니다. 같은 이름을 다시 만드는 것(앞 명령이 실패해 재시도)은
막지 않습니다. `.claude/state/` 는 gitignore 대상입니다.

## 왜 "확인 요청"이 아니라 "차단"인가

같은 저장소의 `sql-write-guard.py` 가 실측한 결과, 이 원격 실행 환경에서는 `ask` 판정이
무시되고 도구가 그대로 실행됩니다. 실제로 멈춰 세우는 판정은 `deny` 뿐입니다.

## 막힌 것을 푸는 방법 — 일회용 열쇠

    .claude/branch-unlock

이 파일이 있으면 막혔을 명령 **한 번**이 통과하고, 통과하는 즉시 파일이 지워집니다.
DB 쓰기 훅과 같은 규약입니다 — **Claude 는 사용자가 승인하기 전에 열쇠를 만들지 않습니다.**
두 번째 브랜치가 정말 필요하면(저장소 규칙이 PR 분리를 강제할 때) 이름을 보여 주고 승인을
받은 뒤 열쇠를 만듭니다.

## 잴 수 없으면 막지 않는다

"이미 머지된 PR 이 있는가"는 `gh` 로 묻습니다. `gh` 가 없거나 인증이 안 돼 답을 얻지
못하면 차단하지 않고 물러납니다(표준 오류에 그 사실을 남깁니다). 재지 못한 것을 막으면
정상 작업이 영구히 멈추기 때문입니다.

## 이 훅이 덮지 못하는 것

- 클라우드(claude.ai/code) 세션은 플랫폼이 브랜치를 만들어 훅이 볼 명령이 없습니다.
  그쪽은 `.github/workflows/branch-pr-policy.yml` 이 받습니다.
- 여러 저장소를 한 폴더에 여는 세션에서는 훅이 실리지 않는 것이 실측됐습니다(2026-08-17).
- GitHub MCP 도구로 브랜치·PR 을 만드는 경로는 보지 않습니다. 그 서버가 연결되면
  매처 한 줄을 더하는 일입니다.
"""

import json
import os
import re
import shlex
import subprocess
import sys

BASH_TOOL = "Bash"
UNLOCK_FILENAME = "branch-unlock"
STATE_DIRNAME = os.path.join("state", "branch-guard")
RULE_PATH = ".claude/rules/branch-pr-naming.md"

# 브랜치 이름 형식 — 규칙 1절. 종류 여섯 중 하나, 슬래시, 영문 소문자·숫자·하이픈.
KINDS = ("feat", "fix", "mig", "refactor", "docs", "chore")
BRANCH_PATTERN = re.compile(r"^(?:%s)/[a-z0-9]+(?:-[a-z0-9]+)*$" % "|".join(KINDS))

# 명령 앞에 붙는 감싸개. 이것을 벗겨야 `sudo git …`·`env X=1 git …` 도 같은 명령으로 보인다.
COMMAND_WRAPPERS = {"sh", "bash", "zsh", "dash", "sudo", "env", "time", "nohup", "exec"}

# `git branch` 의 옵션 중 "만들기"가 아닌 것. 이 중 하나라도 있으면 생성으로 보지 않는다.
BRANCH_NON_CREATE_FLAGS = {
    "-d", "-D", "--delete", "-l", "--list", "-a", "--all", "-r", "--remotes", "-v", "-vv",
    "--verbose", "--show-current", "-u", "--set-upstream-to", "--unset-upstream",
    "--edit-description", "--contains", "--no-contains", "--merged", "--no-merged",
    "--points-at", "-c", "-C", "--copy",
}
BRANCH_RENAME_FLAGS = {"-m", "-M", "--move"}


# ── 경로 ──────────────────────────────────────────────────────────────────────

def claude_dir() -> str:
    """`.claude/` 의 경로. 훅 파일 위치를 기준으로 삼아 실행 위치와 무관하게 같은 곳을 본다."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def unlock_path() -> str:
    return os.path.join(claude_dir(), UNLOCK_FILENAME)


def state_path(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
    return os.path.join(claude_dir(), STATE_DIRNAME, safe)


# ── 바깥 세계 — 시험에서 바꿔 끼울 수 있게 함수로 둔다 ───────────────────────

def current_branch(cwd):
    """지금 체크아웃된 브랜치 이름. 못 재면 None."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd or None, capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    name = proc.stdout.strip()
    return name or None


def merged_pr_exists(branch: str, cwd):
    """그 브랜치를 head 로 하는 머지된 PR 이 있는가. True/False, 못 재면 None."""
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "merged",
             "--limit", "1", "--json", "number"],
            cwd=cwd or None, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return len(json.loads(proc.stdout or "[]")) > 0
    except ValueError:
        return None


# ── 명령 해석 ────────────────────────────────────────────────────────────────

def segments(command: str):
    """셸 명령을 `&&`·`||`·`;`·`|`·줄바꿈으로 나눠 각 조각을 토큰 목록으로 만든다."""
    out = []
    for raw in re.split(r"\s*(?:&&|\|\||;|\||\n)\s*", command or ""):
        raw = raw.strip().strip("()").strip()
        if not raw:
            continue
        try:
            tokens = shlex.split(raw, posix=True)
        except ValueError:
            tokens = raw.split()
        # 감싸개와 `X=1` 꼴의 환경 변수 대입을 벗긴다.
        while tokens:
            head = tokens[0]
            bare = os.path.basename(head.strip("\"'"))
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", head) or bare in COMMAND_WRAPPERS:
                tokens.pop(0)
                continue
            break
        if tokens:
            out.append(tokens)
    return out


def git_invocation(tokens):
    """`git` 호출이면 (하위명령, 그 뒤 인자, -C 로 지정한 폴더) 를, 아니면 None."""
    if not tokens or os.path.basename(tokens[0]) != "git":
        return None
    i, cdir = 1, None
    while i < len(tokens):
        t = tokens[i]
        if t == "-C" and i + 1 < len(tokens):
            cdir = tokens[i + 1]
            i += 2
            continue
        if t == "-c" and i + 1 < len(tokens):
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        break
    if i >= len(tokens):
        return None
    return tokens[i], tokens[i + 1:], cdir


def value_after(args, flags):
    """`flags` 중 하나 바로 뒤의 값을 돌려준다. `--flag=값` 꼴도 받는다."""
    for i, a in enumerate(args):
        if a in flags:
            return args[i + 1] if i + 1 < len(args) else None
        for f in flags:
            if a.startswith(f + "="):
                return a[len(f) + 1:]
    return None


def branch_creation(sub, args):
    """이 git 호출이 브랜치를 새로 만드는가. 만들면 그 이름, 아니면 None."""
    if sub == "checkout":
        return value_after(args, ("-b", "-B"))
    if sub == "switch":
        return value_after(args, ("-c", "-C", "--create", "--force-create"))
    if sub == "branch":
        if any(a in BRANCH_NON_CREATE_FLAGS or a in BRANCH_RENAME_FLAGS for a in args):
            return None
        positional = [a for a in args if not a.startswith("-")]
        return positional[0] if positional else None
    return None


def is_branch_rename(sub, args) -> bool:
    return sub == "branch" and any(a in BRANCH_RENAME_FLAGS for a in args)


def push_targets_main(args, branch) -> bool:
    """`git push` 가 main 을 겨누는가. refspec 에 main 이 있거나, refspec 없이 main 에 서 있을 때."""
    positional = [a for a in args if not a.startswith("-")]
    refspecs = positional[1:]  # 첫 위치 인자는 remote
    for r in refspecs:
        if r == "main" or r.endswith(":main"):
            return True
    if refspecs:
        return False
    return branch == "main"


def gh_pr_create(tokens) -> bool:
    return (len(tokens) >= 3 and os.path.basename(tokens[0]) == "gh"
            and tokens[1] == "pr" and tokens[2] == "create")


# ── 세션 상태 ────────────────────────────────────────────────────────────────

def recorded_branch(session_id):
    if not session_id:
        return None
    try:
        with open(state_path(session_id), "r", encoding="utf-8") as fp:
            return fp.read().strip() or None
    except OSError:
        return None


def record_branch(session_id, name):
    if not session_id:
        return
    path = state_path(session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(name + "\n")
    except OSError:
        pass


def consume_unlock() -> bool:
    """열쇠가 있으면 지우고 True. 없으면 False."""
    try:
        os.remove(unlock_path())
        return True
    except OSError:
        return False


# ── 판정 ─────────────────────────────────────────────────────────────────────

def tail(extra: str = "") -> str:
    """차단 사유의 공통 꼬리 — 근거와 여는 법."""
    parts = [f"근거: `{RULE_PATH}`."]
    if extra:
        parts.append(extra)
    parts.append(
        "정말 필요하면 무엇을 왜 하려는지 사용자에게 보여 주고, 승인을 받은 뒤 "
        f"`.claude/{UNLOCK_FILENAME}` 파일을 만들면 그 한 번만 통과한다. "
        "승인 전에 열쇠를 스스로 만들지 않는다."
    )
    return " ".join(parts)


def judge(payload: dict):
    """차단할 이유가 있으면 그 문장을, 없으면 None 을 돌려준다. 부수효과: 브랜치 생성 기록."""
    if payload.get("tool_name") != BASH_TOOL:
        return None
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        return None
    session_id = (payload.get("session_id") or "").strip()
    cwd = payload.get("cwd") or None
    # 같은 명령 안에서 앞 조각이 브랜치를 만들었으면, 뒤 조각은 그 브랜치에 서 있다.
    # 훅은 실행 전에 판정하므로 git 에 물으면 아직 옛 브랜치(main)가 나온다.
    created_here = None

    def branch_now(where):
        return created_here or current_branch(where)

    for tokens in segments(command):
        inv = git_invocation(tokens)
        if inv:
            sub, args, cdir = inv
            where = cdir or cwd

            if is_branch_rename(sub, args):
                return ("브랜치 이름을 바꾸는 명령은 막는다. GitHub 은 이름이 바뀐 브랜치의 열린 PR 을 "
                        "닫아 버리고 다시 열 수 없다(2026-09-21 사고). 새 이름이 필요하면 새 브랜치를 "
                        "새 세션에서 만든다. " + tail())

            name = branch_creation(sub, args)
            if name:
                if not BRANCH_PATTERN.match(name):
                    return (f"브랜치 이름 `{name}` 은(는) 규칙 형식이 아니다. 형식은 "
                            f"`<종류>/<영문-슬러그>` 이고 종류는 {', '.join(KINDS)} 중 하나다 "
                            "(예: `feat/gen-task-order`). 날짜·PR 번호·대문자·밑줄을 넣지 않는다. " + tail())
                prev = recorded_branch(session_id)
                if prev and prev != name:
                    return (f"이 세션은 이미 브랜치 `{prev}` 을(를) 만들었다. 세션 하나는 브랜치 하나만 "
                            f"만든다 — `{name}` 을(를) 또 만들면 요청 하나가 PR 여러 개로 흩어진다. "
                            "다른 요청이면 새 세션에서 시작한다. "
                            + tail("저장소 규칙이 PR 분리를 강제하는 경우(예: 마이그레이션)만 예외다."))
                record_branch(session_id, name)
                created_here = name
                continue

            if sub == "push" and push_targets_main(args, branch_now(where)):
                return ("`main` 에 직접 push 하지 않는다. 작업 브랜치에 push 하고 PR 로 머지한다. " + tail())
            continue

        if gh_pr_create(tokens):
            head = value_after(tokens[3:], ("--head", "-H")) or branch_now(cwd)
            if head == "main":
                return ("`main` 에서 PR 을 열 수 없다. 먼저 `<종류>/<슬러그>` 브랜치를 만들고 거기서 연다. "
                        + tail())
            if head:
                merged = merged_pr_exists(head, cwd)
                if merged is None:
                    print("[branch-guard] gh 로 머지된 PR 유무를 재지 못해 이 판정은 건너뛴다.",
                          file=sys.stderr)
                elif merged:
                    return (f"브랜치 `{head}` 에는 이미 머지된 PR 이 있다. 머지된 브랜치를 다시 써서 "
                            "PR 을 또 열면 한 브랜치에 PR 이 쌓인다(실측: 한 브랜치에서 7개). "
                            "다음 요청은 새 세션·새 브랜치·새 PR 로 시작한다. " + tail())
    return None


def emit(decision: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> int:
    # Windows 콘솔(cp949)에서는 판정문의 일부 글자가 인코딩되지 않아 훅 자체가 죽는다.
    # 판정은 JSON 으로 Claude Code 가 읽으므로 UTF-8 로 고정한다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # 입력을 못 읽으면 판단하지 않고 원래 권한 흐름에 맡긴다.
        return 0

    reason = judge(payload)
    if reason is None:
        return 0

    if consume_unlock():
        # 열쇠를 만든 행위가 곧 승인이므로 확인 창을 또 띄우지 않는다.
        emit("allow", "사용자가 만든 일회용 열쇠(.claude/branch-unlock)로 이번 한 번만 통과한다. 열쇠는 소비됐다.")
        return 0

    emit("deny", reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())

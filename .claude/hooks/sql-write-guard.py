#!/usr/bin/env python3
"""데이터를 바꾸는 SQL 을 사람의 허락 없이는 실행하지 못하게 막는 PreToolUse 훅.

## 왜 이 훅이 필요한가

Supabase 의 SQL 실행 도구(`execute_sql`)는 권한 목록에서 통째로 열어 두었습니다.
조회 한 번마다 확인 창이 뜨면 작업이 계속 멈추기 때문입니다. 그런데 권한 규칙은
**도구 단위로만** 걸 수 있어서, 같은 도구 안에서 `select`(조회)와 `delete`·`drop`
(삭제)을 가를 방법이 없습니다. 그 결과 데이터를 지우는 쿼리까지 묻지 않고
실행되는 상태가 됩니다.

이 훅이 그 틈을 메웁니다. 도구가 호출되기 직전에 쿼리문을 읽어, 읽기만 하는
쿼리면 그대로 통과시키고, 데이터나 구조를 바꾸는 쿼리면 차단합니다.

## 네 개의 감시면 — 저장소가 어느 통로를 쓰든 관문이 서게

| 감시면 | 하는 일 | 이 훅의 처리 |
|---|---|---|
| 커넥터의 `execute_sql` | 조회와 쓰기가 한 도구에 섞여 있다 | 쿼리문을 읽어 **가린다.** 조회면 통과, 쓰기면 차단 |
| 커넥터의 `apply_migration` | 데이터베이스 구조를 바꾸는 스크립트를 적용한다 | 두 단계다. 먼저 그 파일이 **push 됐는지** 보고 아니면 열쇠와 무관하게 차단한다. push 됐으면 그다음은 전부 쓰기이므로 열쇠를 요구한다 |
| `Bash` | 자체 호스팅 저장소는 셸 스크립트로 마이그레이션을 적용한다 | 명령이 그 스크립트를 **실행하는지** 가려, 실행이면 커넥터와 같은 순서(push 판정 → 열쇠)를 적용한다. 그 밖의 모든 셸 명령에는 아무 판정도 내지 않는다 |
| GitHub Actions 실행 도구 | 적용을 **저장소의 자동 작업(워크플로)** 으로 옮기면, 그 작업을 부르는 것이 곧 라이브 쓰기다 | 부르려는 워크플로 파일 이름이 라이브 데이터베이스를 바꾸는 목록에 있을 때만 같은 순서를 적용한다. 다른 워크플로(빌드·검사·배포)에는 아무 판정도 내지 않는다 |

앞의 둘을 보는 이유는 실제 사고에서 나왔습니다. 한 세션이 `apply_migration` 을
여섯 번 불러 라이브 데이터베이스를 바꿨는데, 그 도구는 허용 목록에도 없고 이 훅도
보지 않던 경로여서 아무 확인 없이 지나갔습니다. `execute_sql` 만 막아 두면 옆문이
열려 있는 셈입니다.

세 번째를 더한 이유도 같은 모양입니다. **통로가 옮겨가면 관문은 따라가지 않습니다.**
Fitstack 이 2026-09-03 에 관리형 Supabase 에서 자체 호스팅으로 옮기자 적용 통로가
커넥터 호출에서 셸 스크립트 실행으로 바뀌었고, 훅은 옛 통로를 그대로 지키고 있었습니다.
막는 장치가 아무도 다니지 않는 길에 서 있는 상태였고, 오류는 한 번도 나지 않았습니다.

네 번째는 같은 일이 **또 일어나기 전에** 미리 세운 것입니다. 적용을 사람의 컴퓨터에서
저장소의 자동 작업으로 옮기면(원격 세션에는 클라우드 접속 자격이 없으므로 그렇게 옮기게
됩니다), 셸 감시면이 보던 명령은 더 이상 세션에서 실행되지 않습니다. 대신 세션은 "그
워크플로를 돌려라"라고 GitHub 에 요청하고, 실제 적용은 GitHub 의 실행기가 합니다. 그
요청 한 번이 곧 라이브 데이터베이스 쓰기이므로, 이 감시면이 그 자리에 관문을 세웁니다.

정직하게 적어 둘 한계가 하나 있습니다. 이 감시면은 **새로 실행하는 요청**(`run_workflow`)
만 가립니다. 이미 돌았던 실행을 다시 돌리는 요청(`rerun_*`)은 실행 번호만 넘어오고 그것이
어느 워크플로였는지 훅이 알 수 없어 판정하지 않습니다. 그 구멍은 훅이 아니라 **적용
스크립트 쪽**에서 막습니다 — 이미 적용된 버전을 다시 적용하려 하면 스크립트가 거부합니다.

## 왜 "확인 요청"이 아니라 "차단"인가 — 이 환경에서 실측한 결과

Claude Code 의 권한 판정에는 세 가지가 있습니다. `allow`(통과) · `ask`(사람에게
확인) · `deny`(차단). 설계대로라면 이 훅은 `ask` 를 내서 확인 창을 띄우는 것이
맞습니다. 그런데 이 원격 실행 환경에서 실제로 시험해 보니 **`ask` 판정이 무시되고
쿼리가 그대로 실행됐습니다.**

시험은 존재하지 않는 표를 지우는 쿼리(`delete from __permission_probe_...`)로
했고, 결과는 다음과 같았습니다.

| 확인한 것 | 결과 |
|---|---|
| 훅이 호출되는가 | 호출된다 |
| 훅이 `ask` 를 냈을 때 | 확인 창 없이 쿼리가 실행됐다 |
| 훅이 `deny` 를 냈을 때 | 데이터베이스에 닿기 전에 차단됐다 |

그래서 이 훅은 `ask` 가 아니라 `deny` 를 냅니다. 이 환경에서 실제로 막히는
판정이 그것뿐이기 때문입니다.

## 통과도 침묵이 아니라 명시적 `allow` 로 낸다 — 확인 창이 계속 뜨던 원인

처음에는 읽기 전용 쿼리에서 훅이 아무 판정도 내지 않고 물러났습니다(정상 권한
흐름에 맡김). 설계 의도는 "허용 목록(`.claude/settings.json`)이 조회를 통과시킨다"
였지만, 실측 결과 그 의도가 이 원격 환경에서 지켜지지 않았습니다. 허용 목록이
main 에 들어간 지 일주일이 지난 2026-08-17 에도, claude.ai/code 대화형 세션에서는
순수 조회(`select ...`)마다 "Allow Claude to use Execute SQL?" 확인 창이 계속
떴습니다. 즉 **저장소에 체크인된 허용 목록만으로는 이 도구의 확인 창이 사라지지
않습니다.**

반면 훅이 내는 판정(`deny`)은 같은 세션들에서 확실히 동작하는 것이 확인돼
있습니다. 그래서 통과시킬 때도 침묵하는 대신 **같은 채널로 명시적 `allow` 를
출력합니다.** 훅의 `allow` 는 권한 흐름 자체를 건너뛰므로 확인 창이 뜨지 않습니다.

`allow` 를 내는 경우는 두 가지뿐입니다.

1. 모든 문장이 조회로 판정된 `execute_sql` 호출 — 아래 판단 기준을 전부 통과한 것.
2. 사람이 열쇠 파일로 이미 승인한 쓰기 — 열쇠를 만든 행위가 승인이므로,
   그 위에 확인 창을 한 번 더 띄우는 것은 중복이다.

정직하게 적어 둘 한계가 하나 있습니다. 명시적 `allow` 는 확인 창이라는 마지막
안전망까지 걷어냅니다. 그래서 "조회로 보이지만 실제로는 쓰는" 쿼리 — 예를 들어
데이터를 바꾸는 데이터베이스 함수를 `select fn(...)` 로 부르는 경우 — 는 이 훅이
가려내지 못한 채 통과합니다. 이것은 허용 목록으로 조회를 열어 두려던 원래 설계에도
똑같이 있던 한계이며, 이 저장소의 함수는 전부 내부에서 만든 것이라 감수합니다.

## 그러면 정말 필요한 쓰기는 어떻게 실행하나 — 일회용 열쇠

무조건 막기만 하면 정당한 작업까지 못 하게 됩니다. 그래서 사람이 허락했을 때만
열리는 통로를 하나 둡니다. **열쇠 파일**입니다.

    .claude/sql-write-unlock

이 파일이 있으면 쓰기 쿼리 **한 번**이 통과하고, 통과하는 즉시 파일이 지워집니다.
한 번 쓰면 없어지는 일회용 열쇠입니다. 그래서 열쇠를 한 번 만들어 두고 그 뒤로
계속 쓰는 일이 생기지 않습니다.

이 장치가 무엇을 보장하고 무엇을 보장하지 않는지 정직하게 적습니다.

- **보장하는 것:** 쓰기 쿼리는 열쇠를 만드는 별도의 동작 없이는 절대 실행되지
  않습니다. 그 동작은 대화 기록에 그대로 남아 사용자가 눈으로 봅니다. 즉 조용히
  지나가는 쓰기가 없습니다.
- **보장하지 않는 것:** Claude 가 파일을 만들 수 있으므로, Claude 가 스스로
  열쇠를 만드는 것을 기술적으로 막지는 못합니다. 이 훅이 막는 것은 **악의**가
  아니라 **부주의**입니다. 허락받지 않은 쓰기를 실수로 흘려보내는 일을 막고,
  모든 쓰기를 사용자 눈에 보이는 명시적 단계로 만듭니다.

## 판단 기준 — 확실할 때만 통과시킨다

이 훅은 "위험한 것을 찾아내면 막는" 방식이 아니라 **"안전한 것이 확실할 때만
통과시키는"** 방식입니다. 판단이 서지 않으면 통과가 아니라 차단 쪽으로 갑니다.
빠뜨린 패턴 하나가 곧 사고가 되기 때문입니다.

통과하려면 두 조건을 **모두** 만족해야 합니다.

1. 세미콜론으로 나눈 모든 문장이 조회를 여는 단어(`select`·`with`·`explain`·
   `show`·`values`·`table`)로 시작한다.
2. 쿼리 어디에도 데이터나 구조를 바꾸는 단어가 없다.

두 번째 조건을 검사하기 전에 주석과 문자열 리터럴을 먼저 지웁니다. 문자열 안의
`'delete'` 는 실행되는 명령이 아니라 그냥 글자이므로, 지우지 않으면 멀쩡한
조회가 차단되어 버립니다.

## 오탐은 감수한다

`explain analyze select ...` 처럼 실제로는 읽기만 하는 쿼리도 차단될 수 있습니다.
`analyze` 가 위험 단어 목록에 있기 때문입니다. 이것은 의도한 절충입니다. 한 번 더
열쇠를 만들어야 하는 불편과, 데이터가 조용히 지워지는 사고 중에서 앞을 택했습니다.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time

# 조회를 여는 단어. 문장이 이 중 하나로 시작하지 않으면 통과시키지 않는다.
READ_STARTERS = ("select", "with", "explain", "show", "values", "table")

# 데이터나 구조를 바꾸는 단어. 하나라도 나오면 통과시키지 않는다.
WRITE_WORDS = (
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "replace", "merge", "upsert", "grant", "revoke", "copy", "call", "do",
    "execute", "prepare", "vacuum", "analyze", "reindex", "cluster",
    "refresh", "lock", "listen", "notify", "unlisten", "discard", "reset",
    "set", "begin", "start", "commit", "rollback", "savepoint", "declare",
    "fetch", "move", "close", "into", "rename", "attach", "detach",
    "nextval", "setval", "pg_terminate_backend", "pg_cancel_backend",
    "dblink", "comment", "security",
)

WRITE_PATTERN = re.compile(
    r"\b(" + "|".join(WRITE_WORDS) + r")\b", re.IGNORECASE
)

UNLOCK_FILENAME = "sql-write-unlock"


# 열쇠를 쓴 직후 남기는 영수증. 원격 실행 환경에서는 한 번의 도구 호출에 이 훅이 여러 번
# 발동한다(2026-08-20 실측 — 첫 발동이 열쇠를 소진하고 통과를 내지만 다음 발동이 열쇠를
# 못 찾아 차단하고 그 결과가 최종으로 남았다). 그래서 **같은 호출의 재판정**을 통과시킨다.
#
# 같은 호출인지는 **지문**으로 가른다. 지문이 다르면 통과시키지 않으므로, 승인 한 번이
# 다른 쓰기로 번지지 않는다. 이것이 유효시간을 넉넉히 두고도 안전한 이유다.
RECEIPT_FILENAME = "sql-write-unlock.receipt"

# 유효시간을 15분으로 둔다. 60초였던 것을 늘린 이유는 실측이다 — 2026-08-31 에 한 번의
# `apply_migration` 호출에서 두 발동의 간격이 **481초(8분 1초)** 로 나왔다(지문은 두 발동이
# 동일했다). 60초 영수증은 그 사이에 만료돼, 사용자가 승인해 만든 열쇠가 첫 발동에서 소진되고
# 두 번째 발동이 차단을 내 **승인이 있어도 마이그레이션을 영영 적용할 수 없었다.**
#
# 왜 간격이 그렇게 벌어지나. 도구 호출이 한 번에 끝나지 않고 재시도되면 훅이 다시 발동하는데,
# 그 재시도까지의 시간은 훅이 통제할 수 없다. 그래서 "몇 초 안"이라는 시간 가정 대신 지문
# 대조를 안전장치의 중심에 두고, 시간은 넉넉한 상한으로만 쓴다.
RECEIPT_TTL_SECONDS = 900
# 이 훅이 지켜보는 도구.
#
# `execute_sql` 은 조회와 쓰기가 한 도구에 섞여 있어 쿼리문을 읽어 가려야 한다.
# `apply_migration` 은 가릴 것이 없다 — 마이그레이션은 정의상 데이터베이스의 구조를
# 바꾸는 스크립트이므로 전부 쓰기다. 그래서 쿼리 내용과 무관하게 열쇠를 요구한다.
SQL_TOOL = "mcp__Supabase__execute_sql"
MIGRATION_TOOL = "mcp__Supabase__apply_migration"

# 세 번째 감시면 — 셸로 라이브 데이터베이스를 바꾸는 스크립트.
#
# ## 왜 늘렸나 (2026-09-08 Fitstack 실측)
#
# 이 훅은 원래 커넥터 도구 두 개만 보았다. 그런데 저장소가 관리형 Supabase 에서 자체
# 호스팅으로 옮기면 마이그레이션을 적용하는 통로가 커넥터 호출에서 **셸 스크립트 실행**
# 으로 바뀐다(예: `sh selfhost/apply-migration.sh <파일>`). 통로가 옮겨간 뒤에도 훅이 옛
# 통로만 보고 있으면, 차단 장치는 아무도 다니지 않는 길을 지키고 **실제로 쓰는 길에는
# 승인 관문이 없다.** Fitstack 이 2026-09-03 에 자체 호스팅으로 컷오버한 뒤 정확히 그
# 상태였고, 그것을 메우려고 이 감시면을 더했다.
#
# ## 무엇을 라이브 쓰기로 보나 — 스크립트 두 종류만
#
# 조회 스크립트(예: `selfhost/query.sh`)는 세션 수준에서 읽기 전용이 강제되므로 보지
# 않는다. 그것까지 막으면 조회마다 승인이 필요해져 훅을 넣은 뜻이 무너진다.
LIVE_DB_WRITE_SCRIPTS = (
    ("apply-migration.sh", "마이그레이션을 라이브 데이터베이스에 적용하는 스크립트"),
    ("deploy-stack.sh", "스택을 다시 세우며 데이터베이스를 복원·교체할 수 있는 스크립트"),
    ("ssm-sql.py", "aws CLI 없이 라이브 데이터베이스에 SQL 을 보내는 대체 경로"),
)

# 한 줄에 여러 명령이 붙어 있으면 토막마다 따로 본다.
SEGMENT_SPLIT = re.compile(r"(?:\|\||&&|[;|\n])")

# 명령 앞에 붙는 감싸개. 이것들을 벗긴 다음 자리가 실제로 실행되는 명령이다.
#
# 인터프리터(`python3` 등)도 감싸개로 본다. 그러지 않으면 `python3 selfhost/ssm-sql.py …`
# 의 첫 자리가 `python3` 이라, 목록에 이름을 등재해도 걸리지 않는다(실측으로 확인했다).
# 벗긴 다음 자리가 목록에 있을 때만 판정하므로, `python3 manage.py …` 처럼 목록에 없는
# 파일을 부르는 명령에는 아무 영향이 없다. 다른 인터프리터로 부르는 통로가 생기면
# (예: `node …`) 그때 여기에 함께 더한다.
COMMAND_WRAPPERS = {
    "sh", "bash", "zsh", "dash", "sudo", "env", "time", "nohup", "exec", "xargs",
    "python", "python3",
}

BASH_TOOL = "Bash"

# 네 번째 감시면 — 저장소의 자동 작업(GitHub Actions 워크플로)으로 옮겨 간 적용 통로.
#
# ## 왜 필요한가
#
# 원격 세션(claude.ai/code)에는 클라우드 접속 자격이 없어서 적용 스크립트를 직접 실행할 수
# 없다. 그래서 적용을 저장소의 워크플로로 옮기고 세션은 그것을 "돌려 달라"고 부르게 된다.
# 그 순간 셸 감시면은 아무것도 보지 못한다 — 세션이 실행하는 명령이 없기 때문이다.
# **부르는 행위 자체가 라이브 쓰기**이므로 그 자리에 관문을 세운다.
#
# ## 무엇을 라이브 쓰기로 보나 — 목록에 적힌 워크플로 파일만
#
# 빌드·검사·배포 워크플로까지 막으면 관계없는 작업이 멈춘다(셸 감시면과 같은 이유다).
# 그래서 데이터베이스를 바꾸는 워크플로만 이름으로 적어 둔다. 새 통로를 만들면서 여기에
# 등재하지 않으면, 이 감시면이 태어난 그 상태(관문 없는 통로)로 되돌아간다.
ACTIONS_TOOL = "mcp__github__actions_run_trigger"
LIVE_DB_WRITE_WORKFLOWS = (
    ("db-migration-apply.yml", "마이그레이션을 라이브 데이터베이스에 적용하는 워크플로"),
)

WATCHED_TOOLS = (SQL_TOOL, MIGRATION_TOOL, BASH_TOOL, ACTIONS_TOOL)


def unlock_path() -> str:
    """열쇠 파일의 경로. 훅 파일 위치를 기준으로 삼아 실행 위치와 무관하게 같은 곳을 본다."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        UNLOCK_FILENAME)


def receipt_path() -> str:
    """영수증 경로. 열쇠와 같은 곳에 둔다."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        RECEIPT_FILENAME)


def call_fingerprint(payload: dict) -> str:
    """이 도구 호출의 지문. 도구 이름과 입력이 같으면 같은 지문이 된다."""
    ti = payload.get("tool_input") or {}
    raw = "\0".join([
        str(payload.get("tool_name") or ""),
        str(ti.get("name") or ""),
        str(ti.get("query") or ""),
        # 셸 명령도 지문에 넣는다. 넣지 않으면 모든 Bash 호출의 지문이 같아져,
        # 한 번 승인한 명령의 영수증으로 **다른 명령**이 통과할 수 있다.
        str(ti.get("command") or ""),
        # 워크플로 실행 요청도 같은 이유로 넣는다. 어느 워크플로를, 어느 브랜치에서,
        # 어떤 입력으로 돌리는지가 모두 달라지면 다른 승인이어야 한다.
        str(ti.get("workflow_id") or ""),
        str(ti.get("ref") or ""),
        json.dumps(ti.get("inputs") or {}, sort_keys=True, ensure_ascii=False),
    ])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def consume_unlock(fingerprint: str) -> bool:
    """열쇠가 있으면 쓰고 영수증을 남긴다. 영수증은 **같은 지문의 재판정**만 통과시킨다.

    ## 무엇이 이 함수를 어렵게 만드나

    한 번의 도구 호출에 이 훅이 여러 번 발동한다. 열쇠는 일회용이라 첫 발동이 그것을 소진하고,
    남은 발동들은 열쇠를 못 찾아 차단을 내며, **그 차단이 최종 판정으로 남는다.** 그래서 열쇠를
    쓸 때 영수증을 남겨 같은 호출의 재판정을 함께 통과시킨다.

    ## 무엇으로 "같은 호출"을 가르나 — 시간이 아니라 지문이다

    앞선 판(2026-08-20)은 시간만 봤다. 지문을 대조하려 했더니 두 발동의 입력이 달라 짝이
    맞지 않는다는 관찰이 있었기 때문이다. 그 대신 "방금 열쇠를 썼고 아직 60초 안이다"만 보고,
    느슨해진 만큼을 **영수증 일회성**으로 되받았다.

    그 설계가 2026-08-31 에 두 곳에서 깨졌다.

      1. **간격.** 한 `apply_migration` 호출의 두 발동이 481초(8분 1초) 떨어져 있었다. 60초
         영수증은 그 사이에 만료돼, 승인해 만든 열쇠가 있어도 적용이 영영 되지 않았다.
      2. **지문.** 같은 실측에서 두 발동의 지문은 **똑같았다**(`tool_name` + `name` + `query`).
         지문이 갈린다는 전제 자체가 지금은 사실이 아니다.

    그래서 축을 바꾼다. **지문이 같아야 통과**시키고, 시간은 넉넉한 상한으로만 쓴다. 이것은
    앞선 판보다 **느슨하지 않고 더 촘촘하다** — 예전에는 60초 안이면 *아무* 쓰기나 한 번
    통과했지만, 이제는 승인받은 그 호출과 글자까지 같은 것만 통과한다.

    ## 왜 재사용 횟수를 세지 않나

    지문이 같다는 것은 곧 같은 도구·같은 이름·같은 쿼리라는 뜻이다. 그것이 몇 번 재판정되든
    사용자가 승인한 그 한 가지 일이므로, 횟수를 세면 환경이 발동 횟수를 바꿀 때마다 같은
    방식으로 다시 깨진다. 승인의 범위를 좁히는 일은 횟수가 아니라 지문이 한다.

    ## 지문이 다른 영수증을 만나면

    통과시키지 않고 영수증도 남겨 둔다. 남의 승인을 빌려 쓰지 않으면서, 원래 호출의 재판정이
    아직 오지 않았을 가능성을 막지 않기 위해서다. 유효시간이 지나면 자연히 무효가 된다.
    """
    path = unlock_path()
    try:
        os.remove(path)
    except FileNotFoundError:
        saved_fp, saved_at = read_receipt()
        if saved_at is None:
            return False
        if (time.time() - saved_at) > RECEIPT_TTL_SECONDS:
            # 만료된 영수증은 그 자리에서 버린다 — 찌꺼기를 남기지 않는다.
            try:
                os.remove(receipt_path())
            except OSError:
                pass
            return False
        # 지문이 다르면 남의 승인이다. 통과시키지 않고, 영수증은 원래 주인을 위해 남겨 둔다.
        return saved_fp == fingerprint
    except OSError:
        return False

    try:
        with open(receipt_path(), "w", encoding="utf-8") as f:
            f.write(f"{fingerprint} {time.time()}")
    except OSError:
        pass
    return True


def read_receipt():
    """영수증의 (지문, 시각). 없거나 읽을 수 없으면 (None, None)."""
    try:
        with open(receipt_path(), encoding="utf-8") as f:
            parts = f.read().strip().split(" ")
        return parts[0], float(parts[-1])
    except (FileNotFoundError, ValueError, OSError, IndexError):
        return None, None


def repo_root() -> str:
    """훅 파일 위치를 기준으로 저장소 뿌리를 잡는다(.claude/hooks/ 의 두 단계 위)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def git(*args):
    """git 을 조용히 돌린다. 실패하면 None — '잴 수 없다'와 '재서 아니다'를 가르기 위해서다."""
    try:
        out = subprocess.run(
            ("git",) + args,
            cwd=repo_root(),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def migration_push_state(name: str):
    """마이그레이션 파일이 origin 에 push 돼 있는지 본다.

    돌려주는 값은 (판정, 설명) 이고 판정은 셋 중 하나다.

        "pushed"    파일이 커밋돼 있고 그 커밋이 원격에서 닿는다 — 적용해도 좋다
        "unpushed"  파일이 없거나, 수정이 커밋되지 않았거나, 커밋이 원격에 없다 — 막는다
        "unknown"   git 이 없거나 원격이 없어 잴 수 없다 — 막지 않고 그 사실만 알린다

    ## 왜 이것을 보나 (2026-08-18 실제 사고)

    한 세션이 발효 함수의 결함 넷을 고쳐 라이브에 적용했는데 **그 파일을 push 하지
    않았다.** 13분 뒤 다른 세션이 같은 함수를 고치면서 저장소만 보고 본문을 썼고,
    `create or replace` 가 앞의 수정을 통째로 되돌렸다. push 돼 있었다면 뒤 세션이
    그 파일을 보고 위에 얹었을 것이다. 즉 **적용 전 push 는 예의가 아니라 다른
    세션이 내 변경을 읽을 수 있게 만드는 유일한 통로**다.

    ## 왜 열쇠로 넘어갈 수 없게 했나

    열쇠(`sql-write-unlock`)는 "사용자가 이 SQL 을 보고 승인했다"를 뜻한다. 그런데
    push 여부는 사용자가 승인으로 대체할 수 있는 성질의 것이 아니다 — 승인하든 안 하든,
    push 하지 않은 채 적용하면 다음 세션이 그것을 볼 방법이 없다. 그래서 이 판정은
    열쇠보다 **앞에** 두고 열쇠로 열리지 않게 했다. 막힌 쪽은 push 한 뒤 다시 부르면
    그대로 통과한다.

    ## "잴 수 없으면 막지 않는다" — 이 하나만 예외로 둔 이유

    이 파일의 다른 판정은 애매하면 차단으로 간다. 여기서만 반대로 한 것은, git 이 없는
    환경에서 차단하면 **되돌림과 무관한 정상 작업이 영구히 막히기** 때문이다. 대신
    "재지 못했다"는 사실을 판정 이유에 실어 침묵하지 않는다.
    """
    if not name:
        return "unknown", "마이그레이션 이름이 비어 있어 대응하는 파일을 찾을 수 없었습니다"

    rel = os.path.join("supabase", "migrations", f"{name}.sql")
    if git("rev-parse", "--git-dir") is None:
        return "unknown", "git 저장소가 아니거나 git 을 쓸 수 없어 push 여부를 재지 못했습니다"

    if not os.path.exists(os.path.join(repo_root(), rel)):
        return "unpushed", (
            f"넘긴 이름에 대응하는 파일 `{rel}` 이 저장소에 없습니다. "
            "적용 이력에는 이 이름이 박히는데 저장소에는 파일이 없으면, 다음 사람이 "
            "무엇이 적용됐는지 확인할 방법이 없습니다(고아 마이그레이션)"
        )

    dirty = git("status", "--porcelain", "--", rel)
    if dirty:
        return "unpushed", f"`{rel}` 에 커밋하지 않은 변경이 있습니다 — 적용하는 내용과 저장소의 내용이 갈립니다"

    commit = git("log", "-1", "--format=%H", "--", rel)
    if not commit:
        return "unpushed", f"`{rel}` 이 아직 커밋되지 않았습니다"

    remotes = git("branch", "-r", "--contains", commit)
    if remotes is None:
        return "unknown", f"`{rel}` 의 커밋이 원격에 있는지 재지 못했습니다"
    if not remotes.strip():
        return "unpushed", (
            f"`{rel}` 은 커밋됐지만 그 커밋({commit[:8]})이 원격 어디에도 없습니다 — "
            "아직 push 되지 않았습니다"
        )

    return "pushed", f"`{rel}` 이 원격에 있습니다 ({remotes.split()[0]})"


def bash_live_db_write(command: str):
    """셸 명령이 라이브 데이터베이스를 바꾸는 스크립트를 **실행하는가**.

    돌려주는 값은 `(설명, sql 인자)` 이고, 해당 없으면 `(None, None)` 이다.

    ## 왜 "글자가 들어 있는가"가 아니라 "실행하는가"로 보나

    Bash 는 세션이 거의 모든 일을 하는 통로다. 명령 문자열에 스크립트 이름이 보이기만
    하면 막는다면, 그 파일을 **읽는** 정상 작업(`cat selfhost/apply-migration.sh`)까지
    막혀 훅이 일을 방해하는 장치가 된다. 그래서 명령 토막에서 감싸개(`sh`·`sudo` 등)와
    앞선 환경변수 지정을 벗긴 **첫 자리**가 그 스크립트일 때만 실행으로 본다.

    ## 이 판정만 애매할 때 막지 않는 이유

    이 파일의 다른 판정은 애매하면 차단으로 간다. 여기서만 반대인 것은 감시면이 SQL 한
    줄이 아니라 **셸 전체**이기 때문이다. 확실하지 않은 것까지 막기 시작하면 관계없는
    작업이 멈추고, 그러면 사람이 훅 자체를 꺼 버린다. 정직하게 적어 둘 한계는 이렇다 —
    스크립트를 거치지 않고 데이터베이스에 직접 붙는 명령(원격에서 psql 을 띄우는 등)은
    이 훅이 가려내지 못하므로, 그 경로에서는 규칙 문서의 세 단계(보여 주기·승인·실행)를
    Claude 가 스스로 지켜야 한다.
    """
    for segment in SEGMENT_SPLIT.split(command or ""):
        tokens = [t for t in segment.strip().split() if t]
        while tokens:
            head = tokens[0]
            bare = os.path.basename(head.strip("\"'()"))
            is_env_assign = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", head) is not None
            if is_env_assign or head.startswith("-") or bare in COMMAND_WRAPPERS:
                tokens.pop(0)
                continue
            break
        if not tokens:
            continue
        target = os.path.basename(tokens[0].strip("\"'()"))
        for script, label in LIVE_DB_WRITE_SCRIPTS:
            if target == script:
                sql_arg = next(
                    (t.strip("\"'") for t in tokens[1:] if t.strip("\"'").endswith(".sql")),
                    None,
                )
                return label, sql_arg
    return None, None


def actions_live_db_write(tool_input: dict):
    """GitHub Actions 실행 요청이 라이브 데이터베이스를 바꾸는 워크플로를 **새로 돌리는가**.

    돌려주는 값은 `(설명, sql 인자)` 이고, 해당 없으면 `(None, None)` 이다.

    ## 무엇을 보고 가르나

    두 가지가 모두 맞아야 라이브 쓰기로 본다. 첫째, 요청이 **새 실행**(`run_workflow`)이어야
    한다. 둘째, 부르려는 워크플로 파일 이름이 `LIVE_DB_WRITE_WORKFLOWS` 에 있어야 한다.
    워크플로를 번호(숫자 id)로 부르는 것도 가능한데, 번호만으로는 어느 파일인지 알 수 없으므로
    가리지 못한다 — 그 한계는 파일 상단에 적어 두었고 적용 스크립트의 중복 적용 거부가 받친다.
    """
    if (tool_input or {}).get("method") != "run_workflow":
        return None, None

    workflow = os.path.basename(str(tool_input.get("workflow_id") or "").strip().strip("\"'"))
    for name, label in LIVE_DB_WRITE_WORKFLOWS:
        if workflow == name:
            inputs = tool_input.get("inputs") or {}
            sql_arg = next(
                (str(v) for v in inputs.values() if str(v).endswith(".sql")),
                None,
            )
            return label, sql_arg
    return None, None


def push_gate(label: str, sql_arg):
    """적용 전 push 판정 — 셸 통로와 워크플로 통로가 **같은 순서, 같은 문구**를 쓰게 한다.

    돌려주는 값은 `(설명, 차단 사유)` 이고, 차단 사유가 채워져 있으면 그 자리에서 막아야
    한다. 넘긴 인자가 마이그레이션 파일이 아닐 때는 잴 것이 없으므로 그대로 통과시킨다
    (그래도 뒤의 열쇠 판정은 남아 있다).
    """
    if not sql_arg:
        return label, None

    name = os.path.basename(sql_arg)
    if name.endswith(".sql"):
        name = name[: -len(".sql")]
    state, detail = migration_push_state(name)
    if state == "unpushed":
        return label, push_denied_reason(detail)
    if state == "unknown":
        return f"{label} (참고: 적용 전 push 여부를 재지 못했습니다 — {detail})", None
    return label, None


def push_denied_reason(detail: str) -> str:
    """적용 전 push 판정에 걸렸을 때의 설명. 커넥터 통로와 셸 통로가 같은 문구를 쓴다."""
    return (
        f"적용 전 push 판정에 걸렸습니다 — {detail}. "
        "마이그레이션은 라이브에 적용하기 **전에** 파일을 커밋하고 push 해야 합니다. "
        "2026-08-18 에 한 세션이 발효 함수의 결함 넷을 고쳐 적용하면서 파일을 push 하지 "
        "않았고, 13분 뒤 다른 세션이 저장소만 보고 같은 함수를 고쳐 그 수정을 통째로 "
        "되돌렸습니다. push 는 다른 세션이 내 변경을 읽을 수 있게 하는 유일한 통로입니다. "
        "이 판정은 열쇠로 넘어갈 수 없습니다 — push 한 뒤 다시 실행하십시오."
    )


def strip_noise(sql: str) -> str:
    """주석과 문자열 리터럴을 지운다.

    문자열 안에 든 단어는 실행되는 명령이 아니라 값이므로, 위험 단어를 찾기
    전에 지워야 멀쩡한 조회가 잘못 걸리지 않는다.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)          # /* 블록 주석 */
    sql = re.sub(r"--[^\n]*", " ", sql)                        # -- 줄 주석
    sql = re.sub(r"\$([A-Za-z_]*)\$.*?\$\1\$", " '' ", sql, flags=re.S)  # $$ 본문 $$
    sql = re.sub(r"'(?:[^']|'')*'", " '' ", sql)               # '문자열'
    return sql


def classify(query: str):
    """(통과시켜도 되는가, 왜 아닌가) 를 돌려준다."""
    cleaned = strip_noise(query)

    statements = [s.strip() for s in cleaned.split(";")]
    statements = [s for s in statements if s]
    if not statements:
        return False, "읽기 전용 쿼리가 아닙니다 — 쿼리가 비어 있어 무엇을 실행하는지 판단할 수 없습니다"

    for statement in statements:
        first = re.split(r"[\s(]+", statement.lstrip("("), 1)[0].lower()
        if first not in READ_STARTERS:
            return False, f"읽기 전용 쿼리가 아닙니다 — 조회로 시작하지 않는 문장이 있습니다 ({first})"

    found = WRITE_PATTERN.search(cleaned)
    if found:
        return False, f"읽기 전용 쿼리가 아닙니다 — 데이터나 구조를 바꾸는 단어가 있습니다 ({found.group(1).lower()})"

    return True, None


def emit(decision: str, reason: str) -> None:
    """권한 판정을 표준 출력으로 낸다. allow 는 확인 창을 건너뛰고, deny 는 실행을 막는다."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # 입력을 못 읽으면 판단하지 않고 원래 권한 흐름에 맡긴다.
        return 0

    tool = payload.get("tool_name")
    if tool not in WATCHED_TOOLS:
        return 0

    if tool == BASH_TOOL:
        command = (payload.get("tool_input") or {}).get("command") or ""
        label, sql_arg = bash_live_db_write(command)
        if not label:
            # 라이브 데이터베이스를 바꾸는 명령이 아니다 — 아무 판정도 내지 않고 물러난다.
            # Bash 는 일반 작업 통로이므로, 여기서 침묵하는 것이 이 감시면의 전제다.
            return 0

        # 커넥터 통로와 같은 순서를 지킨다 — 적용 전 push 판정이 열쇠보다 앞이다.
        # 스크립트 자신도 같은 확인을 하지만, 훅에도 두어 스크립트가 바뀌어도 이 순서가
        # 남게 한다. 넘긴 인자가 마이그레이션 파일일 때만 판정한다.
        label, deny_reason = push_gate(label, sql_arg)
        if deny_reason:
            emit("deny", deny_reason)
            return 0

        reason = f"셸로 라이브 데이터베이스를 바꾸는 명령입니다 — {label}"
    elif tool == ACTIONS_TOOL:
        label, sql_arg = actions_live_db_write(payload.get("tool_input") or {})
        if not label:
            # 라이브 데이터베이스를 바꾸는 워크플로가 아니다 — 빌드·검사·배포를 부르는
            # 요청은 그대로 지나가야 하므로 아무 판정도 내지 않는다.
            return 0

        label, deny_reason = push_gate(label, sql_arg)
        if deny_reason:
            emit("deny", deny_reason)
            return 0

        reason = (
            "라이브 데이터베이스를 바꾸는 워크플로를 실행하려는 요청입니다"
            f" — {label}"
        )
    elif tool == MIGRATION_TOOL:
        # 열쇠보다 **앞에** 두는 판정. 열쇠는 "사용자가 이 SQL 을 승인했다"를 뜻하는데,
        # push 여부는 승인으로 대체될 수 없다 — push 하지 않고 적용하면 다음 세션이 그
        # 변경을 볼 방법이 아예 없기 때문이다. 2026-08-18 되돌림 사고의 원인 절반이 이것이다.
        name = (payload.get("tool_input") or {}).get("name") or ""
        state, detail = migration_push_state(name)
        if state == "unpushed":
            emit("deny", push_denied_reason(detail))
            return 0
        if state == "unknown":
            reason = (
                "마이그레이션은 데이터베이스의 구조를 바꾸는 스크립트입니다"
                f" (참고: 적용 전 push 여부를 재지 못했습니다 — {detail})"
            )
        else:
            reason = "마이그레이션은 데이터베이스의 구조를 바꾸는 스크립트입니다"
    else:
        query = (payload.get("tool_input") or {}).get("query") or ""
        is_read_only, reason = classify(query)
        if is_read_only:
            # 침묵(정상 권한 흐름 위임)이 아니라 명시적 allow 를 낸다.
            # 이 원격 환경에서는 허용 목록만으로 확인 창이 사라지지 않는 것이
            # 실측됐기 때문이다(파일 상단 설명 참조).
            emit("allow", "읽기 전용 조회입니다 — 데이터를 바꾸지 않으므로 자동 승인합니다.")
            return 0

    # 사람이 허락해 둔 일회용 열쇠가 있으면 이번 한 번만 통과시킨다.
    # 열쇠를 만든 행위가 곧 사용자의 승인이므로, 확인 창을 또 띄우지 않는다.
    if consume_unlock(call_fingerprint(payload)):
        emit("allow", "사용자가 열어 둔 일회용 열쇠를 사용해 실행합니다. 열쇠는 지금 지워졌으며, "
                      "같은 쿼리의 재판정만 짧은 시간 안에 함께 통과합니다.")
        return 0

    emit("deny", (
        f"{reason}. "
        "데이터베이스의 데이터나 구조를 바꾸는 일은 사용자의 허락 없이 실행할 수 없습니다. "
        f"사용자가 승인했다면 열쇠 파일({UNLOCK_FILENAME})을 만든 뒤 다시 실행하십시오. "
        "열쇠는 한 번 쓰면 사라집니다."
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())

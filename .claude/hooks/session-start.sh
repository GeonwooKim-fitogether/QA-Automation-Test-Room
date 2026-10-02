#!/bin/bash
# SessionStart hook — provisions the environment that bundled QA skills need.
#
# This repo is a GitHub template ("Use this template"). Every repo copied from
# it inherits this hook, so new repos get the same environment automatically.
#
# Job: install the Python `playwright` package that the `webapp-testing` skill
# uses. The managed web environment ships Chromium browser binaries in
# /opt/pw-browsers but NOT the pip package, and the package version must match
# the bundled Chromium build or the default launch() fails.
set -euo pipefail

# --- 폐기 목록 안내 (past-decisions 규칙) -----------------------------------
# 규칙(.claude/rules/)은 세션 시작 시 자동으로 읽히는데 결정 기록은 읽히지
# 않는다. 그래서 새 세션은 "어떻게 일할 것인가"는 알고 들어오면서 "무엇을 하지
# 않기로 정했나"는 모른 채 들어온다. 이 한 줄이 그 구멍을 메운다.
#
# .claude/retired.json 이 없는 저장소에서는 아무것도 출력하지 않는다.
# 이 안내는 환경과 무관하게 항상 돌아야 하므로 아래 원격 전용 구간보다 앞에 둔다.
if [ -f "$(dirname "$0")/retired-guard.py" ]; then
  python3 "$(dirname "$0")/retired-guard.py" --announce 2>/dev/null || true
fi

# Only run in the remote (Claude Code on the web) environment. Locally, leave
# the user's own Python setup untouched.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Skip if there's no managed browser bundle (nothing to match against).
if [ ! -d /opt/pw-browsers ]; then
  echo "[session-start] /opt/pw-browsers not found; skipping playwright setup."
  exit 0
fi

# Pin the playwright version that matches the bundled Chromium build.
# Default 1.56.0 == Chromium build 1194 (this environment).
PLAYWRIGHT_VERSION="1.56.0"

# If a different Chromium build is present, surface it so the pin can be updated.
DETECTED_BUILD="$(ls -d /opt/pw-browsers/chromium-* 2>/dev/null | grep -oE '[0-9]+$' | head -1 || true)"
if [ -n "$DETECTED_BUILD" ] && [ "$DETECTED_BUILD" != "1194" ]; then
  echo "[session-start] WARNING: Chromium build $DETECTED_BUILD detected, but the"
  echo "[session-start]          hook pins playwright==$PLAYWRIGHT_VERSION (build 1194)."
  echo "[session-start]          If webapp-testing fails to launch, update PLAYWRIGHT_VERSION"
  echo "[session-start]          in .claude/hooks/session-start.sh to the matching version."
fi

# Idempotent: pip is a no-op if the exact version is already satisfied.
echo "[session-start] Installing playwright==$PLAYWRIGHT_VERSION (Python package)..."
pip install --quiet "playwright==$PLAYWRIGHT_VERSION"

echo "[session-start] Done. webapp-testing skill is ready."

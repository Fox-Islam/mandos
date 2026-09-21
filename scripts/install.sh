#!/usr/bin/env sh
set -eu

SOURCE="${MANDOS_SOURCE:-${MANDOS_REPO:-https://github.com/Fox-Islam/mandos}}"

resolve_uv_package_spec() {
  case "$1" in
    git+* | file:*)
      printf '%s\n' "$1"
      ;;
    http://* | https://* | ssh://*)
      printf 'git+%s\n' "$1"
      ;;
    *)
      if [ -d "$1" ]; then
        (cd "$1" && pwd -P)
      else
        printf '%s\n' "$1"
      fi
      ;;
  esac
}

PACKAGE_SPEC="$(resolve_uv_package_spec "${SOURCE}")"

if [ "${PACKAGE_SPEC#git+}" != "${PACKAGE_SPEC}" ] && ! command -v git >/dev/null 2>&1; then
  echo "Git is required for the current git-URL install source." >&2
  echo "Install Git, then rerun this command. A future PyPI release will remove this prerequisite." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (astral.sh/uv)..."
  curl -fsSL https://astral.sh/uv/install.sh | sh
  if [ -f "${HOME}/.local/bin/env" ]; then
    . "${HOME}/.local/bin/env"
  fi
  PATH="${HOME}/.local/bin:${PATH}"
  export PATH
fi

TOOL_BIN="$(uv tool dir --bin 2>/dev/null || true)"
if [ -n "${TOOL_BIN}" ]; then
  PATH="${TOOL_BIN}:${PATH}"
  export PATH
fi

PYTHON_CMD="$(uv python find 3.13 2>/dev/null || true)"
if [ -z "${PYTHON_CMD}" ]; then
  echo "Installing Python 3.13 with uv..."
  uv python install 3.13
  PYTHON_CMD="$(uv python find 3.13)"
fi

echo "Installing mandos from ${PACKAGE_SPEC} ..."
echo "Refreshing any previous mandos tool environment..."
uv tool uninstall mandos >/dev/null 2>&1 || true
uv cache clean mandos >/dev/null 2>&1 || true
uv tool install --force --reinstall --refresh --python "${PYTHON_CMD}" "${PACKAGE_SPEC}"
uv tool update-shell >/dev/null 2>&1 || true

if command -v mandos >/dev/null 2>&1; then
  MANDOS_BIN="$(command -v mandos)"
else
  MANDOS_BIN="${TOOL_BIN}/mandos"
fi

if [ ! -x "${MANDOS_BIN}" ]; then
  echo "Installed mandos executable was not found at ${MANDOS_BIN}." >&2
  exit 1
fi

if ! "${MANDOS_BIN}" --help >/dev/null 2>&1; then
  TOOL_DIR="$(uv tool dir 2>/dev/null || true)"
  echo "Installed mandos did not launch." >&2
  if [ -n "${TOOL_DIR}" ]; then
    echo "Inspect ${TOOL_DIR}/mandos/pyvenv.cfg for the Python home used by uv." >&2
  fi
  exit 1
fi

echo ""
echo "Installed two commands:"
echo "  mandos      - the configurator TUI (run this next)"
echo "  mandos-mcp  - the stdio MCP server your harnesses spawn"
echo ""
if command -v mandos >/dev/null 2>&1; then
  echo "Next: run 'mandos' to assemble your council."
else
  echo "Next: open a new terminal, then run 'mandos' to assemble your council."
  if [ -n "${TOOL_BIN}" ]; then
    echo "If needed, add this directory to PATH: ${TOOL_BIN}"
  fi
fi

#!/usr/bin/env bash

set -euo pipefail

# ============================================================
# GREMLIN 2.0
# Portable local development-agent launcher
# ============================================================

SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1
    pwd -P
)"

AGENT="$SCRIPT_DIR/gremlin.py"
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

MODEL="${GREMLIN_MODEL:-qwen3-coder:30b}"

WORKSPACE_OVERRIDE=""
MODEL_OVERRIDE=""
DEBUG=false
CHECK_MODEL=true
AUTO_PULL=false


# ============================================================
# COLORS
# ============================================================

if [[ -t 1 ]]; then
    RED=$'\033[91m'
    GREEN=$'\033[92m'
    YELLOW=$'\033[93m'
    BLUE=$'\033[94m'
    CYAN=$'\033[96m'
    GRAY=$'\033[90m'
    RESET=$'\033[0m'
else
    RED=""
    GREEN=""
    YELLOW=""
    BLUE=""
    CYAN=""
    GRAY=""
    RESET=""
fi


# ============================================================
# OUTPUT
# ============================================================

info() {
    printf '%s→%s %s\n' "$CYAN" "$RESET" "$*"
}

success() {
    printf '%s✓%s %s\n' "$GREEN" "$RESET" "$*"
}

warning() {
    printf '%s!%s %s\n' "$YELLOW" "$RESET" "$*"
}

error() {
    printf '%sERROR:%s %s\n' "$RED" "$RESET" "$*" >&2
}

die() {
    error "$@"
    exit 1
}


# ============================================================
# HELP
# ============================================================

show_help() {
    cat <<'EOF'

Gremlin 2.0 — local development agent

Usage:
    ./launch.sh [options]

Options:

    --workspace PATH
        Use PATH as the development workspace.

    --model MODEL
        Use a different Ollama model.

    --no-model-check
        Do not check whether the configured model exists.

    --pull-model
        Automatically pull the configured model if missing.

    --debug
        Enable Bash debugging.

    -h, --help
        Show this help.

Environment variables:

    GREMLIN_WORKSPACE
        Default workspace override.

    GREMLIN_MODEL
        Default Ollama model.

    GREMLIN_COMMAND_TIMEOUT
        Command timeout in seconds.
        Default: 120

    GREMLIN_MAX_OUTPUT
        Maximum command output characters.
        Default: 20000

    GREMLIN_MAX_FILE_SIZE
        Maximum file size in bytes.
        Default: 2000000

Examples:

    ./launch.sh

    ./launch.sh --model qwen3-coder:30b

    ./launch.sh --workspace ~/Projects/my-game

    GREMLIN_MODEL=qwen3-coder:30b ./launch.sh

EOF
}


# ============================================================
# ARGUMENTS
# ============================================================

while [[ $# -gt 0 ]]; do
    case "$1" in

        --workspace)
            [[ $# -ge 2 ]] || die "--workspace requires a path."
            WORKSPACE_OVERRIDE="$2"
            shift 2
            ;;

        --model)
            [[ $# -ge 2 ]] || die "--model requires a model name."
            MODEL_OVERRIDE="$2"
            shift 2
            ;;

        --no-model-check)
            CHECK_MODEL=false
            shift
            ;;

        --pull-model)
            AUTO_PULL=true
            shift
            ;;

        --debug)
            DEBUG=true
            shift
            ;;

        -h|--help)
            show_help
            exit 0
            ;;

        *)
            die "Unknown option: $1"
            ;;
    esac
done


if [[ "$DEBUG" == true ]]; then
    set -x
fi


if [[ -n "$MODEL_OVERRIDE" ]]; then
    MODEL="$MODEL_OVERRIDE"
fi


# ============================================================
# WORKSPACE
# ============================================================

if [[ -n "$WORKSPACE_OVERRIDE" ]]; then
    WORKSPACE="$WORKSPACE_OVERRIDE"
elif [[ -n "${GREMLIN_WORKSPACE:-}" ]]; then
    WORKSPACE="$GREMLIN_WORKSPACE"
else
    WORKSPACE="$SCRIPT_DIR"
fi


# Resolve an existing workspace without relying on PWD.
if [[ -e "$WORKSPACE" ]]; then
    WORKSPACE="$(
        cd -- "$WORKSPACE" >/dev/null 2>&1 &&
        pwd -P
    )"
else
    die "Workspace does not exist: $WORKSPACE"
fi


# ============================================================
# HEADER
# ============================================================

printf '\n'
printf '%s============================================================%s\n' "$CYAN" "$RESET"
printf '%s GREMLIN 2.0 — LOCAL DEVELOPMENT AGENT%s\n' "$CYAN" "$RESET"
printf '%s============================================================%s\n' "$CYAN" "$RESET"
printf '\n'

printf '%sLauncher:%s  %s\n' "$GRAY" "$RESET" "$SCRIPT_DIR"
printf '%sWorkspace:%s %s\n' "$GRAY" "$RESET" "$WORKSPACE"
printf '%sModel:%s     %s\n' "$GRAY" "$RESET" "$MODEL"
printf '\n'


# ============================================================
# VERIFY AGENT
# ============================================================

[[ -f "$AGENT" ]] || die \
    "gremlin.py was not found beside launch.sh.

Expected:
    $AGENT"


# ============================================================
# FIND PYTHON
# ============================================================

if [[ -n "${PYTHON:-}" ]]; then
    SYSTEM_PYTHON="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    SYSTEM_PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    SYSTEM_PYTHON="$(command -v python)"
else
    die "Python 3 was not found in PATH."
fi


# ============================================================
# PYTHON VERSION
# ============================================================

PYTHON_VERSION="$(
    "$SYSTEM_PYTHON" -c \
        'import sys; print(".".join(map(str, sys.version_info[:3])))'
)"

PYTHON_MAJOR="$(
    "$SYSTEM_PYTHON" -c \
        'import sys; print(sys.version_info[0])'
)"

PYTHON_MINOR="$(
    "$SYSTEM_PYTHON" -c \
        'import sys; print(sys.version_info[1])'
)"

if [[ "$PYTHON_MAJOR" -lt 3 ]]; then
    die "Python 3 is required. Found Python $PYTHON_VERSION."
fi

if [[ "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -lt 10 ]]; then
    die "Python 3.10 or newer is required. Found Python $PYTHON_VERSION."
fi

success "Python $PYTHON_VERSION"


# ============================================================
# VIRTUAL ENVIRONMENT
# ============================================================

if [[ ! -x "$VENV_PYTHON" ]]; then

    info "Creating local virtual environment..."

    if ! "$SYSTEM_PYTHON" -m venv "$VENV_DIR"; then
        echo
        error "Could not create the Python virtual environment."
        echo
        echo "On Debian/Ubuntu you may need:"
        echo
        echo "    sudo apt install python3-venv"
        echo
        exit 1
    fi

    success "Virtual environment created."

else

    success "Virtual environment found."

fi


if [[ ! -x "$VENV_PYTHON" ]]; then
    die "Virtual environment is broken: $VENV_DIR"
fi


VENV_VERSION="$(
    "$VENV_PYTHON" -c \
        'import sys; print(".".join(map(str, sys.version_info[:3])))'
)"

success "Virtual environment Python $VENV_VERSION"


# ============================================================
# PIP
# ============================================================

if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
    info "Bootstrapping pip..."

    "$VENV_PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 || \
        die "Could not bootstrap pip."
fi


# ============================================================
# PYTHON DEPENDENCY
# ============================================================

if "$VENV_PYTHON" -c "import ollama" >/dev/null 2>&1; then

    success "Ollama Python package available."

else

    info "Installing Ollama Python package..."

    "$VENV_PYTHON" -m pip install --upgrade ollama || {
        die "Could not install the Ollama Python package."
    }

    success "Ollama Python package installed."

fi


# ============================================================
# OLLAMA
# ============================================================

if ! command -v ollama >/dev/null 2>&1; then

    error "Ollama was not found in PATH."
    echo
    echo "Gremlin requires Ollama."
    echo
    echo "Verify with:"
    echo
    echo "    ollama --version"
    echo

    exit 1
fi


OLLAMA_VERSION="$(
    ollama --version 2>/dev/null || echo "unknown"
)"

success "Ollama found: $OLLAMA_VERSION"


# ============================================================
# OLLAMA SERVER
# ============================================================

if ! ollama list >/dev/null 2>&1; then

    warning "Ollama server is not responding."
    info "Attempting to start Ollama..."

    OLLAMA_LOG="${TMPDIR:-/tmp}/gremlin-ollama.log"

    nohup ollama serve \
        >"$OLLAMA_LOG" \
        2>&1 &

    OLLAMA_PID=$!

    SERVER_READY=false

    for _ in {1..40}; do

        sleep 0.25

        if ollama list >/dev/null 2>&1; then
            SERVER_READY=true
            break
        fi

        if ! kill -0 "$OLLAMA_PID" >/dev/null 2>&1; then
            break
        fi

    done

    if [[ "$SERVER_READY" != true ]]; then

        warning "Could not start Ollama automatically."

        echo
        echo "Try:"
        echo
        echo "    ollama serve"
        echo
        echo "Log:"
        echo
        echo "    $OLLAMA_LOG"
        echo

        exit 1
    fi

    success "Ollama server started."

else

    success "Ollama server is responding."

fi


# ============================================================
# MODEL
# ============================================================

if [[ "$CHECK_MODEL" == true ]]; then

    info "Checking model: $MODEL"

    MODEL_EXISTS=false

    if ollama list 2>/dev/null \
        | awk 'NR > 1 {print $1}' \
        | grep -Fxq "$MODEL"; then
        MODEL_EXISTS=true
    fi

    if [[ "$MODEL_EXISTS" == true ]]; then

        success "Model is installed."

    elif [[ "$AUTO_PULL" == true ]]; then

        warning "Model is missing; pulling $MODEL..."
        ollama pull "$MODEL" || \
            die "Failed to pull model '$MODEL'."

        success "Model downloaded."

    else

        warning "Model '$MODEL' is not installed."

        if [[ -t 0 ]]; then

            printf 'Download it now? [Y/n] '

            read -r ANSWER || ANSWER="n"

            if [[ -z "$ANSWER" || "$ANSWER" =~ ^[Yy]$ ]]; then

                echo
                info "Downloading $MODEL..."

                ollama pull "$MODEL" || \
                    die "Failed to download model '$MODEL'."

                success "Model downloaded."

            else

                warning "Continuing without downloading the model."

            fi

        else

            warning "Non-interactive shell; model will not be downloaded."
            warning "Run: ollama pull '$MODEL'"

        fi
    fi

else

    warning "Model check skipped."

fi


# ============================================================
# ENVIRONMENT
# ============================================================

export GREMLIN_WORKSPACE="$WORKSPACE"
export GREMLIN_MODEL="$MODEL"

export PATH="$VENV_DIR/bin:$PATH"


# ============================================================
# START
# ============================================================

printf '\n'
printf '%s------------------------------------------------------------%s\n' "$CYAN" "$RESET"
printf '%s Gremlin 2.0 is ready.%s\n' "$GREEN" "$RESET"
printf '%s------------------------------------------------------------%s\n' "$CYAN" "$RESET"
printf '\n'

printf '%sWorkspace:%s %s\n' "$GRAY" "$RESET" "$WORKSPACE"
printf '%sPython:%s    %s\n' "$GRAY" "$RESET" "$VENV_PYTHON"
printf '%sModel:%s     %s\n' "$GRAY" "$RESET" "$MODEL"
printf '\n'


# ============================================================
# EXECUTE AGENT
# ============================================================
#
# exec replaces THIS launcher process with Python.
#
# It does not activate an interactive Bash shell.
# When Gremlin exits, control returns to the shell that launched
# launch.sh (Fish, Bash, Zsh, etc.).
#
exec "$VENV_PYTHON" "$AGENT"
#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ollama import chat


# ============================================================
# GREMLIN 2.0
# ============================================================

VERSION = "2.0.0"


# ============================================================
# CONFIGURATION
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

MODEL = os.environ.get(
    "GREMLIN_MODEL",
    "qwen3-coder:30b",
)

WORKSPACE = Path(
    os.environ.get(
        "GREMLIN_WORKSPACE",
        str(SCRIPT_DIR),
    )
).expanduser().resolve()

COMMAND_TIMEOUT = int(
    os.environ.get(
        "GREMLIN_COMMAND_TIMEOUT",
        "120",
    )
)

MAX_OUTPUT = int(
    os.environ.get(
        "GREMLIN_MAX_OUTPUT",
        "20000",
    )
)

MAX_FILE_SIZE = int(
    os.environ.get(
        "GREMLIN_MAX_FILE_SIZE",
        "2000000",
    )
)

MAX_PATCH_SIZE = int(
    os.environ.get(
        "GREMLIN_MAX_PATCH_SIZE",
        "250000",
    )
)

MAX_TOOL_CALLS_PER_TURN = int(
    os.environ.get(
        "GREMLIN_MAX_TOOL_CALLS",
        "80",
    )
)

MAX_CONVERSATION_MESSAGES = 80

MAX_SEARCH_RESULTS = 250

MAX_DIRECTORY_RESULTS = 1500

# This is deliberately NOT presented as a sandbox.
#
# Gremlin is a local development agent. If it can run Python,
# Node, Godot, compilers, etc., those programs may themselves
# have substantial operating-system access.
#
# The command runner therefore focuses on:
#
#   * no implicit shell interpretation
#   * predictable argument handling
#   * workspace working directory
#   * explicit destructive-command checks
#   * timeouts
#   * process-group cleanup
#
# It does NOT pretend that an executable allowlist makes an
# unprivileged local process into a sandbox.
ALLOW_DESTRUCTIVE_COMMANDS = os.environ.get(
    "GREMLIN_ALLOW_DESTRUCTIVE_COMMANDS",
    "0",
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}


# ============================================================
# COLORS
# ============================================================

USE_COLOR = sys.stdout.isatty()

if USE_COLOR:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    GRAY = "\033[90m"
    RESET = "\033[0m"
else:
    RED = ""
    GREEN = ""
    YELLOW = ""
    BLUE = ""
    CYAN = ""
    MAGENTA = ""
    GRAY = ""
    RESET = ""


def c(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"


# ============================================================
# CANCELLATION
# ============================================================

_cancel_event = threading.Event()


def handle_sigint(signum, frame):
    _cancel_event.set()

    print(
        "\n\n"
        + c(RED, "Interrupted.")
        + " Stopping the current operation..."
    )


# ============================================================
# DATA TYPES
# ============================================================

@dataclass
class ProjectProfile:
    name: str
    markers: list[str]
    verification: list[str] | None
    instructions: str


# ============================================================
# PROJECT PROFILES
# ============================================================

PROJECT_PROFILES = [
    ProjectProfile(
        name="Godot 4",
        markers=["project.godot"],
        verification=[
            "godot",
            "--path",
            ".",
            "--headless",
            "--editor",
            "--quit",
        ],
        instructions="""
This appears to be a Godot 4 project.

Use Godot 4 APIs.

For CharacterBody2D:
    velocity
    move_and_slide()

For CharacterBody3D:
    velocity
    move_and_slide()

Use _physics_process(delta) for physics.

Inspect project.godot and existing scenes before changing
project structure.

Do not invent resource UIDs or scene references.

After meaningful modifications, run Godot headless
verification and inspect both stdout and stderr.
""".strip(),
    ),

    ProjectProfile(
        name="Python",
        markers=[
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "requirements.txt",
            "pytest.ini",
        ],
        verification=["pytest"],
        instructions="""
This appears to be a Python project.

Inspect pyproject.toml, setup files, README files, and tests.

Prefer existing project tooling.

Do not invent dependencies unnecessarily.

Use the project's existing test/lint/build commands when
available.
""".strip(),
    ),

    ProjectProfile(
        name="Node.js",
        markers=["package.json"],
        verification=["npm", "test"],
        instructions="""
This appears to be a Node.js project.

Inspect package.json before modifying dependencies.

Prefer existing npm scripts.

Do not invent dependencies unnecessarily.
""".strip(),
    ),

    ProjectProfile(
        name="Rust",
        markers=["Cargo.toml"],
        verification=["cargo", "test"],
        instructions="""
This appears to be a Rust project.

Inspect Cargo.toml and source structure.

Prefer cargo check and cargo test for verification.
""".strip(),
    ),

    ProjectProfile(
        name="Go",
        markers=["go.mod"],
        verification=["go", "test", "./..."],
        instructions="""
This appears to be a Go project.

Inspect go.mod and package structure.

Prefer go test ./... for verification.
""".strip(),
    ),

    ProjectProfile(
        name="CMake/C++",
        markers=["CMakeLists.txt"],
        verification=None,
        instructions="""
This appears to use CMake.

Inspect the existing build structure before configuring it.

Do not blindly delete or recreate build directories.
""".strip(),
    ),

    ProjectProfile(
        name=".NET",
        markers=["*.csproj", "*.sln"],
        verification=["dotnet", "test"],
        instructions="""
This appears to be a .NET project.

Inspect the solution/project structure.

Prefer dotnet build and dotnet test when appropriate.
""".strip(),
    ),
]


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = r"""
You are Gremlin, an autonomous local software-development
agent.

You work directly inside a real user's workspace.

Your purpose is to inspect projects, understand them, modify
them when requested, run appropriate tools, verify the result,
and report accurately.

You have real filesystem and command execution capabilities.

You must behave like a careful senior developer, not like a
chatbot that merely suggests code.

============================================================
ABSOLUTE OPERATING PRINCIPLES
============================================================

1. ACTUALLY DO THE WORK.

If the user asks you to create, modify, fix, configure, test,
or investigate something, use the available tools.

Do not merely describe commands the user could run when you
can perform the work yourself.

2. INSPECT BEFORE MODIFYING.

For an unfamiliar workspace:

    list_files
    detect_project_type
    inspect important project files
    inspect existing implementation
    inspect tests/configuration

Do not blindly overwrite an existing project.

3. PRESERVE USER WORK.

Never casually replace unrelated files.

Prefer focused patches to existing files.

Use write_file for:
    - genuinely new files
    - deliberate complete replacements
    - generated files where replacement is appropriate

Use apply_patch for focused changes to existing files.

4. VERIFY.

After meaningful modifications:

    run appropriate verification
    inspect output
    diagnose errors
    fix problems
    verify again

A successful write is NOT proof that the implementation works.

A zero exit code is useful evidence but is not proof of
behavioral correctness.

5. NEVER CLAIM SUCCESS WITHOUT EVIDENCE.

If verification was not possible, say so.

If something failed, report the failure.

Do not fabricate test results.

============================================================
READ-ONLY INTENT
============================================================

If the user asks to:

    inspect
    analyze
    investigate
    review
    diagnose
    check
    explain
    report

and does NOT ask for a modification, operate in READ-ONLY
MODE.

Read-only mode permits:

    list_files
    read_file
    search_files
    detect_project_type
    get_project_profile
    run safe verification commands
    git_status
    git_diff

Read-only mode does NOT permit:

    write_file
    apply_patch
    make_directory

Do not "fix" a problem merely because you found it.

============================================================
MODIFICATION INTENT
============================================================

If the user asks you to:

    create
    build
    implement
    modify
    fix
    refactor
    update
    add
    remove

you may modify the workspace.

Do not repeatedly ask permission for ordinary implementation
details.

Use your judgment.

Ask a question only when a genuinely important ambiguity
cannot reasonably be resolved from the project context.

============================================================
NORMAL DEVELOPMENT LOOP
============================================================

Use this workflow:

    RECON
      ↓
    UNDERSTAND
      ↓
    PLAN
      ↓
    IMPLEMENT
      ↓
    VERIFY
      ↓
    DIAGNOSE
      ↓
    FIX
      ↓
    VERIFY AGAIN
      ↓
    REPORT

Do not get stuck narrating every step.

Tool calls should perform most of the work.

============================================================
PROJECT DETECTION
============================================================

Do not assume the project type.

Inspect the workspace.

Possible projects include:

    Godot
    Python
    Node.js
    Rust
    Go
    C/C++
    .NET
    shell
    documentation
    mixed projects
    custom projects

Multiple project types may coexist.

Use the project's own configuration as the source of truth.

============================================================
GODOT
============================================================

When working with Godot:

1. Inspect project.godot.
2. Inspect existing scenes/scripts.
3. Respect the existing project structure.
4. Use Godot 4 APIs when project.godot indicates Godot 4.
5. Do not invent resource paths or UIDs.
6. Verify scenes and scripts using Godot headless mode.
7. Read stderr carefully.
8. If Godot reports an error, fix it rather than merely
   reporting that the files were written.

For a brand-new empty directory, creating a minimal project
is appropriate when the user explicitly asked for a project.

============================================================
FILES
============================================================

Paths passed to file tools are workspace-relative.

Examples:

    project.godot
    scripts/player.gd
    scenes/main.tscn
    res://scenes/main.tscn

Do not invent paths.

Read existing files before replacing them.

For large files, inspect only the relevant portions when
possible.

============================================================
PATCHING
============================================================

apply_patch is exact.

The old text must exist.

The old text must normally occur exactly once.

If a patch fails:

    read the file again
    understand the current content
    produce a more specific patch

Do not repeatedly submit the same failed patch.

============================================================
COMMAND EXECUTION
============================================================

run_command executes a command as an argument vector.

It does NOT run through a shell.

Therefore:

    pipes
    redirects
    command substitution
    shell variables
    &&

are not implicitly interpreted.

If shell syntax is genuinely required, use the dedicated
shell tool only when it is available and appropriate.

Prefer project-native commands.

Examples:

    godot --path . --headless --editor --quit
    pytest
    cargo test
    go test ./...
    npm test
    git status --short --branch

Do not use commands to circumvent filesystem safety.

Do not use destructive commands merely for convenience.

============================================================
DESTRUCTIVE OPERATIONS
============================================================

Never destroy user data as part of ordinary work.

Do not casually use:

    rm -rf
    mkfs
    dd
    shred
    git reset --hard
    git clean
    git restore
    destructive filesystem wipes

If a requested task genuinely requires destructive behavior,
explain the consequence and use the least destructive method
possible.

Dedicated file tools should be preferred over shell deletion.

============================================================
GIT
============================================================

Git is an inspection and safety mechanism.

Useful commands:

    git status --short --branch
    git diff
    git diff -- file
    git log
    git branch --show-current

Do not automatically commit.

Do not reset or discard unrelated user changes.

Before declaring a substantial modification complete, inspect
git status or git diff when the project is a Git repository.

============================================================
ERROR HANDLING
============================================================

Tool failures are feedback.

If a tool says:

    file does not exist

do not blindly retry the exact same call.

If a command fails:

    inspect stdout
    inspect stderr
    diagnose
    modify if appropriate
    retry

If the model's tool call format is invalid, do NOT emit
hand-written XML tool syntax.

Use the native structured tool interface.

============================================================
COMMUNICATION
============================================================

Be concise while working.

Do not narrate every trivial internal thought.

At completion report:

    What changed
    Important files changed
    Verification performed
    Remaining problems

If something remains broken, say so plainly.

============================================================
AUTONOMY
============================================================

When the user has clearly authorized implementation, work
autonomously.

You are allowed to make ordinary engineering decisions.

You are not required to ask permission for every file creation,
patch, test, or bug fix.

Use the user's request as authorization for the requested scope.

============================================================
TOOL CALLING
============================================================

Use the provided structured tools.

DO NOT output:

    <tool_call>
    <function=...>
    <parameter=...>

Do not invent a textual tool protocol.

The application handles structured tool calls directly.
"""


# ============================================================
# PATH SAFETY
# ============================================================

def workspace_root() -> Path:
    return WORKSPACE.resolve()


def safe_path(
    relative_path: str,
    *,
    must_exist: bool = False,
) -> Path:

    if not isinstance(relative_path, str):
        raise ValueError("Path must be a string.")

    value = relative_path.strip()

    if not value:
        raise ValueError("Path cannot be empty.")

    if value.startswith("res://"):
        value = value[6:]

    value = value.replace("\\", "/")

    candidate = Path(value)

    if candidate.is_absolute():
        raise ValueError(
            "Absolute filesystem paths are not allowed. "
            "Use a workspace-relative path."
        )

    # Do not allow explicit parent traversal.
    parts = Path(value).parts

    if ".." in parts:
        raise ValueError(
            "Parent-directory traversal is not allowed."
        )

    root = workspace_root()

    target = (root / value).resolve()

    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(
            "Path resolves outside the workspace."
        )

    if must_exist and not target.exists():
        raise FileNotFoundError(
            f"Path does not exist: {relative_path}"
        )

    return target


def relative_path(path: Path) -> str:
    return str(
        path.resolve().relative_to(
            workspace_root()
        )
    )


# ============================================================
# FILESYSTEM HELPERS
# ============================================================

IGNORED_DIRECTORIES = {
    ".git",
    ".godot",
    ".import",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "target",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
}

IGNORED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".ico",
    ".ogg",
    ".mp3",
    ".wav",
    ".flac",
    ".aac",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".pdf",
    ".zip",
    ".7z",
    ".tar",
    ".gz",
    ".xz",
    ".bz2",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
}


def should_ignore(path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(
            workspace_root()
        )
    except ValueError:
        return True

    return any(
        part in IGNORED_DIRECTORIES
        for part in rel.parts
    )


def is_probably_binary(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(4096)
    except OSError:
        return True

    if b"\x00" in chunk:
        return True

    return False


# ============================================================
# FILE TOOLS
# ============================================================

def list_files(path: str = ".") -> str:

    try:
        directory = safe_path(path, must_exist=True)
    except Exception as exc:
        return f"ERROR: {exc}"

    if not directory.is_dir():
        return f"ERROR: not a directory: {path}"

    results: list[str] = []

    try:

        for item in sorted(
            directory.rglob("*"),
            key=lambda p: str(p).lower(),
        ):

            if should_ignore(item):
                continue

            try:
                rel = item.resolve().relative_to(
                    workspace_root()
                )
            except ValueError:
                continue

            if len(results) >= MAX_DIRECTORY_RESULTS:
                break

            if item.is_dir():
                results.append(
                    f"[DIR]  {rel}"
                )
            elif item.is_file():
                size = item.stat().st_size
                results.append(
                    f"[FILE] {rel} ({size} bytes)"
                )

    except Exception as exc:
        return f"ERROR listing workspace: {exc}"

    if not results:
        return "(directory is empty)"

    output = "\n".join(results)

    if len(results) >= MAX_DIRECTORY_RESULTS:
        output += (
            "\n\n[DIRECTORY RESULT LIMIT REACHED]"
        )

    return output


def read_file(path: str) -> str:

    try:
        file_path = safe_path(
            path,
            must_exist=True,
        )
    except Exception as exc:
        return f"ERROR: {exc}"

    if not file_path.is_file():
        return f"ERROR: not a file: {path}"

    try:
        size = file_path.stat().st_size
    except OSError as exc:
        return f"ERROR inspecting {path}: {exc}"

    if size > MAX_FILE_SIZE:
        return (
            f"ERROR: file is too large to read "
            f"({size} bytes > {MAX_FILE_SIZE})."
        )

    if is_probably_binary(file_path):
        return (
            "ERROR: file appears to be binary. "
            "Use an appropriate binary-aware tool."
        )

    try:
        return file_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        return f"ERROR reading {path}: {exc}"


def write_file(
    path: str,
    content: str,
) -> str:

    if not isinstance(content, str):
        return "ERROR: content must be a string."

    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        return (
            "ERROR: file exceeds maximum size of "
            f"{MAX_FILE_SIZE} bytes."
        )

    try:
        file_path = safe_path(path)
    except Exception as exc:
        return f"ERROR: {exc}"

    if file_path.exists() and file_path.is_dir():
        return (
            f"ERROR: cannot replace directory with a file: "
            f"{path}"
        )

    try:

        file_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        fd, temp_name = tempfile.mkstemp(
            prefix=".gremlin-",
            dir=str(file_path.parent),
        )

        try:

            with os.fdopen(
                fd,
                "w",
                encoding="utf-8",
                newline="",
            ) as temporary:

                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())

            os.replace(
                temp_name,
                file_path,
            )

        except Exception:

            try:
                os.unlink(temp_name)
            except OSError:
                pass

            raise

    except Exception as exc:
        return f"ERROR writing {path}: {exc}"

    return (
        f"✓ wrote {path} "
        f"({len(content.encode('utf-8'))} bytes)"
    )


def make_directory(path: str) -> str:

    try:
        directory = safe_path(path)
    except Exception as exc:
        return f"ERROR: {exc}"

    try:
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )
    except Exception as exc:
        return f"ERROR creating directory: {exc}"

    return f"✓ directory ready: {path}"


def apply_patch(
    path: str,
    old_text: str,
    new_text: str,
) -> str:

    if not isinstance(old_text, str):
        return "ERROR: old_text must be a string."

    if not isinstance(new_text, str):
        return "ERROR: new_text must be a string."

    if not old_text:
        return "ERROR: old_text cannot be empty."

    if (
        len(old_text) > MAX_PATCH_SIZE
        or len(new_text) > MAX_PATCH_SIZE
    ):
        return (
            "ERROR: patch is too large. "
            f"Maximum patch text is {MAX_PATCH_SIZE} characters."
        )

    try:
        file_path = safe_path(
            path,
            must_exist=True,
        )
    except Exception as exc:
        return f"ERROR: {exc}"

    if not file_path.is_file():
        return f"ERROR: not a file: {path}"

    try:
        content = file_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        return f"ERROR reading {path}: {exc}"

    occurrences = content.count(old_text)

    if occurrences == 0:
        return (
            "ERROR: exact old_text was not found.\n"
            "Read the current file before trying another patch."
        )

    if occurrences > 1:
        return (
            f"ERROR: old_text occurs {occurrences} times.\n"
            "Make the patch more specific."
        )

    replacement = content.replace(
        old_text,
        new_text,
        1,
    )

    return write_file(
        path,
        replacement,
    )


def search_files(query: str) -> str:

    if not isinstance(query, str):
        return "ERROR: query must be a string."

    query = query.strip()

    if not query:
        return "ERROR: query cannot be empty."

    results: list[str] = []

    lowered = query.lower()

    try:

        for file_path in workspace_root().rglob("*"):

            if _cancel_event.is_set():
                return "Search cancelled."

            if not file_path.is_file():
                continue

            if should_ignore(file_path):
                continue

            if file_path.suffix.lower() in IGNORED_EXTENSIONS:
                continue

            try:
                if file_path.stat().st_size > MAX_FILE_SIZE:
                    continue

                if is_probably_binary(file_path):
                    continue

                text = file_path.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )

            except Exception:
                continue

            try:
                rel = file_path.resolve().relative_to(
                    workspace_root()
                )
            except ValueError:
                continue

            for number, line in enumerate(
                text.splitlines(),
                start=1,
            ):

                if lowered in line.lower():

                    results.append(
                        f"{rel}:{number}: {line.strip()}"
                    )

                    if len(results) >= MAX_SEARCH_RESULTS:
                        return (
                            "\n".join(results)
                            + "\n[SEARCH RESULT LIMIT REACHED]"
                        )

    except Exception as exc:
        return f"ERROR searching workspace: {exc}"

    if not results:
        return f"No matches found for: {query}"

    return "\n".join(results)


# ============================================================
# PROJECT DETECTION
# ============================================================

def marker_matches(
    marker: str,
) -> list[Path]:

    results: list[Path] = []

    if "*" not in marker:
        candidate = workspace_root() / marker

        if candidate.exists():
            results.append(candidate)

        return results

    for candidate in workspace_root().glob(marker):

        if candidate.exists():
            results.append(candidate)

    return results


def detect_project_type() -> str:

    detected: list[str] = []

    for profile in PROJECT_PROFILES:

        markers: list[str] = []

        for marker in profile.markers:

            if marker_matches(marker):
                markers.append(marker)

        if markers:
            detected.append(
                f"- {profile.name}: "
                f"{', '.join(markers)}"
            )

    if not detected:
        return (
            "No recognized project profile was detected.\n"
            "Treat the workspace as a generic/custom project."
        )

    return (
        "Detected project profiles:\n"
        + "\n".join(detected)
    )


def get_project_profile() -> str:

    sections: list[str] = []

    for profile in PROJECT_PROFILES:

        found = [
            marker
            for marker in profile.markers
            if marker_matches(marker)
        ]

        if not found:
            continue

        verification = (
            shlex.join(profile.verification)
            if profile.verification
            else "No automatic verification command."
        )

        sections.append(
            f"PROFILE: {profile.name}\n"
            f"MARKERS: {', '.join(found)}\n"
            f"VERIFICATION: {verification}\n"
            f"INSTRUCTIONS:\n{profile.instructions}"
        )

    if not sections:
        return (
            "No specialized project profile detected.\n"
            "Use generic inspection and project-native tooling."
        )

    return "\n\n".join(sections)


# ============================================================
# COMMAND SAFETY
# ============================================================

# These are not the security boundary.
#
# They exist to catch obvious destructive mistakes made through
# ordinary development commands.
OBVIOUS_DESTRUCTIVE_PROGRAMS = {
    "mkfs",
    "fdisk",
    "parted",
    "wipefs",
    "shred",
}

OBVIOUS_DESTRUCTIVE_GIT = {
    ("git", "reset", "--hard"),
    ("git", "clean"),
    ("git", "restore"),
    ("git", "checkout"),
}

OBVIOUS_DESTRUCTIVE_FILE_COMMANDS = {
    "rm",
    "rmdir",
    "unlink",
}


def command_is_obviously_destructive(
    argv: list[str],
) -> bool:

    if not argv:
        return False

    program = Path(argv[0]).name.lower()

    if program in OBVIOUS_DESTRUCTIVE_PROGRAMS:
        return True

    if program in OBVIOUS_DESTRUCTIVE_FILE_COMMANDS:
        return True

    normalized = tuple(
        item.lower()
        for item in argv
    )

    for pattern in OBVIOUS_DESTRUCTIVE_GIT:

        if normalized[:len(pattern)] == pattern:
            return True

    return False


def parse_command(
    command: str,
) -> tuple[list[str] | None, str]:

    if not isinstance(command, str):
        return None, "Command must be a string."

    command = command.strip()

    if not command:
        return None, "Command cannot be empty."

    try:
        argv = shlex.split(
            command,
            posix=True,
        )
    except ValueError as exc:
        return None, f"Could not parse command: {exc}"

    if not argv:
        return None, "Command produced no arguments."

    return argv, ""


def run_command(
    command: str,
    timeout: int | None = None,
) -> str:

    argv, error = parse_command(command)

    if argv is None:
        return f"ERROR: {error}"

    if command_is_obviously_destructive(argv):

        if not ALLOW_DESTRUCTIVE_COMMANDS:
            return (
                "ERROR: command was blocked because it is "
                "obviously destructive.\n\n"
                f"COMMAND: {shlex.join(argv)}\n\n"
                "Gremlin's normal workflow should not require "
                "destructive filesystem or Git operations."
            )

    if _cancel_event.is_set():
        return "ERROR: command cancelled before execution."

    timeout_value = (
        timeout
        if timeout is not None
        else COMMAND_TIMEOUT
    )

    if timeout_value <= 0:
        timeout_value = COMMAND_TIMEOUT

    process: subprocess.Popen[str] | None = None

    try:

        process = subprocess.Popen(
            argv,
            cwd=str(workspace_root()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
            shell=False,
        )

        try:

            stdout, stderr = process.communicate(
                timeout=timeout_value,
            )

        except subprocess.TimeoutExpired:

            terminate_process(process)

            return (
                f"ERROR: command timed out after "
                f"{timeout_value} seconds.\n\n"
                f"COMMAND: {shlex.join(argv)}"
            )

        if _cancel_event.is_set():

            terminate_process(process)

            return (
                "ERROR: command interrupted by user."
            )

        stdout = stdout or ""
        stderr = stderr or ""

        combined_length = (
            len(stdout)
            + len(stderr)
        )

        if combined_length > MAX_OUTPUT:

            remaining = MAX_OUTPUT

            stdout = stdout[:remaining]

            remaining -= len(stdout)

            stderr = stderr[:max(0, remaining)]

            truncated = True

        else:

            truncated = False

        result = (
            f"COMMAND: {shlex.join(argv)}\n"
            f"EXIT CODE: {process.returncode}\n\n"
            f"STDOUT:\n{stdout}\n\n"
            f"STDERR:\n{stderr}"
        )

        if truncated:
            result += "\n\n[OUTPUT TRUNCATED]"

        if process.returncode == 0:
            return "✓ command succeeded\n" + result

        return "✗ command failed\n" + result

    except FileNotFoundError:

        program = argv[0]

        return (
            f"ERROR: executable was not found: {program}\n\n"
            f"COMMAND: {shlex.join(argv)}"
        )

    except PermissionError as exc:

        return (
            f"ERROR: permission denied: {exc}\n\n"
            f"COMMAND: {shlex.join(argv)}"
        )

    except KeyboardInterrupt:

        if process is not None:
            terminate_process(process)

        return "ERROR: command interrupted."

    except Exception as exc:

        if process is not None:
            terminate_process(process)

        return (
            f"ERROR running command: {exc}\n\n"
            f"COMMAND: {shlex.join(argv)}"
        )


def terminate_process(
    process: subprocess.Popen[str],
) -> None:

    try:

        if process.poll() is not None:
            return

    except Exception:
        pass

    try:

        os.killpg(
            process.pid,
            signal.SIGTERM,
        )

    except Exception:

        try:
            process.terminate()
        except Exception:
            pass

    try:

        process.wait(
            timeout=3,
        )

        return

    except Exception:
        pass

    try:

        os.killpg(
            process.pid,
            signal.SIGKILL,
        )

    except Exception:

        try:
            process.kill()
        except Exception:
            pass


# ============================================================
# GIT
# ============================================================

def git_status() -> str:
    return run_command(
        "git status --short --branch"
    )


def git_diff() -> str:
    return run_command(
        "git diff"
    )


def git_log() -> str:
    return run_command(
        "git log -10 --oneline --decorate"
    )


# ============================================================
# TESTING
# ============================================================

def run_tests() -> str:

    detected: list[ProjectProfile] = []

    for profile in PROJECT_PROFILES:

        if any(
            marker_matches(marker)
            for marker in profile.markers
        ):
            detected.append(profile)

    if not detected:
        return (
            "No automatic verification profile was detected.\n"
            "Inspect the project and use its native tooling."
        )

    results: list[str] = []

    for profile in detected:

        if not profile.verification:

            results.append(
                f"{profile.name}: "
                "no automatic test command configured."
            )

            continue

        command = shlex.join(
            profile.verification
        )

        results.append(
            f"=== {profile.name} ===\n"
            f"{run_command(command)}"
        )

    return "\n\n".join(results)


# ============================================================
# TOOL DEFINITIONS
# ============================================================

def tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


TOOLS = [
    tool(
        "list_files",
        (
            "List files and directories in the workspace. "
            "Use this early when starting a task."
        ),
        {
            "path": {
                "type": "string",
                "description": (
                    "Workspace-relative directory. "
                    "Use '.' for the workspace root."
                ),
            },
        },
    ),

    tool(
        "read_file",
        (
            "Read a UTF-8 text file from the workspace. "
            "Always use this before making a focused patch "
            "to an existing file."
        ),
        {
            "path": {
                "type": "string",
                "description": (
                    "Workspace-relative file path."
                ),
            },
        },
        ["path"],
    ),

    tool(
        "write_file",
        (
            "Create a new text file or deliberately replace "
            "an entire existing text file. Parent directories "
            "are created automatically."
        ),
        {
            "path": {
                "type": "string",
                "description": (
                    "Workspace-relative file path."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "Complete UTF-8 text content."
                ),
            },
        },
        ["path", "content"],
    ),

    tool(
        "apply_patch",
        (
            "Replace one exact section of an existing file. "
            "old_text must occur exactly once. Prefer this "
            "for focused modifications."
        ),
        {
            "path": {
                "type": "string",
                "description": (
                    "Workspace-relative file path."
                ),
            },
            "old_text": {
                "type": "string",
                "description": (
                    "Exact existing text."
                ),
            },
            "new_text": {
                "type": "string",
                "description": (
                    "Replacement text."
                ),
            },
        },
        ["path", "old_text", "new_text"],
    ),

    tool(
        "make_directory",
        (
            "Create a workspace-relative directory, including "
            "missing parent directories."
        ),
        {
            "path": {
                "type": "string",
                "description": (
                    "Workspace-relative directory path."
                ),
            },
        },
        ["path"],
    ),

    tool(
        "search_files",
        (
            "Search text files in the workspace for a literal "
            "case-insensitive string."
        ),
        {
            "query": {
                "type": "string",
                "description": (
                    "Text to search for."
                ),
            },
        },
        ["query"],
    ),

    tool(
        "detect_project_type",
        (
            "Detect likely project types using project marker "
            "files."
        ),
        {},
    ),

    tool(
        "get_project_profile",
        (
            "Return detailed instructions and verification "
            "commands for detected project types."
        ),
        {},
    ),

    tool(
        "run_command",
        (
            "Run a development command inside the workspace. "
            "Arguments are parsed without a shell. Use this "
            "for compilers, interpreters, test runners, Git, "
            "Godot, package managers, and project tooling."
        ),
        {
            "command": {
                "type": "string",
                "description": (
                    "Command written as a normal command line. "
                    "Shell operators are not interpreted."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Optional timeout in seconds."
                ),
            },
        },
        ["command"],
    ),

    tool(
        "run_tests",
        (
            "Run appropriate automatically detected project "
            "verification commands."
        ),
        {},
    ),

    tool(
        "git_status",
        (
            "Show current Git branch and working-tree status."
        ),
        {},
    ),

    tool(
        "git_diff",
        (
            "Show the current unstaged Git diff."
        ),
        {},
    ),

    tool(
        "git_log",
        (
            "Show recent Git history."
        ),
        {},
    ),
]


TOOL_NAMES = {
    item["function"]["name"]
    for item in TOOLS
}


# ============================================================
# TOOL EXECUTION
# ============================================================

def execute_tool(
    name: str,
    arguments: dict[str, Any],
) -> str:

    if _cancel_event.is_set():
        return "ERROR: operation cancelled."

    if name not in TOOL_NAMES:
        return f"ERROR: unknown tool: {name}"

    if not isinstance(arguments, dict):
        return "ERROR: tool arguments must be an object."

    try:

        if name == "list_files":
            return list_files(
                arguments.get("path", ".")
            )

        if name == "read_file":
            return read_file(
                arguments["path"]
            )

        if name == "write_file":
            return write_file(
                arguments["path"],
                arguments["content"],
            )

        if name == "apply_patch":
            return apply_patch(
                arguments["path"],
                arguments["old_text"],
                arguments["new_text"],
            )

        if name == "make_directory":
            return make_directory(
                arguments["path"]
            )

        if name == "search_files":
            return search_files(
                arguments["query"]
            )

        if name == "detect_project_type":
            return detect_project_type()

        if name == "get_project_profile":
            return get_project_profile()

        if name == "run_command":

            timeout = arguments.get(
                "timeout"
            )

            if timeout is not None:

                try:
                    timeout = int(timeout)
                except (TypeError, ValueError):
                    return (
                        "ERROR: timeout must be an integer."
                    )

            return run_command(
                arguments["command"],
                timeout,
            )

        if name == "run_tests":
            return run_tests()

        if name == "git_status":
            return git_status()

        if name == "git_diff":
            return git_diff()

        if name == "git_log":
            return git_log()

        return f"ERROR: unknown tool: {name}"

    except KeyError as exc:

        return (
            f"ERROR executing {name}: "
            f"missing required argument {exc}"
        )

    except Exception as exc:

        return (
            f"ERROR executing {name}: {exc}"
        )


# ============================================================
# OLLAMA MESSAGE HELPERS
# ============================================================

def normalize_arguments(
    arguments: Any,
) -> dict[str, Any]:

    if isinstance(arguments, dict):
        return arguments

    if isinstance(arguments, str):

        try:
            parsed = json.loads(arguments)

            if isinstance(parsed, dict):
                return parsed

        except json.JSONDecodeError:
            pass

    return {}


def get_message_content(
    message: Any,
) -> str:

    content = getattr(
        message,
        "content",
        "",
    )

    if content is None:
        return ""

    return str(content)


def get_tool_calls(
    message: Any,
) -> list[Any]:

    calls = getattr(
        message,
        "tool_calls",
        None,
    )

    if calls is None:
        return []

    return list(calls)


def append_tool_result(
    messages: list[Any],
    result: str,
) -> None:

    messages.append(
        {
            "role": "tool",
            "content": result,
        }
    )


# ============================================================
# CONTEXT MANAGEMENT
# ============================================================

def compact_context(
    messages: list[Any],
) -> list[Any]:

    if len(messages) <= MAX_CONVERSATION_MESSAGES:
        return messages

    system = messages[0]

    recent = messages[
        -(MAX_CONVERSATION_MESSAGES - 1):
    ]

    summary_message = {
        "role": "user",
        "content": (
            "Conversation context was compacted. "
            "Continue using the current workspace state as "
            "the source of truth. Re-inspect files when needed."
        ),
    }

    return [
        system,
        summary_message,
        *recent,
    ]


# ============================================================
# MODEL ERROR RECOVERY
# ============================================================

def looks_like_legacy_xml_tool_call(
    content: str,
) -> bool:

    lowered = content.lower()

    return (
        "<function=" in lowered
        or "<tool_call>" in lowered
        or "<parameter=" in lowered
    )


# ============================================================
# AGENT TURN
# ============================================================

def run_agent_turn(
    messages: list[Any],
) -> bool:

    _cancel_event.clear()

    tool_call_count = 0
    malformed_tool_retries = 0

    while True:

        if _cancel_event.is_set():
            return False

        messages[:] = compact_context(
            messages
        )

        try:

            response = chat(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
            )

        except KeyboardInterrupt:

            _cancel_event.set()
            return False

        except Exception as exc:

            if _cancel_event.is_set():
                return False

            print(
                "\n"
                + c(RED, "OLLAMA ERROR:")
            )
            print(str(exc))

            # Give the model one chance to recover from a
            # transient/tool-format problem without recursively
            # hammering Ollama forever.
            malformed_tool_retries += 1

            if malformed_tool_retries > 3:

                print(
                    c(
                        RED,
                        "Ollama failed repeatedly. "
                        "Stopping this turn.",
                    )
                )

                return False

            messages.append(
                {
                    "role": "user",
                    "content": (
                        "The previous model response could not "
                        "be processed. Continue using native "
                        "structured tool calls only. Do not "
                        "emit XML or textual tool-call syntax."
                    ),
                }
            )

            time.sleep(
                min(
                    malformed_tool_retries,
                    3,
                )
            )

            continue

        if _cancel_event.is_set():
            return False

        # Native Ollama response.
        messages.append(
            response.message
        )

        native_calls = get_tool_calls(
            response.message
        )

        if native_calls:

            malformed_tool_retries = 0

            for call in native_calls:

                if _cancel_event.is_set():
                    return False

                tool_call_count += 1

                if (
                    tool_call_count
                    > MAX_TOOL_CALLS_PER_TURN
                ):

                    print(
                        "\n"
                        + c(
                            RED,
                            "Tool-call limit reached for "
                            "this turn.",
                        )
                    )

                    return False

                function = getattr(
                    call,
                    "function",
                    None,
                )

                if function is None:

                    append_tool_result(
                        messages,
                        "ERROR: malformed tool call.",
                    )

                    continue

                name = getattr(
                    function,
                    "name",
                    "",
                )

                arguments = normalize_arguments(
                    getattr(
                        function,
                        "arguments",
                        {},
                    )
                )

                print(
                    "\n"
                    + c(
                        YELLOW,
                        f"→ {name}",
                    )
                )

                result = execute_tool(
                    name,
                    arguments,
                )

                print(
                    c(
                        GRAY,
                        result,
                    )
                )

                append_tool_result(
                    messages,
                    result,
                )

            continue

        content = get_message_content(
            response.message
        )

        if looks_like_legacy_xml_tool_call(
            content
        ):

            malformed_tool_retries += 1

            if malformed_tool_retries <= 2:

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response attempted "
                            "to use legacy XML tool syntax. "
                            "Do not output XML tool calls. "
                            "Use the native structured tools "
                            "provided to you. Retry the operation."
                        ),
                    }
                )

                continue

            print(
                "\n"
                + c(
                    RED,
                    "The model repeatedly emitted unsupported "
                    "legacy XML tool syntax.",
                )
            )

            return False

        if content.strip():

            print(
                "\n"
                + c(
                    GREEN,
                    "Gremlin >",
                )
            )

            print(content)

        return True


# ============================================================
# UI
# ============================================================

def print_banner() -> None:

    print()
    print(
        c(
            CYAN,
            "=" * 70,
        )
    )

    print(
        c(
            CYAN,
            " GREMLIN 2.0 — LOCAL DEVELOPMENT AGENT",
        )
    )

    print(
        c(
            CYAN,
            "=" * 70,
        )
    )

    print()

    print(
        f"{GRAY}Version:{RESET}    {VERSION}"
    )

    print(
        f"{GRAY}Model:{RESET}      {MODEL}"
    )

    print(
        f"{GRAY}Workspace:{RESET}  {WORKSPACE}"
    )

    print(
        f"{GRAY}Agent:{RESET}      {SCRIPT_DIR}"
    )

    print(
        f"{GRAY}Python:{RESET}     {sys.version.split()[0]}"
    )

    print()

    print(
        detect_project_type()
    )

    print()


def print_help() -> None:

    print()

    print(
        c(
            CYAN,
            "Gremlin commands:",
        )
    )

    print()

    print("  /help       Show this help")
    print("  /status     Show Gremlin status")
    print("  /workspace  Show workspace")
    print("  /model      Show model")
    print("  /tools      Show available tools")
    print("  /clear      Clear conversation context")
    print("  /profile    Detect project profile")
    print("  exit        Exit")
    print("  quit        Exit")

    print()

    print(
        "Everything else is sent to the development agent."
    )

    print()


def print_status() -> None:

    print()

    print(
        f"{GRAY}Gremlin:{RESET}    {VERSION}"
    )

    print(
        f"{GRAY}Model:{RESET}      {MODEL}"
    )

    print(
        f"{GRAY}Workspace:{RESET}  {WORKSPACE}"
    )

    print(
        f"{GRAY}Timeout:{RESET}    {COMMAND_TIMEOUT}s"
    )

    print(
        f"{GRAY}Tools:{RESET}      {len(TOOLS)}"
    )

    print(
        f"{GRAY}Destructive:{RESET} "
        f"{'enabled' if ALLOW_DESTRUCTIVE_COMMANDS else 'blocked'}"
    )

    print()


def print_tools() -> None:

    print()

    for item in TOOLS:

        function = item["function"]

        print(
            f"{CYAN}{function['name']}{RESET}"
        )

        print(
            f"  {function['description']}"
        )

    print()


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    signal.signal(
        signal.SIGINT,
        handle_sigint,
    )

    if not WORKSPACE.exists():

        print(
            c(
                RED,
                "ERROR: workspace does not exist.",
            )
        )

        return 1

    if not WORKSPACE.is_dir():

        print(
            c(
                RED,
                "ERROR: workspace is not a directory.",
            )
        )

        return 1

    print_banner()

    print(
        c(
            GRAY,
            "Ctrl+C stops the current operation."
        )
    )

    print(
        c(
            GRAY,
            "Type /help for commands."
        )
    )

    print()

    messages: list[Any] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ]

    while True:

        _cancel_event.clear()

        try:

            user = input(
                f"\n{CYAN}You > {RESET}"
            )

        except EOFError:

            print()
            break

        except KeyboardInterrupt:

            _cancel_event.clear()

            print(
                "\n"
                + c(
                    YELLOW,
                    "Nothing was running. Back to prompt.",
                )
            )

            continue

        user = user.strip()

        if not user:
            continue

        command = user.lower()

        if command in {
            "exit",
            "quit",
        }:

            print(
                "\n"
                + c(
                    YELLOW,
                    "Goodbye, gremlin. 👋",
                )
            )

            break

        if command == "/help":
            print_help()
            continue

        if command == "/status":
            print_status()
            continue

        if command == "/workspace":
            print()
            print(WORKSPACE)
            continue

        if command == "/model":
            print()
            print(MODEL)
            continue

        if command == "/tools":
            print_tools()
            continue

        if command == "/profile":

            print()

            print(
                get_project_profile()
            )

            continue

        if command == "/clear":

            messages = [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                }
            ]

            print(
                "\n"
                + c(
                    YELLOW,
                    "Conversation context cleared.",
                )
            )

            continue

        messages.append(
            {
                "role": "user",
                "content": user,
            }
        )

        completed = run_agent_turn(
            messages
        )

        if not completed:

            print(
                "\n"
                + c(
                    YELLOW,
                    "Current operation stopped. "
                    "Conversation context preserved.",
                )
            )

        _cancel_event.clear()

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    raise SystemExit(
        main()
    )
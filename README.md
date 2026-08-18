# Gremlin

**Gremlin** is a portable local development agent powered by [Ollama](https://ollama.com/).

It runs directly on your machine, works inside a configured workspace, and can inspect projects, edit files, run development tools, detect project types, and verify changes.

Gremlin is designed to be a practical local coding companion rather than a cloud-hosted development environment.

## Features

* 🤖 Local LLM inference through Ollama
* 📁 Workspace-aware filesystem tools
* 🔎 Project type detection
* ✏️ File creation and focused patching
* 🔍 Workspace text search
* 🧪 Project-aware verification and testing
* 🐙 Git status, diff, and log inspection
* ⏱️ Command timeouts
* 🛑 Process cleanup on interruption
* 🚫 Basic protection against obviously destructive commands
* 🧠 Conversation context management
* 🧩 Configurable model and workspace
* 🐍 Automatic Python virtual environment setup

---
<img width="919" height="820" alt="Screenshot_20260818_122521" src="https://github.com/user-attachments/assets/1e3344e7-3be9-4d09-bd55-5890d7f7f5f8" />

---

## Requirements

* Python 3.10+
* [Ollama](https://ollama.com/)
* An Ollama-compatible coding model
* A capable gpu and system (Recommended 12GB VRAM)

The default model is:

```text
qwen3-coder:30b
```

You can change it with `--model` or the `GREMLIN_MODEL` environment variable.

## Quick Start

Clone the repository and enter the project:

```bash
git clone https://github.com/the033003/Gremlin.git
cd gremlin
```

Make the launcher executable:

```bash
chmod +x launch.sh
```

Start Gremlin:

```bash
./launch.sh
```

On first launch, Gremlin will:

1. Check the Python version.
2. Create a local `.venv` if necessary.
3. Install the Python Ollama package if necessary.
4. Check for Ollama.
5. Start the Ollama server when possible.
6. Check whether the configured model is installed.
7. Launch the Gremlin agent.

If the model is not installed, Gremlin can offer to download it.

You can also download the model yourself:

```bash
ollama pull qwen3-coder:30b
```

## Usage

Start Gremlin normally:

```bash
./launch.sh
```

Use another model:

```bash
./launch.sh --model <model>
```

Use a different workspace:

```bash
./launch.sh --workspace ~/Projects/my-project
```

Skip the model check:

```bash
./launch.sh --no-model-check
```

Automatically pull a missing model:

```bash
./launch.sh --pull-model
```

Enable Bash debugging:

```bash
./launch.sh --debug
```

### Environment Variables

| Variable                             | Default            | Description                                        |
| ------------------------------------ | ------------------ | -------------------------------------------------- |
| `GREMLIN_WORKSPACE`                  | Launcher directory | Default development workspace                      |
| `GREMLIN_MODEL`                      | `qwen3-coder:30b`  | Ollama model                                       |
| `GREMLIN_COMMAND_TIMEOUT`            | `120`              | Command timeout in seconds                         |
| `GREMLIN_MAX_OUTPUT`                 | `20000`            | Maximum command output characters                  |
| `GREMLIN_MAX_FILE_SIZE`              | `2000000`          | Maximum file size for file operations              |
| `GREMLIN_MAX_PATCH_SIZE`             | `250000`           | Maximum patch size                                 |
| `GREMLIN_MAX_TOOL_CALLS`             | `80`               | Maximum tool calls per turn                        |
| `GREMLIN_ALLOW_DESTRUCTIVE_COMMANDS` | `0`                | Allow commands classified as obviously destructive |

Example:

```bash
GREMLIN_MODEL=qwen3-coder:30b ./launch.sh
```

## Workspace

By default, Gremlin operates in the directory containing the launcher.

You can override this with:

```bash
./launch.sh --workspace ~/Projects/my-game
```

or:

```bash
export GREMLIN_WORKSPACE=~/Projects/my-game
./launch.sh
```

Gremlin's file tools restrict paths to the configured workspace and reject absolute paths and explicit parent-directory traversal.

## Built-in Tools

Gremlin currently provides tools for:

### Files

* `list_files`
* `read_file`
* `write_file`
* `apply_patch`
* `make_directory`
* `search_files`

### Project Detection

* `detect_project_type`
* `get_project_profile`

### Development

* `run_command`
* `run_tests`

### Git

* `git_status`
* `git_diff`
* `git_log`

## Project Support

Gremlin can detect several common project types automatically.

Currently supported profiles include:

* Godot 4
* Python
* Node.js
* Rust
* Go
* C/C++
* .NET

Multiple project types can coexist in the same workspace.

Gremlin uses project configuration files as the source of truth rather than assuming what a project is.

## Safety

Gremlin is intended to run as a **local development agent**, not as a security sandbox.

Commands execute using the permissions of the user running Gremlin.

Gremlin does provide several safeguards:

* Commands are executed without implicit shell interpretation.
* Workspace file paths are validated.
* Parent-directory traversal is rejected.
* Command timeouts are enforced.
* Child processes are placed in their own process group for cleanup.
* Obviously destructive commands are blocked by default.
* Git reset/clean-style destructive operations are blocked by default.
* File writes use temporary files followed by atomic replacement where possible.

However, these protections should **not** be interpreted as a security boundary.

If a development tool launched by Gremlin has operating-system access, that tool may itself have significant capabilities.

Only run Gremlin with models and projects you trust.

### Destructive Commands

Gremlin blocks commands classified as obviously destructive by default.

This behavior can be changed with:

```bash
GREMLIN_ALLOW_DESTRUCTIVE_COMMANDS=1
```

Only enable this when you understand the consequences.

## Read-Only Tasks

When a user asks Gremlin to inspect, analyze, investigate, diagnose, review, check, or explain something without requesting modifications, Gremlin is instructed to operate in read-only mode.

Read-only operations include:

* Reading files
* Searching files
* Listing files
* Detecting project types
* Running safe verification commands
* Inspecting Git status
* Inspecting Git diffs

Modification tools are reserved for tasks that actually request changes.

## Git

Gremlin can inspect Git repositories without automatically modifying Git history.

Useful commands inside Gremlin include:

```text
/status
```

and natural-language requests such as:

```text
Show me the current git diff.
```

Gremlin does not automatically commit changes.

It also avoids casually discarding existing user changes.

## Interactive Commands

Inside Gremlin:

```text
/help
```

Show available commands.

```text
/status
```

Show Gremlin's current configuration.

```text
/workspace
```

Show the active workspace.

```text
/model
```

Show the configured Ollama model.

```text
/tools
```

Show available tools.

```text
/profile
```

Detect the current project profile.

```text
/clear
```

Clear conversation context.

Use:

```text
exit
```

or:

```text
quit
```

to leave Gremlin.

Press `Ctrl+C` to interrupt the current operation.

## Architecture

Gremlin consists of two primary components:

```text
launch.sh
    │
    ├── validates Python
    ├── creates .venv
    ├── installs dependencies
    ├── checks Ollama
    ├── checks the model
    └── launches
          │
          ▼
      gremlin.py
          │
          ├── Ollama
          ├── filesystem tools
          ├── command execution
          ├── project detection
          ├── verification
          └── Git inspection
```

`launch.sh` handles local environment setup and startup.

`gremlin.py` contains the development-agent logic and tool implementations.

## Development

Gremlin itself is intentionally lightweight.

There is no separate server or web application required. Ollama provides the local model runtime while Gremlin provides the agent loop and development tools.

To work on Gremlin itself:

```bash
./launch.sh --workspace .
```

You can then ask Gremlin to inspect or modify its own source code.

## Known Limitations

Gremlin is intentionally local and simple.

Some limitations include:

* Ollama must be installed separately.
* Model performance depends heavily on the selected model and available hardware.
* Command execution is not a security sandbox.
* Project detection is marker-based.
* Automatic verification is currently limited to supported project profiles.
* Binary files are not exposed through the normal text file tools.
* Shell operators are not interpreted by `run_command`.

## License

Add your preferred license here before publishing the repository.

If you intend to make Gremlin open source, adding a `LICENSE` file to the repository is recommended.

---

**Gremlin 2.0** — local tools, local models, local development.

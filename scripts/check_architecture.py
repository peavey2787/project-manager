from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "repo_manager"
TESTS = ROOT / "tests"

MAX_MODULE_LINES = 500
MAX_CLASS_METHODS = 30
MAX_FOLDER_PY_FILES = 15
MAX_FUNCTION_LINES = 120

# Low-level Windows adapters are inherently verbose because they mirror Win32/
# COM APIs and keep ABI details together. These are explicit, reviewed
# exceptions rather than a blanket escape hatch.
MODULE_LINE_EXCEPTIONS = {
    "repo_manager/platform/windows/msaa/events.py": (700, "WinEvent message-loop/COM adapter"),
    "repo_manager/platform/windows/msaa/tree.py": (650, "MSAA tree traversal adapter"),
    "repo_manager/platform/windows/uia_fallback.py": (600, "isolated PowerShell/UIA fallback adapter"),
}
FUNCTION_LINE_EXCEPTIONS = {
    ("repo_manager/browser/launch.py", "launch_windows_browser"): (180, "browser launch/capture transaction"),
    ("repo_manager/commands/terminal_process.py", "run_windows_commands_in_terminal"): (180, "terminal launch transaction"),
    ("repo_manager/commands/terminal_window.py", "_focus_windows_console_by_pid"): (220, "Win32 console focus fallback ladder"),
    ("repo_manager/platform/windows/msaa/downloads.py", "download_latest_msaa"): (210, "COM-thread download scan/invocation"),
    ("repo_manager/platform/windows/msaa/browser_url.py", "read_browser_active_url_msaa"): (140, "browser chrome accessibility traversal"),
    ("repo_manager/chat/polling.py", "_run"): (180, "watchdog/poll orchestration"),
    ("repo_manager/chat/polling.py", "_apply_snapshots"): (140, "state reconciliation transaction"),
}

FORBIDDEN_LEGACY_PATHS = {
    "repo_manager/app.py",
    "repo_manager/operations.py",
    "repo_manager/windows_msaa.py",
    "repo_manager/windows_accessibility.py",
    "repo_manager/chat_watcher.py",
    "repo_manager/dialogs.py",
}
FORBIDDEN_CATCHALL_NAMES = {"utils.py", "helpers.py", "operations.py", "misc_utils.py"}


@dataclass(frozen=True)
class Violation:
    path: str
    message: str


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _module_imports(tree: ast.AST) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def check_repo() -> list[Violation]:
    violations: list[Violation] = []

    for rel in sorted(FORBIDDEN_LEGACY_PATHS):
        if (ROOT / rel).exists():
            violations.append(Violation(rel, "legacy/superseded module must not exist"))

    for path in PACKAGE.rglob("*.py"):
        rel = _rel(path)
        if path.name in FORBIDDEN_CATCHALL_NAMES:
            violations.append(Violation(rel, f"catch-all module name is forbidden: {path.name}"))
        text = path.read_text(encoding="utf-8")
        line_count = len(text.splitlines())
        limit, _reason = MODULE_LINE_EXCEPTIONS.get(rel, (MAX_MODULE_LINES, ""))
        if line_count > limit:
            violations.append(Violation(rel, f"module has {line_count} lines; limit is {limit}"))

        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            violations.append(Violation(rel, f"syntax error: {exc}"))
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if len(methods) > MAX_CLASS_METHODS:
                    violations.append(
                        Violation(rel, f"class {node.name} has {len(methods)} methods; limit is {MAX_CLASS_METHODS}")
                    )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                fn_limit, _reason = FUNCTION_LINE_EXCEPTIONS.get((rel, node.name), (MAX_FUNCTION_LINES, ""))
                if length > fn_limit:
                    violations.append(
                        Violation(rel, f"function {node.name} has {length} lines; limit is {fn_limit}")
                    )

        imports = _module_imports(tree)
        if rel.startswith("repo_manager/domain/"):
            internal = [name for name in imports if name.startswith("repo_manager")]
            if internal:
                violations.append(Violation(rel, f"domain layer imports application modules: {internal}"))
        if rel.startswith("repo_manager/platform/") and any("ui" in name.split(".") for name in imports):
            violations.append(Violation(rel, "platform layer must not import UI"))
        if rel.startswith(("repo_manager/archive/", "repo_manager/browser/", "repo_manager/chat/", "repo_manager/commands/")):
            if any("ui" in name.split(".") for name in imports):
                violations.append(Violation(rel, "service subsystem must not import UI"))

    for folder in [p for p in PACKAGE.rglob("*") if p.is_dir()] + [PACKAGE]:
        count = len(list(folder.glob("*.py")))
        if count > MAX_FOLDER_PY_FILES:
            violations.append(Violation(_rel(folder), f"folder contains {count} Python files; limit is {MAX_FOLDER_PY_FILES}"))

    for path in TESTS.rglob("test_*.py"):
        if path.name.startswith("test_v") and len(path.name) > 6 and path.name[6].isdigit():
            violations.append(Violation(_rel(path), "tests must be named for behavior, not historical revision"))

    return violations


def main() -> int:
    violations = check_repo()
    if violations:
        print("Architecture gate: FAIL")
        for violation in violations:
            print(f"- {violation.path}: {violation.message}")
        return 1
    print("Architecture gate: PASS")
    print("- no legacy monolith modules")
    print("- module/class/function size guardrails satisfied")
    print("- dependency direction guardrails satisfied")
    print("- folder-size and behavior-test naming guardrails satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

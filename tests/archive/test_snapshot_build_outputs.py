from __future__ import annotations

import tempfile
from pathlib import Path

from repo_manager.archive.snapshots import compare_snapshot, iter_snapshot_entries
from repo_manager.domain import Project


def test_snapshot_ignores_common_and_project_build_output_dirs_at_any_depth() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "src" / "main.rs").write_text("fn main() {}", encoding="utf-8")
        for rel in [
            "target/debug/app.exe",
            "web/dist/index.html",
            "web/.next/cache/item",
            "android/app/build/output.apk",
            "dotnet/bin/Debug/app.exe",
            "dotnet/obj/cache.bin",
            "custom/generated/thing.bin",
        ]:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("generated", encoding="utf-8")

        project = Project(excluded_dirs=["generated"])
        entries = iter_snapshot_entries(root, project.snapshot_excluded_dirs)

        assert "src/" in entries
        assert "src/main.rs" in entries
        lowered = "\n".join(entries).casefold()
        for name in ["target", "dist", ".next", "build", "bin", "obj", "generated"]:
            assert f"/{name}/" not in f"/{lowered}"


def test_snapshot_comparison_filters_build_outputs_from_legacy_saved_snapshot() -> None:
    project = Project()
    saved = ["src/", "src/main.rs", "target/", "target/debug/", "target/debug/app.exe"]
    current = ["src/", "src/main.rs"]
    diff = compare_snapshot(saved, current, project.snapshot_excluded_dirs)
    assert not diff.changed

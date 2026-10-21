from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


def natural_sort_key(path: Path) -> list:
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", path.name)]


def scan_chapter_files(novel_folder: Path, extensions: list[str] | None = None) -> list[Path]:
    if extensions is None:
        extensions = [".txt"]
    files: list[Path] = []
    for ext in extensions:
        files.extend(novel_folder.glob(f"*{ext}"))
    return sorted(files, key=natural_sort_key)


def load_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {"next_index": 0, "last_submitted_at": None, "submitted": []}
    return json.loads(state_file.read_text(encoding="utf-8"))


def save_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def mark_submitted(state_file: Path, chapter_path: str, next_chapter_url: str | None = None) -> None:
    state = load_state(state_file)
    state["next_index"] += 1
    state["last_submitted_at"] = datetime.now().astimezone().isoformat()
    state["submitted"].append({"file": chapter_path, "at": state["last_submitted_at"]})
    if next_chapter_url:
        state["next_chapter_url"] = next_chapter_url
    save_state(state_file, state)

#!/usr/bin/env python3
"""
cc-sessions: Claude Code 对话管理工具

管理 ~/.claude/ 下的对话记录，支持列表、搜索、删除、清理。
纯 Python 3 标准库实现，无外部依赖。
"""

import argparse
import curses
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"
FILE_HISTORY_DIR = CLAUDE_DIR / "file-history"
DEBUG_DIR = CLAUDE_DIR / "debug"
TASKS_DIR = CLAUDE_DIR / "tasks"
SESSION_ENV_DIR = CLAUDE_DIR / "session-env"
BACKUPS_DIR = CLAUDE_DIR / "backups" / "sessions"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# ─── Colors ───────────────────────────────────────────────────────────

class C:
    """ANSI color helpers. Auto-disabled when not a TTY."""
    _enabled = sys.stdout.isatty()

    @staticmethod
    def _wrap(code: str, text: str) -> str:
        if C._enabled:
            return f"\033[{code}m{text}\033[0m"
        return text

    @staticmethod
    def bold(t: str) -> str: return C._wrap("1", t)
    @staticmethod
    def dim(t: str) -> str: return C._wrap("2", t)
    @staticmethod
    def green(t: str) -> str: return C._wrap("32", t)
    @staticmethod
    def yellow(t: str) -> str: return C._wrap("33", t)
    @staticmethod
    def red(t: str) -> str: return C._wrap("31", t)
    @staticmethod
    def cyan(t: str) -> str: return C._wrap("36", t)
    @staticmethod
    def magenta(t: str) -> str: return C._wrap("35", t)


# ─── Data ─────────────────────────────────────────────────────────────

@dataclass
class SessionInfo:
    session_id: str
    project_name: str           # e.g. "-Users-tei-Codes"
    project_dir: Path           # full path to project dir under ~/.claude/projects/
    jsonl_path: Optional[Path] = None
    first_prompt: str = ""
    custom_title: Optional[str] = None
    message_count: int = 0
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
    git_branch: Optional[str] = None
    cwd: Optional[str] = None     # original working directory (project path)
    total_size: int = 0
    related_files: Dict[str, Path] = field(default_factory=dict)

    @property
    def display_title(self) -> str:
        """Title for display: custom_title > first_prompt > session_id[:8]"""
        return self.custom_title or self.first_prompt or self.session_id[:8]


# ─── Session Manager ─────────────────────────────────────────────────

class SessionManager:

    def discover_sessions(self, project_filter: Optional[str] = None) -> List[SessionInfo]:
        """Discover all sessions across all projects."""
        sessions = []
        if not PROJECTS_DIR.exists():
            return sessions

        for project_dir in sorted(PROJECTS_DIR.iterdir()):
            if not project_dir.is_dir():
                continue
            project_name = project_dir.name
            if project_filter and project_filter not in project_name:
                continue

            # Load sessions-index.json if available
            index_data = self._read_sessions_index(project_dir)

            # Scan for .jsonl files
            for jsonl in sorted(project_dir.glob("*.jsonl")):
                sid = jsonl.stem
                if not UUID_RE.match(sid):
                    continue

                info = SessionInfo(
                    session_id=sid,
                    project_name=project_name,
                    project_dir=project_dir,
                    jsonl_path=jsonl,
                )

                # Try index first
                if sid in index_data:
                    idx = index_data[sid]
                    info.first_prompt = idx.get("firstPrompt", "")
                    info.custom_title = idx.get("customTitle")
                    info.message_count = idx.get("messageCount", 0)
                    info.git_branch = idx.get("gitBranch")
                    if idx.get("created"):
                        info.created = _parse_iso(idx["created"])
                    if idx.get("modified"):
                        info.modified = _parse_iso(idx["modified"])

                # Always parse jsonl for custom_title (index may not have it)
                # and fill in missing fields
                if not info.first_prompt or not info.custom_title:
                    prompt, title, ts, count, cwd = self._extract_from_jsonl(jsonl)
                    if not info.first_prompt:
                        info.first_prompt = prompt
                    if not info.custom_title and title:
                        info.custom_title = title
                    if not info.created:
                        info.created = ts
                    if not info.message_count:
                        info.message_count = count
                    if cwd:
                        info.cwd = cwd

                # Use file mtime as modified if not set
                if not info.modified:
                    info.modified = datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc)
                if not info.created:
                    info.created = info.modified

                info.total_size = jsonl.stat().st_size
                sessions.append(info)

        # Sort by modified desc
        sessions.sort(key=lambda s: s.modified or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return sessions

    def search_sessions(self, keyword: str, project_filter: Optional[str] = None) -> List[Tuple[SessionInfo, str]]:
        """Search sessions by keyword in content. Returns (session, matched_line) tuples."""
        results = []
        sessions = self.discover_sessions(project_filter)
        keyword_lower = keyword.lower()

        for info in sessions:
            # Check title and first_prompt first
            if info.custom_title and keyword_lower in info.custom_title.lower():
                results.append((info, info.custom_title))
                continue
            if keyword_lower in info.first_prompt.lower():
                results.append((info, info.first_prompt))
                continue

            # Search in jsonl content
            if info.jsonl_path and info.jsonl_path.exists():
                match = self._search_in_jsonl(info.jsonl_path, keyword_lower)
                if match:
                    results.append((info, match))

        return results

    def get_session_files(self, session_id: str) -> Dict[str, Path]:
        """Find all files/dirs related to a session ID."""
        files = {}

        # Search across all projects for the jsonl
        if PROJECTS_DIR.exists():
            for project_dir in PROJECTS_DIR.iterdir():
                if not project_dir.is_dir():
                    continue
                jsonl = project_dir / f"{session_id}.jsonl"
                if jsonl.exists():
                    files["jsonl"] = jsonl
                meta_dir = project_dir / session_id
                if meta_dir.is_dir():
                    files["metadata"] = meta_dir

        # Other locations
        fh = FILE_HISTORY_DIR / session_id
        if fh.is_dir():
            files["file-history"] = fh

        debug = DEBUG_DIR / f"{session_id}.txt"
        if debug.exists():
            files["debug"] = debug

        tasks = TASKS_DIR / session_id
        if tasks.is_dir():
            files["tasks"] = tasks

        env = SESSION_ENV_DIR / session_id
        if env.is_dir():
            files["session-env"] = env

        return files

    def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """Get detailed info for a specific session."""
        files = self.get_session_files(session_id)
        if not files:
            return None

        # Find project info
        jsonl_path = files.get("jsonl")
        if jsonl_path:
            project_dir = jsonl_path.parent
        elif files.get("metadata"):
            project_dir = files["metadata"].parent
        else:
            project_dir = Path("unknown")

        info = SessionInfo(
            session_id=session_id,
            project_name=project_dir.name if project_dir.name != "unknown" else "unknown",
            project_dir=project_dir,
            jsonl_path=jsonl_path,
            related_files=files,
        )

        if jsonl_path and jsonl_path.exists():
            prompt, title, ts, count, cwd = self._extract_from_jsonl(jsonl_path)
            info.first_prompt = prompt
            info.custom_title = title
            info.created = ts
            info.message_count = count
            info.cwd = cwd
            info.modified = datetime.fromtimestamp(jsonl_path.stat().st_mtime, tz=timezone.utc)

        # Calculate total size
        total = 0
        for path in files.values():
            if path.is_file():
                total += path.stat().st_size
            elif path.is_dir():
                total += _dir_size(path)
        info.total_size = total

        return info

    def delete_session(self, session_id: str, dry_run: bool = False,
                       backup: bool = True, force: bool = False) -> bool:
        """Delete a session and all related files."""
        info = self.get_session_info(session_id)
        if not info:
            print(C.red(f"Session not found: {session_id}"))
            return False

        files = info.related_files

        # Check if recently active
        if info.jsonl_path and info.jsonl_path.exists():
            mtime = info.jsonl_path.stat().st_mtime
            age_min = (datetime.now().timestamp() - mtime) / 60
            if age_min < 5:
                print(C.yellow(f"WARNING: This session was modified {age_min:.0f} minutes ago, it may be in use!"))
                if not force and not dry_run:
                    resp = input("Continue? [y/N] ").strip().lower()
                    if resp != "y":
                        print("Aborted.")
                        return False

        # Show what will be deleted
        print(f"\n{C.bold('Session:')} {session_id}")
        if info.custom_title:
            print(f"{C.bold('Title:')}   {info.custom_title}")
        print(f"{C.bold('Preview:')} {info.first_prompt[:100]}")
        print(f"{C.bold('Size:')}    {_fmt_size(info.total_size)}")
        print(f"\n{C.bold('Files to delete:')}")
        for label, path in files.items():
            size = path.stat().st_size if path.is_file() else _dir_size(path) if path.is_dir() else 0
            print(f"  [{label}] {path} ({_fmt_size(size)})")
        print(f"  [history.jsonl] entries with sessionId={session_id}")

        # Check sessions-index.json
        has_index = False
        if info.project_dir and info.project_dir.exists():
            idx_path = info.project_dir / "sessions-index.json"
            if idx_path.exists():
                has_index = True
                print(f"  [sessions-index] entry in {idx_path}")

        if dry_run:
            print(C.yellow("\n[DRY RUN] No files were deleted."))
            return True

        # Confirm
        if not force:
            resp = input(f"\n{C.bold('Delete this session?')} [y/N] ").strip().lower()
            if resp != "y":
                print("Aborted.")
                return False

        # Backup
        if backup:
            backup_path = self._backup_session(session_id, files)
            if backup_path:
                print(C.dim(f"Backup saved: {backup_path}"))

        # Delete files and dirs
        for label, path in files.items():
            try:
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
                print(C.green(f"  Deleted [{label}]"))
            except Exception as e:
                print(C.red(f"  Failed to delete [{label}]: {e}"))

        # Clean history.jsonl
        removed = self._remove_from_history(session_id)
        print(C.green(f"  Removed {removed} entries from history.jsonl"))

        # Clean sessions-index.json
        if has_index:
            self._remove_from_sessions_index(info.project_dir, session_id)
            print(C.green("  Removed entry from sessions-index.json"))

        print(C.green(f"\nSession {session_id[:8]}... deleted successfully."))
        return True

    def delete_before(self, before_date: datetime, project_filter: Optional[str] = None,
                      dry_run: bool = False, force: bool = False) -> int:
        """Delete all sessions modified before the given date."""
        sessions = self.discover_sessions(project_filter)
        to_delete = [s for s in sessions if s.modified and s.modified < before_date]

        if not to_delete:
            print("No sessions found before that date.")
            return 0

        print(f"Found {C.bold(str(len(to_delete)))} sessions before {before_date.strftime('%Y-%m-%d')}:\n")
        for s in to_delete:
            mod = s.modified.strftime("%Y-%m-%d") if s.modified else "?"
            title = s.display_title[:60].replace("\n", " ")
            print(f"  {C.dim(s.session_id[:8])}  {mod}  {title}")

        if dry_run:
            print(C.yellow(f"\n[DRY RUN] Would delete {len(to_delete)} sessions."))
            return len(to_delete)

        if not force:
            resp = input(f"\nDelete all {len(to_delete)} sessions? [y/N] ").strip().lower()
            if resp != "y":
                print("Aborted.")
                return 0

        count = 0
        for s in to_delete:
            print(f"\nDeleting {s.session_id[:8]}...")
            if self.delete_session(s.session_id, force=True, backup=True):
                count += 1

        print(f"\n{C.green(f'Deleted {count} sessions.')}")
        return count

    def find_orphans(self) -> List[Tuple[str, Path, str]]:
        """Find orphaned metadata dirs (no corresponding .jsonl)."""
        orphans = []
        if not PROJECTS_DIR.exists():
            return orphans

        for project_dir in PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            for item in project_dir.iterdir():
                if item.is_dir() and UUID_RE.match(item.name):
                    jsonl = project_dir / f"{item.name}.jsonl"
                    if not jsonl.exists():
                        orphans.append((item.name, item, project_dir.name))

        # Also check file-history, tasks, debug, session-env
        for base_dir, label in [
            (FILE_HISTORY_DIR, "file-history"),
            (TASKS_DIR, "tasks"),
            (SESSION_ENV_DIR, "session-env"),
        ]:
            if base_dir.exists():
                for item in base_dir.iterdir():
                    if item.is_dir() and UUID_RE.match(item.name):
                        has_jsonl = False
                        if PROJECTS_DIR.exists():
                            for pd in PROJECTS_DIR.iterdir():
                                if pd.is_dir() and (pd / f"{item.name}.jsonl").exists():
                                    has_jsonl = True
                                    break
                        if not has_jsonl:
                            orphans.append((item.name, item, label))

        if DEBUG_DIR.exists():
            for item in DEBUG_DIR.iterdir():
                if item.is_file() and item.suffix == ".txt" and UUID_RE.match(item.stem):
                    has_jsonl = False
                    if PROJECTS_DIR.exists():
                        for pd in PROJECTS_DIR.iterdir():
                            if pd.is_dir() and (pd / f"{item.stem}.jsonl").exists():
                                has_jsonl = True
                                break
                    if not has_jsonl:
                        orphans.append((item.stem, item, "debug"))

        return orphans

    def get_stats(self) -> dict:
        """Gather overall statistics."""
        stats = {
            "projects": 0,
            "sessions": 0,
            "total_jsonl_size": 0,
            "total_metadata_size": 0,
            "total_file_history_size": 0,
            "total_debug_size": 0,
            "history_entries": 0,
            "orphans": 0,
        }

        if PROJECTS_DIR.exists():
            for pd in PROJECTS_DIR.iterdir():
                if not pd.is_dir():
                    continue
                has_sessions = False
                for f in pd.glob("*.jsonl"):
                    if UUID_RE.match(f.stem):
                        stats["sessions"] += 1
                        stats["total_jsonl_size"] += f.stat().st_size
                        has_sessions = True
                for d in pd.iterdir():
                    if d.is_dir() and UUID_RE.match(d.name):
                        stats["total_metadata_size"] += _dir_size(d)
                if has_sessions:
                    stats["projects"] += 1

        if FILE_HISTORY_DIR.exists():
            stats["total_file_history_size"] = _dir_size(FILE_HISTORY_DIR)
        if DEBUG_DIR.exists():
            stats["total_debug_size"] = _dir_size(DEBUG_DIR)
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE) as f:
                stats["history_entries"] = sum(1 for _ in f)

        stats["orphans"] = len(self.find_orphans())
        return stats

    # ─── Internal Methods ─────────────────────────────────────────

    def _read_sessions_index(self, project_dir: Path) -> Dict[str, dict]:
        idx_path = project_dir / "sessions-index.json"
        if not idx_path.exists():
            return {}
        try:
            with open(idx_path) as f:
                data = json.load(f)
            return {e["sessionId"]: e for e in data.get("entries", [])}
        except (json.JSONDecodeError, KeyError):
            return {}

    def _extract_from_jsonl(self, jsonl_path: Path, max_lines: int = 200) -> Tuple[str, Optional[str], Optional[datetime], int, Optional[str]]:
        """Extract first user message, custom title, timestamp, message count, and cwd.

        Returns: (first_prompt, custom_title, timestamp, message_count, cwd)
        """
        first_prompt = ""
        first_ts = None
        msg_count = 0
        cwd = None

        # First pass: get first prompt, cwd, and message count (first N lines)
        try:
            with open(jsonl_path) as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    dtype = d.get("type")
                    if dtype in ("user", "assistant"):
                        msg_count += 1

                    # Extract cwd from first message that has it
                    if not cwd and d.get("cwd"):
                        cwd = d["cwd"]

                    if not first_prompt and dtype == "user":
                        ts = d.get("timestamp")
                        if ts:
                            first_ts = _parse_iso(ts)
                        msg = d.get("message", {})
                        content = msg.get("content", "")
                        first_prompt = _extract_text(content)

                    if i >= max_lines:
                        avg_line_size = jsonl_path.stat().st_size / (i + 1)
                        total_lines = jsonl_path.stat().st_size / avg_line_size
                        ratio = msg_count / (i + 1) if i > 0 else 0.5
                        msg_count = int(total_lines * ratio)
                        break
        except Exception:
            pass

        # Second pass: find custom title by scanning from end of file
        custom_title = self._find_custom_title(jsonl_path)

        return first_prompt, custom_title, first_ts, msg_count, cwd

    def _find_custom_title(self, jsonl_path: Path) -> Optional[str]:
        """Find the last custom-title entry by fast scanning the full file.

        Uses a byte-level search for the marker string to avoid parsing every line.
        """
        try:
            with open(jsonl_path, "rb") as f:
                data = f.read()

            # Fast: search for all occurrences of the marker
            marker = b'"custom-title"'
            title = None
            start = 0
            while True:
                pos = data.find(marker, start)
                if pos == -1:
                    break
                # Find the full line containing this marker
                line_start = data.rfind(b"\n", 0, pos)
                line_end = data.find(b"\n", pos)
                if line_start == -1:
                    line_start = 0
                else:
                    line_start += 1
                if line_end == -1:
                    line_end = len(data)
                line = data[line_start:line_end].decode("utf-8", errors="replace")
                try:
                    d = json.loads(line)
                    if d.get("type") == "custom-title" and d.get("customTitle"):
                        title = d["customTitle"]
                except (json.JSONDecodeError, KeyError):
                    pass
                start = pos + len(marker)

            return title
        except Exception:
            pass
        return None

    def get_recent_messages(self, jsonl_path: Path, count: int = 10) -> List[Tuple[str, str]]:
        """Extract the last N user/assistant messages from a jsonl file.

        Returns list of (role, text) tuples, most recent last.
        """
        messages = []
        try:
            file_size = jsonl_path.stat().st_size
            # Read from end in increasing chunks until we have enough messages
            chunk_size = 32768  # 32KB
            for multiplier in range(1, 30):
                read_size = min(chunk_size * multiplier, file_size)
                with open(jsonl_path, "rb") as f:
                    f.seek(max(0, file_size - read_size))
                    data = f.read().decode("utf-8", errors="replace")

                messages = []
                for line in data.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    dtype = d.get("type")
                    if dtype not in ("user", "assistant"):
                        continue
                    role = d.get("message", {}).get("role", dtype)
                    content = d.get("message", {}).get("content", "")
                    text = _extract_text(content)
                    if text:
                        messages.append((role, text))

                if len(messages) >= count or read_size >= file_size:
                    break

            return messages[-count:]
        except Exception:
            return []

    def _search_in_jsonl(self, jsonl_path: Path, keyword_lower: str) -> Optional[str]:
        """Search for keyword in a jsonl file. Returns first matching line."""
        try:
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if keyword_lower not in line.lower():
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") != "user":
                        continue
                    content = _extract_text(d.get("message", {}).get("content", ""))
                    if keyword_lower in content.lower():
                        return content[:200]
        except Exception:
            pass
        return None

    def _backup_session(self, session_id: str, files: Dict[str, Path]) -> Optional[Path]:
        """Create a tar.gz backup of session files."""
        try:
            BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = BACKUPS_DIR / f"{session_id[:8]}_{ts}.tar.gz"
            with tarfile.open(backup_path, "w:gz") as tar:
                for label, path in files.items():
                    if path.exists():
                        tar.add(path, arcname=f"{session_id}/{label}/{path.name}")
            return backup_path
        except Exception as e:
            print(C.yellow(f"Warning: backup failed: {e}"))
            return None

    def _remove_from_history(self, session_id: str) -> int:
        """Remove entries for session_id from history.jsonl. Returns count removed."""
        if not HISTORY_FILE.exists():
            return 0
        try:
            kept = []
            removed = 0
            with open(HISTORY_FILE) as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        d = json.loads(stripped)
                        if d.get("sessionId") == session_id:
                            removed += 1
                            continue
                    except json.JSONDecodeError:
                        pass
                    kept.append(line)

            if removed > 0:
                fd, tmp = tempfile.mkstemp(dir=CLAUDE_DIR, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w") as f:
                        f.writelines(kept)
                    os.replace(tmp, HISTORY_FILE)
                except Exception:
                    os.unlink(tmp)
                    raise
            return removed
        except Exception as e:
            print(C.yellow(f"Warning: failed to clean history.jsonl: {e}"))
            return 0

    def _remove_from_sessions_index(self, project_dir: Path, session_id: str):
        """Remove entry from sessions-index.json."""
        idx_path = project_dir / "sessions-index.json"
        if not idx_path.exists():
            return
        try:
            with open(idx_path) as f:
                data = json.load(f)
            entries = data.get("entries", [])
            new_entries = [e for e in entries if e.get("sessionId") != session_id]
            if len(new_entries) < len(entries):
                data["entries"] = new_entries
                fd, tmp = tempfile.mkstemp(dir=str(project_dir), suffix=".tmp")
                try:
                    with os.fdopen(fd, "w") as f:
                        json.dump(data, f, indent=2)
                    os.replace(tmp, idx_path)
                except Exception:
                    os.unlink(tmp)
                    raise
        except Exception as e:
            print(C.yellow(f"Warning: failed to update sessions-index.json: {e}"))


# ─── Helpers ──────────────────────────────────────────────────────────

def _extract_text(content) -> str:
    """Extract plain text from message content (string or list format)."""
    if isinstance(content, str):
        text = re.sub(r"<[^>]+>.*?</[^>]+>", "", content, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "", text)
        return text.strip()
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                text = re.sub(r"<[^>]+>.*?</[^>]+>", "", text, flags=re.DOTALL)
                text = re.sub(r"<[^>]+>", "", text)
                text = text.strip()
                if text:
                    return text
    return ""


def _parse_iso(s) -> Optional[datetime]:
    """Parse ISO 8601 timestamp string."""
    if not s:
        return None
    try:
        if isinstance(s, (int, float)):
            return datetime.fromtimestamp(s / 1000, tz=timezone.utc)
        s = str(s)
        if s.isdigit():
            return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, OSError):
        return None


def _fmt_size(size_bytes: int) -> str:
    """Format file size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _relative_time(dt: Optional[datetime]) -> str:
    """Format datetime as relative time string."""
    if not dt:
        return "?"
    now = datetime.now(tz=timezone.utc)
    diff = now - dt
    seconds = diff.total_seconds()
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        m = int(seconds / 60)
        return f"{m} min ago"
    elif seconds < 86400:
        h = int(seconds / 3600)
        return f"{h} hour{'s' if h > 1 else ''} ago"
    elif seconds < 86400 * 30:
        d = int(seconds / 86400)
        return f"{d} day{'s' if d > 1 else ''} ago"
    else:
        return dt.strftime("%Y-%m-%d")


def _dir_size(path: Path) -> int:
    """Calculate total size of a directory."""
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except Exception:
        pass
    return total


def _short_project(name: str) -> str:
    """Shorten project name for display: -Users-tei-Codes -> Codes"""
    parts = name.split("-")
    meaningful = [p for p in parts if p and p not in ("Users", "tei")]
    return "-".join(meaningful[-2:]) if meaningful else name


def _resolve_session_id(mgr: SessionManager, sid: str) -> Optional[str]:
    """Resolve a partial session ID to full ID."""
    if len(sid) >= 36:
        return sid
    sessions = mgr.discover_sessions()
    matches = [s for s in sessions if s.session_id.startswith(sid)]
    if len(matches) == 0:
        print(C.red(f"No session found starting with '{sid}'"))
        return None
    elif len(matches) > 1:
        print(C.yellow(f"Ambiguous ID '{sid}', matches:"))
        for m in matches:
            print(f"  {m.session_id}  {m.display_title[:60]}")
        return None
    return matches[0].session_id


# ─── Interactive TUI ──────────────────────────────────────────────────

class TUI:
    """Interactive terminal UI for managing sessions using curses."""

    def __init__(self):
        self.mgr = SessionManager()
        self.sessions: List[SessionInfo] = []
        self.filtered: List[SessionInfo] = []
        self.cursor = 0
        self.scroll = 0
        self.search_query = ""
        self.mode = "list"  # list, search, confirm_delete, detail
        self.message = ""
        self.message_color = 0
        self.delete_target: Optional[SessionInfo] = None
        self._resume_pending: Optional[Tuple[str, str]] = None  # (session_id, cwd)

    def run(self):
        """Launch the TUI."""
        try:
            curses.wrapper(self._main)
        except KeyboardInterrupt:
            pass

    def _main(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(0)
        curses.use_default_colors()

        # Init color pairs
        curses.init_pair(1, curses.COLOR_CYAN, -1)       # session id
        curses.init_pair(2, curses.COLOR_GREEN, -1)       # success messages
        curses.init_pair(3, curses.COLOR_YELLOW, -1)      # warnings
        curses.init_pair(4, curses.COLOR_RED, -1)         # errors / delete
        curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)   # selected row
        curses.init_pair(6, curses.COLOR_WHITE, -1)       # normal
        curses.init_pair(7, curses.COLOR_MAGENTA, -1)     # project name

        self._load_sessions()

        while True:
            self._draw()
            key = stdscr.getch()

            if self.mode == "search":
                if key == 27:  # ESC
                    self.mode = "list"
                    self.search_query = ""
                    self.filtered = self.sessions
                    self.cursor = 0
                    self.scroll = 0
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    self.search_query = self.search_query[:-1]
                    self._apply_filter()
                elif key == 10:  # Enter
                    self.mode = "list"
                elif key == curses.KEY_UP:
                    self._move_cursor(-1)
                elif key == curses.KEY_DOWN:
                    self._move_cursor(1)
                elif 32 <= key <= 126:
                    self.search_query += chr(key)
                    self._apply_filter()

            elif self.mode == "confirm_delete":
                if key in (ord("y"), ord("Y")):
                    self._do_delete()
                else:
                    self.mode = "list"
                    self.message = "Cancelled."
                    self.message_color = 3

            elif self.mode == "detail":
                if key in (ord("q"), 27, ord("h"), curses.KEY_LEFT):
                    self.mode = "list"
                elif key in (ord("d"), ord("D")):
                    if self.filtered:
                        self.delete_target = self.filtered[self.cursor]
                        self.mode = "confirm_delete"
                elif key == 10:  # Enter = resume in new terminal tab
                    if self.filtered:
                        s = self.filtered[self.cursor]
                        self._resume_in_new_terminal(s)
                elif key == ord("c"):  # copy session ID to clipboard
                    if self.filtered:
                        self._copy_to_clipboard(self.filtered[self.cursor].session_id)

            else:  # list mode
                if key in (ord("q"), ord("Q")):
                    break
                elif key == curses.KEY_UP or key == ord("k"):
                    self._move_cursor(-1)
                elif key == curses.KEY_DOWN or key == ord("j"):
                    self._move_cursor(1)
                elif key in (ord("g"),):  # go to top
                    self.cursor = 0
                    self.scroll = 0
                elif key in (ord("G"),):  # go to bottom
                    self.cursor = max(0, len(self.filtered) - 1)
                elif key == ord("/"):
                    self.mode = "search"
                    self.search_query = ""
                elif key == 10 or key == curses.KEY_RIGHT or key == ord("l"):  # Enter / right / l
                    if self.filtered:
                        self.mode = "detail"
                elif key in (ord("d"), ord("D")):
                    if self.filtered:
                        self.delete_target = self.filtered[self.cursor]
                        self.mode = "confirm_delete"
                elif key == ord("c"):  # copy session ID to clipboard
                    if self.filtered:
                        self._copy_to_clipboard(self.filtered[self.cursor].session_id)
                elif key == ord("r") or key == ord("R"):
                    self._load_sessions()
                    self.message = "Refreshed."
                    self.message_color = 2

    def _resume_in_new_terminal(self, session: SessionInfo):
        """Open a new terminal tab and run claude --resume there."""
        sid = session.session_id
        cwd = session.cwd or os.path.expanduser("~")

        # Build the command to run in new tab
        cmd = f'cd {cwd} && claude --resume {sid}'

        # Use AppleScript to open a new Terminal/iTerm2 tab
        # Try iTerm2 first, fall back to Terminal.app
        iterm_script = f'''
        tell application "System Events"
            set frontApp to name of first application process whose frontmost is true
        end tell
        if frontApp is "iTerm2" then
            tell application "iTerm2"
                tell current window
                    create tab with default profile
                    tell current session
                        write text "{cmd}"
                    end tell
                end tell
            end tell
        else
            tell application "Terminal"
                activate
                do script "{cmd}"
            end tell
        end if
        '''
        try:
            subprocess.Popen(
                ["osascript", "-e", iterm_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            title = session.custom_title or sid[:8]
            self.message = f"Resuming '{title}' in new tab..."
            self.message_color = 2
        except Exception as e:
            self.message = f"Failed to open terminal: {e}"
            self.message_color = 4

    def _copy_to_clipboard(self, text: str):
        """Copy text to system clipboard (macOS pbcopy)."""
        import subprocess
        try:
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc.communicate(text.encode())
            self.message = f"Copied: {text[:8]}..."
            self.message_color = 2
        except Exception:
            self.message = "Failed to copy (pbcopy not available)"
            self.message_color = 4

    def _load_sessions(self):
        self.sessions = self.mgr.discover_sessions()
        self.filtered = self.sessions
        self.cursor = 0
        self.scroll = 0

    def _apply_filter(self):
        if not self.search_query:
            self.filtered = self.sessions
        else:
            q = self.search_query.lower()
            self.filtered = [
                s for s in self.sessions
                if q in s.display_title.lower()
                or q in s.first_prompt.lower()
                or q in s.session_id.lower()
                or (s.custom_title and q in s.custom_title.lower())
            ]
        self.cursor = 0
        self.scroll = 0

    def _move_cursor(self, delta: int):
        self.cursor = max(0, min(len(self.filtered) - 1, self.cursor + delta))
        # Adjust scroll
        h, _ = self.stdscr.getmaxyx()
        list_height = h - 5  # header + footer
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + list_height:
            self.scroll = self.cursor - list_height + 1

    def _do_delete(self):
        if not self.delete_target:
            return
        sid = self.delete_target.session_id
        # Perform deletion (non-interactive, forced, with backup)
        # Capture output
        old_stdout = sys.stdout
        sys.stdout = open(os.devnull, 'w')
        try:
            ok = self.mgr.delete_session(sid, force=True, backup=True)
        finally:
            sys.stdout.close()
            sys.stdout = old_stdout

        if ok:
            self.message = f"Deleted {sid[:8]} (backup saved)"
            self.message_color = 2
            # Remove from lists
            self.sessions = [s for s in self.sessions if s.session_id != sid]
            self.filtered = [s for s in self.filtered if s.session_id != sid]
            if self.cursor >= len(self.filtered):
                self.cursor = max(0, len(self.filtered) - 1)
        else:
            self.message = f"Failed to delete {sid[:8]}"
            self.message_color = 4

        self.delete_target = None
        self.mode = "list"

    def _safe_addstr(self, y: int, x: int, text: str, n: int, attr: int = 0):
        """Safely write to screen, catching curses errors at boundaries."""
        try:
            h, w = self.stdscr.getmaxyx()
            # Clamp to avoid writing past the last position
            max_n = max(0, w - x - 1) if y == h - 1 else max(0, w - x)
            if max_n <= 0:
                return
            self.stdscr.addnstr(y, x, text, min(n, max_n), attr)
        except curses.error:
            pass

    def _draw(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        if h < 4 or w < 20:
            return

        if self.mode == "detail":
            self._draw_detail(h, w)
            return

        # ── Header ──
        title = " cc-sessions "
        self._safe_addstr(0, 0, "─" * w, w, curses.color_pair(1))
        self._safe_addstr(0, 2, title, len(title), curses.A_BOLD | curses.color_pair(1))
        count_str = f" {len(self.filtered)}/{len(self.sessions)} sessions "
        if w > len(count_str) + len(title) + 4:
            self._safe_addstr(0, w - len(count_str) - 1, count_str, len(count_str), curses.color_pair(1))

        # ── Search bar ──
        if self.mode == "search":
            search_str = f" / {self.search_query}█"
            self._safe_addstr(1, 0, search_str, min(len(search_str), w - 1), curses.A_BOLD | curses.color_pair(3))
        elif self.search_query:
            search_str = f" filter: {self.search_query}"
            self._safe_addstr(1, 0, search_str, min(len(search_str), w - 1), curses.color_pair(3))

        # ── Session list ──
        list_start = 2
        list_height = h - 4

        for i in range(list_height):
            idx = self.scroll + i
            if idx >= len(self.filtered):
                break
            s = self.filtered[idx]
            y = list_start + i
            is_selected = (idx == self.cursor)

            time_str = _relative_time(s.modified)
            size_str = _fmt_size(s.total_size)

            # Title (custom title or first prompt)
            title = s.display_title.replace("\n", " ")
            max_title = w - 45
            if max_title > 0:
                title = title[:max_title]
            else:
                title = ""

            if is_selected:
                line = f" > {title:<{max_title}}  {time_str:>12s}  {size_str:>8s} "
                self._safe_addstr(y, 0, line[:w], w, curses.color_pair(5) | curses.A_BOLD)
            else:
                self._safe_addstr(y, 0, "   ", 3, curses.color_pair(6))
                self._safe_addstr(y, 3, title, min(len(title), max_title), curses.color_pair(6))
                meta = f"  {time_str:>12s}  {size_str:>8s}"
                meta_x = 3 + max_title
                if meta_x + len(meta) < w:
                    self._safe_addstr(y, meta_x, meta, len(meta), curses.color_pair(1))

        # ── Message bar ──
        if self.message:
            msg_y = h - 2
            self._safe_addstr(msg_y, 1, self.message[:w-2], w - 2, curses.color_pair(self.message_color))
            self.message = ""

        # ── Footer ──
        footer_y = h - 1
        if self.mode == "confirm_delete":
            target = self.delete_target
            if target:
                prompt = f" Delete '{target.display_title[:30]}'? (y/N) "
                self._safe_addstr(footer_y, 0, prompt[:w-1], w - 1, curses.A_BOLD | curses.color_pair(4))
        else:
            footer = " j/k:move  Enter:detail  /:search  d:delete  c:copy ID  r:refresh  q:quit "
            self._safe_addstr(footer_y, 0, "─" * (w - 1), w - 1, curses.color_pair(1))
            if len(footer) < w - 1:
                self._safe_addstr(footer_y, 1, footer, len(footer), curses.color_pair(1))

        self.stdscr.refresh()

    def _draw_detail(self, h: int, w: int):
        """Draw the detail view for the selected session."""
        if not self.filtered:
            return
        s = self.filtered[self.cursor]

        # Get full info
        info = self.mgr.get_session_info(s.session_id)
        if not info:
            info = s

        # Header
        title_str = f" {info.custom_title or info.session_id[:8]} "
        self._safe_addstr(0, 0, "─" * w, w, curses.color_pair(1))
        self._safe_addstr(0, 2, title_str, len(title_str), curses.A_BOLD | curses.color_pair(1))

        # Compact metadata line
        y = 2
        meta_parts = [
            info.session_id[:8],
            _short_project(info.project_name),
            f"{info.message_count} msgs",
            _fmt_size(info.total_size),
            _relative_time(info.modified),
        ]
        meta_line = "  " + "  ·  ".join(meta_parts)
        self._safe_addstr(y, 0, meta_line[:w], w, curses.color_pair(1))
        y += 1

        # Separator
        self._safe_addstr(y, 0, "─" * w, w, curses.color_pair(1))
        y += 1

        # Recent messages
        self._safe_addstr(y, 2, "Recent messages:", 16, curses.A_BOLD | curses.color_pair(6))
        y += 1

        if info.jsonl_path and info.jsonl_path.exists():
            # Calculate how many messages we can show
            available_lines = h - y - 2  # reserve footer
            max_msgs = max(3, available_lines // 3)  # ~3 lines per message
            recent = self.mgr.get_recent_messages(info.jsonl_path, count=max_msgs)

            for role, text in recent:
                if y >= h - 2:
                    break

                # Role indicator
                if role == "user":
                    prefix = "▶ You"
                    prefix_color = curses.color_pair(3) | curses.A_BOLD  # yellow
                else:
                    prefix = "◀ Claude"
                    prefix_color = curses.color_pair(1) | curses.A_BOLD  # cyan

                self._safe_addstr(y, 2, prefix, len(prefix), prefix_color)
                y += 1
                if y >= h - 2:
                    break

                # Message text (wrap to ~2 lines max)
                text_clean = text.replace("\n", " ").strip()
                line_width = w - 6
                if line_width <= 0:
                    continue
                for line_num in range(2):  # max 2 lines of content
                    if y >= h - 2:
                        break
                    start = line_num * line_width
                    chunk = text_clean[start:start + line_width]
                    if not chunk:
                        break
                    if line_num == 1 and len(text_clean) > start + line_width:
                        chunk = chunk[:line_width - 3] + "..."
                    self._safe_addstr(y, 4, chunk, len(chunk), curses.color_pair(6))
                    y += 1

                if y >= h - 2:
                    break
        else:
            self._safe_addstr(y, 4, "(no conversation data)", 22, curses.color_pair(3))

        # Footer
        footer_y = h - 1
        footer = " q:back  Enter:resume  d:delete  c:copy ID "
        self._safe_addstr(footer_y, 0, "─" * (w - 1), w - 1, curses.color_pair(1))
        self._safe_addstr(footer_y, 1, footer, len(footer), curses.color_pair(1))

        self.stdscr.refresh()


# ─── CLI Commands ─────────────────────────────────────────────────────

def cmd_list(args):
    mgr = SessionManager()
    sessions = mgr.discover_sessions(args.project)
    if args.limit:
        sessions = sessions[:args.limit]

    if not sessions:
        print("No sessions found.")
        return

    print(f"\n{C.bold(f'Sessions ({len(sessions)})')}\n")

    # Table header
    print(f"  {'ID':10s}  {'Modified':12s}  {'Size':>8s}  {'Title / Preview'}")
    print(f"  {'─' * 10}  {'─' * 12}  {'─' * 8}  {'─' * 45}")

    for s in sessions:
        sid = C.cyan(s.session_id[:8])
        mod = _relative_time(s.modified)
        size = _fmt_size(s.total_size)
        title = s.display_title.replace("\n", " ")[:60]
        print(f"  {sid}    {mod:>12s}  {size:>8s}  {title}")

    print()


def cmd_search(args):
    mgr = SessionManager()
    results = mgr.search_sessions(args.keyword, args.project)

    if not results:
        print(f"No sessions matching '{args.keyword}'.")
        return

    print(f"\n{C.bold(f'Search results for \"{args.keyword}\" ({len(results)} matches)')}\n")

    for info, matched in results:
        sid = C.cyan(info.session_id[:8])
        mod = _relative_time(info.modified)
        proj = _short_project(info.project_name)
        title = info.display_title.replace("\n", " ")[:60]
        matched_clean = matched.replace("\n", " ")[:80]
        print(f"  {sid}  {mod:>12s}  [{proj}]  {title}")
        if matched_clean != title:
            print(f"           {C.dim(matched_clean)}")
        print()


def cmd_info(args):
    mgr = SessionManager()
    sid = _resolve_session_id(mgr, args.session_id)
    if not sid:
        return

    info = mgr.get_session_info(sid)
    if not info:
        print(C.red(f"Session not found: {sid}"))
        return

    print(f"\n{C.bold('Session Details')}\n")
    print(f"  {C.bold('ID:'):20s} {info.session_id}")
    print(f"  {C.bold('Project:'):20s} {_short_project(info.project_name)}")
    if info.custom_title:
        print(f"  {C.bold('Title:'):20s} {info.custom_title}")
    print(f"  {C.bold('First message:'):20s} {info.first_prompt[:100]}")
    print(f"  {C.bold('Messages:'):20s} {info.message_count}")
    print(f"  {C.bold('Created:'):20s} {info.created.strftime('%Y-%m-%d %H:%M') if info.created else '?'}")
    print(f"  {C.bold('Modified:'):20s} {info.modified.strftime('%Y-%m-%d %H:%M') if info.modified else '?'} ({_relative_time(info.modified)})")
    print(f"  {C.bold('Total size:'):20s} {_fmt_size(info.total_size)}")

    if info.related_files:
        print(f"\n  {C.bold('Related files:')}")
        for label, path in info.related_files.items():
            size = path.stat().st_size if path.is_file() else _dir_size(path) if path.is_dir() else 0
            print(f"    [{label:15s}] {path} ({_fmt_size(size)})")
    print()


def cmd_delete(args):
    mgr = SessionManager()

    if args.before:
        try:
            before = datetime.strptime(args.before, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(C.red(f"Invalid date format: {args.before} (expected YYYY-MM-DD)"))
            return
        mgr.delete_before(before, args.project, dry_run=args.dry_run, force=args.force)
        return

    if not args.session_id:
        print(C.red("Please provide a session ID or --before DATE"))
        return

    sid = _resolve_session_id(mgr, args.session_id)
    if not sid:
        return
    mgr.delete_session(sid, dry_run=args.dry_run, force=args.force)


def cmd_clean(args):
    mgr = SessionManager()
    orphans = mgr.find_orphans()

    if not orphans:
        print(C.green("No orphaned files found."))
        return

    total_size = 0
    print(f"\n{C.bold(f'Orphaned files ({len(orphans)})')}\n")
    for sid, path, source in orphans:
        size = path.stat().st_size if path.is_file() else _dir_size(path) if path.is_dir() else 0
        total_size += size
        print(f"  {C.dim(sid[:8])}  [{source:15s}]  {path} ({_fmt_size(size)})")

    print(f"\n  Total: {_fmt_size(total_size)}")

    if args.dry_run:
        print(C.yellow("\n[DRY RUN] No files were deleted."))
        return

    if not args.force:
        resp = input(f"\nDelete all {len(orphans)} orphaned items? [y/N] ").strip().lower()
        if resp != "y":
            print("Aborted.")
            return

    for sid, path, source in orphans:
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            print(C.green(f"  Deleted {path}"))
        except Exception as e:
            print(C.red(f"  Failed: {path}: {e}"))

    print(C.green(f"\nCleaned {len(orphans)} orphaned items."))


def cmd_stats(args):
    mgr = SessionManager()
    s = mgr.get_stats()

    print(f"\n{C.bold('Claude Code Session Statistics')}\n")
    print(f"  {'Projects:':25s} {s['projects']}")
    print(f"  {'Sessions:':25s} {s['sessions']}")
    print(f"  {'History entries:':25s} {s['history_entries']}")
    print(f"  {'Orphaned items:':25s} {s['orphans']}")
    print()
    print(f"  {C.bold('Disk Usage:')}")
    print(f"  {'  Conversations (.jsonl):':25s} {_fmt_size(s['total_jsonl_size'])}")
    print(f"  {'  Metadata (subagents..):':25s} {_fmt_size(s['total_metadata_size'])}")
    print(f"  {'  File history:':25s} {_fmt_size(s['total_file_history_size'])}")
    print(f"  {'  Debug logs:':25s} {_fmt_size(s['total_debug_size'])}")
    total = s['total_jsonl_size'] + s['total_metadata_size'] + s['total_file_history_size'] + s['total_debug_size']
    print(f"  {'  ─────────────────':25s}")
    print(f"  {C.bold('  Total:'):25s} {C.bold(_fmt_size(total))}")
    print()


def cmd_tui(args):
    tui = TUI()
    tui.run()


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="cc-sessions",
        description="Claude Code conversation session manager",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # tui (default when no args)
    subparsers.add_parser("tui", help="Interactive TUI mode (default)")

    # list
    p_list = subparsers.add_parser("list", aliases=["ls"], help="List sessions")
    p_list.add_argument("--project", "-p", help="Filter by project name")
    p_list.add_argument("--limit", "-n", type=int, help="Limit number of results")

    # search
    p_search = subparsers.add_parser("search", aliases=["find"], help="Search sessions by keyword")
    p_search.add_argument("keyword", help="Keyword to search for")
    p_search.add_argument("--project", "-p", help="Filter by project name")

    # info
    p_info = subparsers.add_parser("info", aliases=["show"], help="Show session details")
    p_info.add_argument("session_id", help="Session ID (full or partial)")

    # delete
    p_delete = subparsers.add_parser("delete", aliases=["rm"], help="Delete sessions")
    p_delete.add_argument("session_id", nargs="?", help="Session ID (full or partial)")
    p_delete.add_argument("--before", help="Delete sessions before date (YYYY-MM-DD)")
    p_delete.add_argument("--project", "-p", help="Filter by project name")
    p_delete.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    p_delete.add_argument("--force", "-f", action="store_true", help="Skip confirmation")

    # clean
    p_clean = subparsers.add_parser("clean", help="Clean orphaned metadata")
    p_clean.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    p_clean.add_argument("--force", "-f", action="store_true", help="Skip confirmation")

    # stats
    subparsers.add_parser("stats", help="Show statistics")

    args = parser.parse_args()

    # Default to TUI when no command given and stdin is a TTY
    if not args.command:
        if sys.stdin.isatty() and sys.stdout.isatty():
            cmd_tui(args)
        else:
            parser.print_help()
        return

    cmd_map = {
        "tui": cmd_tui,
        "list": cmd_list, "ls": cmd_list,
        "search": cmd_search, "find": cmd_search,
        "info": cmd_info, "show": cmd_info,
        "delete": cmd_delete, "rm": cmd_delete,
        "clean": cmd_clean,
        "stats": cmd_stats,
    }

    cmd_map[args.command](args)


if __name__ == "__main__":
    main()

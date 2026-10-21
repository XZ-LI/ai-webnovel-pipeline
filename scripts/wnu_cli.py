from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

from conf import BASE_DIR


# ---------------------------------------------------------------------------
# Path helpers (mirrors sau_cli.py resolve_account_file / resolve_runtime_home)
# ---------------------------------------------------------------------------

def resolve_account_file(account_name: str) -> Path:
    p = BASE_DIR / "cookies" / f"webnovel_{account_name}.json"
    p.parent.mkdir(exist_ok=True)
    return p


def resolve_state_file(account_name: str, novel_folder: Path) -> Path:
    resolved = novel_folder.resolve()
    # Use parent dir name when folder is generically named (e.g. "chapters")
    slug = resolved.parent.name if resolved.name == "chapters" else resolved.name
    p = BASE_DIR / "state" / f"{account_name}_{slug}.json"
    p.parent.mkdir(exist_ok=True)
    return p


def load_site_config(path: Path) -> dict:
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def existing_dir(value: str) -> Path:
    p = Path(value)
    if not p.is_dir():
        raise argparse.ArgumentTypeError(f"Directory not found: {value}")
    return p


def existing_file(value: str) -> Path:
    p = Path(value)
    if not p.is_file():
        raise argparse.ArgumentTypeError(f"File not found: {value}")
    return p


def clock_time(value: str) -> time:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Time must use HH:MM in 24-hour format") from exc
    return parsed.time()


def timezone_name(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise argparse.ArgumentTypeError(f"Unknown time zone: {value}") from exc


def parse_state_datetime(value: str | None, target_tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        # Older state files were written without an offset. Python treats a
        # naive astimezone() source as the machine's local time.
        dt = dt.astimezone()
    return dt.astimezone(target_tz)


def next_daily_run(
    last_submitted_at: str | None,
    *,
    schedule_tz: ZoneInfo,
    daily_time: time,
) -> datetime:
    now = datetime.now(schedule_tz)
    scheduled_today = now.replace(
        hour=daily_time.hour,
        minute=daily_time.minute,
        second=0,
        microsecond=0,
    )
    last_dt = parse_state_datetime(last_submitted_at, schedule_tz)

    if last_dt is None:
        return scheduled_today if now < scheduled_today else now

    if last_dt.date() >= now.date():
        return scheduled_today + timedelta(days=1)

    return scheduled_today if now < scheduled_today else now


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_login(args: argparse.Namespace) -> int:
    from uploader.webnovel_uploader.main import webnovel_login
    site_config = load_site_config(args.site_config)
    account_file = str(resolve_account_file(args.account))
    headless = not args.headed
    result = await webnovel_login(account_file, site_config, headless=headless)
    if result["success"]:
        print(f"Login successful. Session saved to: {result['account_file']}")
        return 0
    print(f"Login failed: {result['message']}", file=sys.stderr)
    return 1


async def cmd_check(args: argparse.Namespace) -> int:
    from uploader.webnovel_uploader.main import cookie_auth
    site_config = load_site_config(args.site_config)
    account_file = str(resolve_account_file(args.account))
    if not Path(account_file).exists():
        print("invalid — no session file found")
        return 1
    valid = await cookie_auth(account_file, site_config)
    print("valid" if valid else "invalid")
    return 0 if valid else 1


async def cmd_submit_next(args: argparse.Namespace) -> int:
    from uploader.webnovel_uploader.main import WebNovelChapter, webnovel_setup
    from uploader.webnovel_uploader.state import scan_chapter_files, load_state, mark_submitted

    site_config = load_site_config(args.site_config)
    account_file = str(resolve_account_file(args.account))
    novel_folder: Path = args.novel_folder
    state_file = resolve_state_file(args.account, novel_folder)
    headless = not args.headed

    if not await webnovel_setup(account_file, site_config, handle=False):
        print("Session invalid — run `wnu webnovel login` first.", file=sys.stderr)
        return 1

    chapters = scan_chapter_files(novel_folder)
    if not chapters:
        print("No .txt chapter files found in the novel folder.", file=sys.stderr)
        return 1

    state = load_state(state_file)
    idx = state["next_index"]

    if idx >= len(chapters):
        print(f"All {len(chapters)} chapter(s) have already been submitted.")
        return 0

    batch_size = getattr(args, "batch_size", 1)
    submitted = 0
    for _ in range(batch_size):
        state = load_state(state_file)
        idx = state["next_index"]
        if idx >= len(chapters):
            print("No more chapters to submit.")
            break

        chapter = chapters[idx]
        print(f"Submitting {chapter.name} ({idx + 1}/{len(chapters)})...")
        uploader = WebNovelChapter(chapter, site_config, account_file, headless=headless)
        next_url = await uploader.submit()
        mark_submitted(state_file, str(chapter), next_chapter_url=next_url)
        submitted += 1
        print(f"Done: {chapter.name}")
        if _ < batch_size - 1:
            await asyncio.sleep(5)

    print(f"Submitted {submitted} chapter(s).")
    return 0


async def cmd_status(args: argparse.Namespace) -> int:
    from uploader.webnovel_uploader.state import scan_chapter_files, load_state

    novel_folder: Path = args.novel_folder
    state_file = resolve_state_file(args.account, novel_folder)
    chapters = scan_chapter_files(novel_folder)
    state = load_state(state_file)
    idx = state["next_index"]
    last = state.get("last_submitted_at") or "never"

    print(f"Novel folder   : {novel_folder}")
    print(f"Total chapters : {len(chapters)}")
    print(f"Submitted      : {idx}")
    print(f"Remaining      : {max(0, len(chapters) - idx)}")
    print(f"Last submitted : {last}")

    if idx < len(chapters):
        print(f"Next up        : {chapters[idx].name}")
    else:
        print("Status         : all chapters submitted")

    return 0


async def cmd_daemon(args: argparse.Namespace) -> int:
    from uploader.webnovel_uploader.main import WebNovelChapter, webnovel_setup
    from uploader.webnovel_uploader.state import scan_chapter_files, load_state, mark_submitted

    site_config = load_site_config(args.site_config)
    account_file = str(resolve_account_file(args.account))
    novel_folder: Path = args.novel_folder
    state_file = resolve_state_file(args.account, novel_folder)
    interval_seconds = args.interval_hours * 3600
    headless = not args.headed
    schedule_tz = args.time_zone

    logger.info(
        f"Daemon started | account={args.account} | novel={novel_folder.name} | "
        f"schedule={args.schedule} | batch_size={args.batch_size}"
    )
    if args.schedule == "daily":
        logger.info(
            f"Daily batch time: {args.daily_time.strftime('%H:%M')} {schedule_tz.key}"
        )
    else:
        logger.info(f"Interval between batches: {args.interval_hours}h")

    while True:
        if not await webnovel_setup(account_file, site_config, handle=False):
            logger.error("Session invalid. Run `wnu webnovel login` then restart daemon. Retrying in 1 hour.")
            await asyncio.sleep(3600)
            continue

        chapters = scan_chapter_files(novel_folder)
        if not chapters:
            logger.warning("No chapter files found. Waiting 1 hour before re-scanning.")
            await asyncio.sleep(3600)
            continue

        state = load_state(state_file)
        idx = state["next_index"]

        if idx >= len(chapters):
            logger.info(f"All {len(chapters)} chapter(s) submitted. Daemon finished.")
            break

        # Decide whether to submit now or wait
        last_str = state.get("last_submitted_at")
        if args.schedule == "daily":
            next_at = next_daily_run(
                last_str,
                schedule_tz=schedule_tz,
                daily_time=args.daily_time,
            )
            now = datetime.now(schedule_tz)
            remaining = (next_at - now).total_seconds()
            next_at_text = f"{next_at.strftime('%Y-%m-%d %H:%M:%S')} {schedule_tz.key}"
        else:
            if last_str:
                last_dt = parse_state_datetime(last_str, datetime.now().astimezone().tzinfo)
                elapsed = (datetime.now().astimezone() - last_dt).total_seconds()
                remaining = interval_seconds - elapsed
            else:
                remaining = 0
            next_at = datetime.now().astimezone() + timedelta(seconds=max(0, remaining))
            next_at_text = next_at.strftime("%Y-%m-%d %H:%M:%S %Z")

        if remaining > 0:
            logger.info(
                f"Next submission at {next_at_text} "
                f"({remaining / 3600:.1f}h away). Sleeping..."
            )
            # Sleep in 1-hour chunks so macOS sleep/drift can't cause us to
            # miss the wake-up window entirely.
            while True:
                chunk = min(remaining, 3600)
                await asyncio.sleep(chunk)
                now = datetime.now(schedule_tz)
                remaining = (next_at - now).total_seconds()
                if remaining <= 0:
                    break
                logger.info(f"Still waiting for {next_at_text} ({remaining / 3600:.1f}h left)...")
            continue

        # Submit one batch (batch_size chapters)
        batch_size = args.batch_size
        submitted_this_batch = 0
        for _ in range(batch_size):
            state = load_state(state_file)
            idx = state["next_index"]
            if idx >= len(chapters):
                break

            chapter = chapters[idx]
            logger.info(f"Submitting {chapter.name} ({idx + 1}/{len(chapters)})...")
            try:
                uploader = WebNovelChapter(chapter, site_config, account_file, headless=headless)
                next_url = await uploader.submit()
                mark_submitted(state_file, str(chapter), next_chapter_url=next_url)
                submitted_this_batch += 1
                logger.info(f"{chapter.name} submitted.")
                if _ < batch_size - 1:
                    await asyncio.sleep(5)  # brief pause between chapters in same batch
            except Exception as exc:
                logger.error(f"Submission of {chapter.name} failed: {exc}. Stopping batch, will retry next cycle.")
                break

        if submitted_this_batch:
            if args.schedule == "daily":
                logger.info(f"Batch done ({submitted_this_batch} chapter(s)). Next batch tomorrow.")
            else:
                logger.info(f"Batch done ({submitted_this_batch} chapter(s)). Next batch in {args.interval_hours}h.")

    return 0


# ---------------------------------------------------------------------------
# CLI wiring (mirrors sau_cli.py build_parser / dispatch pattern)
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wnu",
        description="webnovel-auto-submit — schedule chapter uploads.",
    )
    platform_parsers = parser.add_subparsers(dest="platform", required=True)

    wn = platform_parsers.add_parser("webnovel", help="Webnovel operations")
    actions = wn.add_subparsers(dest="action", required=True)

    # --- login ---
    p_login = actions.add_parser("login", help="Log in and save session cookies")
    p_login.add_argument("--account", required=True, help="Account name (used as cookie file prefix)")
    p_login.add_argument("--site-config", required=True, type=existing_file, metavar="YAML",
                         help="Path to site config YAML")
    p_login.add_argument("--headed", action="store_true",
                         help="Show browser window (default for login; implied when no credentials in config)")

    # --- check ---
    p_check = actions.add_parser("check", help="Check if saved session is still valid")
    p_check.add_argument("--account", required=True)
    p_check.add_argument("--site-config", required=True, type=existing_file, metavar="YAML")

    # --- submit-next ---
    p_submit = actions.add_parser("submit-next", help="Submit the next pending chapter once")
    p_submit.add_argument("--account", required=True)
    p_submit.add_argument("--novel-folder", required=True, type=existing_dir, metavar="DIR",
                          help="Folder containing chapter .txt files")
    p_submit.add_argument("--site-config", required=True, type=existing_file, metavar="YAML")
    p_submit.add_argument("--batch-size", type=int, default=1, metavar="N",
                          help="Number of chapters to submit (default: 1)")
    p_submit.add_argument("--headed", action="store_true", help="Show browser window")

    # --- status ---
    p_status = actions.add_parser("status", help="Show submission progress for a novel")
    p_status.add_argument("--account", required=True)
    p_status.add_argument("--novel-folder", required=True, type=existing_dir, metavar="DIR")

    # --- daemon ---
    p_daemon = actions.add_parser("daemon", help="Run continuously, submitting scheduled chapter batches")
    p_daemon.add_argument("--account", required=True)
    p_daemon.add_argument("--novel-folder", required=True, type=existing_dir, metavar="DIR")
    p_daemon.add_argument("--site-config", required=True, type=existing_file, metavar="YAML")
    p_daemon.add_argument("--schedule", choices=("daily", "interval"), default="daily",
                          help="Scheduling mode (default: daily)")
    p_daemon.add_argument("--time-zone", type=timezone_name, default=ZoneInfo("Asia/Shanghai"),
                          metavar="TZ", help="Time zone for daily schedule (default: Asia/Shanghai)")
    p_daemon.add_argument("--daily-time", type=clock_time, default=time(0, 0), metavar="HH:MM",
                          help="Daily batch time in --time-zone (default: 00:00)")
    p_daemon.add_argument("--interval-hours", type=float, default=24.0, metavar="N",
                          help="Hours between each batch when --schedule interval is used (default: 24)")
    p_daemon.add_argument("--batch-size", type=int, default=2, metavar="N",
                          help="Chapters to submit per scheduled batch (default: 2)")
    p_daemon.add_argument("--headed", action="store_true", help="Show browser window")

    return parser


async def dispatch(args: argparse.Namespace) -> int:
    if args.platform == "webnovel":
        if args.action == "login":
            return await cmd_login(args)
        if args.action == "check":
            return await cmd_check(args)
        if args.action == "submit-next":
            return await cmd_submit_next(args)
        if args.action == "status":
            return await cmd_status(args)
        if args.action == "daemon":
            return await cmd_daemon(args)
        raise RuntimeError(f"Unknown action: {args.action}")
    raise RuntimeError(f"Unknown platform: {args.platform}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return asyncio.run(dispatch(args))
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
trigger_test.py — 测这个 skill 会不会在该用的时候被用上。

它跑的是真实判断：把每条提问交给一个全新的 Claude Code 会话（`claude -p`），
看那个会话有没有自己去加载 novel-pipeline。描述写不好，用户说"帮我写本小说"
它也不会读 SKILL.md，后面所有功能都用不上——所以这一类该先测。

**需要 claude CLI**：`npm install -g @anthropic-ai/claude-code`。没装就只能手动测，
把 evals/trigger_eval.json 里的问题一条条在新会话里问，看第一步是否读 SKILL.md。

花的是 Claude Code 的额度，不是 OpenAI 的钱。一轮 21 条、每条跑 1 次，几分钟。

用法：
  python3 trigger_test.py                      # 每条跑 1 次
  python3 trigger_test.py --repeat 3           # 每条跑 3 次，看稳定性
  python3 trigger_test.py --only-negative      # 只测不该触发的（最容易出问题的一类）
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
EVAL_FILE = SKILL / "evals" / "trigger_eval.json"
SKILL_NAME = "novel-pipeline"


def find_cli() -> str:
    for cand in ("claude", str(Path.home() / ".claude/local/claude")):
        p = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if p:
            return p
    return ""


def ran_skill(cli: str, query: str, timeout: int) -> tuple:
    """返回 (是否真的加载了本 skill, 备注)。

    判据是工具调用轨迹里有 Skill(novel-pipeline)，或者直接去读了 SKILL.md。
    不能靠在输出文本里 grep skill 名字——可用 skill 列表本来就注入在系统提示里，
    模型随口提一句名字不等于真的用了它。
    """
    try:
        r = subprocess.run(
            [cli, "-p", query, "--output-format", "stream-json", "--verbose",
             "--max-turns", "2"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "超时"
    if not (r.stdout or "").strip():
        return None, "无输出"

    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant":
            continue
        for c in ev.get("message", {}).get("content", []):
            if c.get("type") != "tool_use":
                continue
            blob = json.dumps(c.get("input", {}), ensure_ascii=False)
            if c["name"] == "Skill" and SKILL_NAME in blob:
                return True, "Skill 工具"
            if c["name"] in ("Read", "Bash", "Grep") and f"skills/{SKILL_NAME}" in blob:
                return True, "直接读 SKILL.md"
    return False, ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeat", type=int, default=1, help="每条问题跑几次")
    ap.add_argument("--only-negative", action="store_true", help="只测不该触发的")
    ap.add_argument("--only-positive", action="store_true", help="只测该触发的")
    ap.add_argument("--timeout", type=int, default=180, help="单条超时秒数")
    args = ap.parse_args()

    cli = find_cli()
    if not cli:
        print("[ERROR] 找不到 claude CLI，自动化触发测试跑不了。两条出路：")
        print("  1) 装上：npm install -g @anthropic-ai/claude-code，然后重跑这个脚本")
        print("  2) 手动测：把下面的问题一条条在**新会话**里问，看它第一步是否读 SKILL.md")
        print()
        data = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
        for q in data["queries"]:
            mark = "该触发  " if q["should_trigger"] else "不该触发"
            print(f"  [{mark}] {q['query']}")
        return 1

    data = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
    queries = data["queries"]
    if args.only_negative:
        queries = [q for q in queries if not q["should_trigger"]]
    if args.only_positive:
        queries = [q for q in queries if q["should_trigger"]]

    rows, miss, false_fire = [], [], []
    for i, q in enumerate(queries, 1):
        hits, notes = 0, []
        for _ in range(args.repeat):
            got, note = ran_skill(cli, q["query"], args.timeout)
            if got is None:
                notes.append(note)
            elif got:
                hits += 1
        rate = hits / max(1, args.repeat)
        ok = (rate >= 0.5) == q["should_trigger"]
        rows.append((q, rate, ok, notes))
        if not ok:
            (miss if q["should_trigger"] else false_fire).append(q["query"])
        flag = "✓" if ok else "✗"
        want = "该触发" if q["should_trigger"] else "不该触发"
        extra = f"  [{','.join(notes)}]" if notes else ""
        print(f"{flag} [{want}] 触发率 {rate:.0%}  {q['query'][:46]}{extra}")

    print()
    print(f"通过 {sum(1 for *_, ok, _ in rows if ok)}/{len(rows)}")
    if miss:
        print(f"\n该触发却没触发（{len(miss)} 条）—— 描述缺少这些说法的覆盖：")
        for m in miss:
            print(f"  - {m}")
    if false_fire:
        print(f"\n不该触发却触发了（{len(false_fire)} 条）—— 描述太宽，会在不相干任务里插话：")
        for f in false_fire:
            print(f"  - {f}")
    if not miss and not false_fire:
        print("\n描述的覆盖和边界都没问题。")
    return 0 if not (miss or false_fire) else 1


if __name__ == "__main__":
    sys.exit(main())

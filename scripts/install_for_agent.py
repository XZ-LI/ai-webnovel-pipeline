#!/usr/bin/env python3
"""
install_for_agent.py — 给各家 AI 编码 agent 生成入口文件。

这条流水线的本体是 scripts/ 下的 Python 脚本和 assets/ 下的题材模版，不依赖任何
agent。各家 agent 的差别只在"去哪里读项目指令"：Claude Code 读 skill 目录里的
SKILL.md，Codex 和一批工具读 AGENTS.md，Cursor 读 .cursor/rules/*.mdc，
Cline 读 .clinerules，Copilot 读 .github/copilot-instructions.md……

所以这里只做一件事：按目标 agent 的格式写一个薄入口，指回 AGENTS.md。
真正的内容永远只有一份，不会分叉。

用法：
  python3 install_for_agent.py --list
  python3 install_for_agent.py --agent cursor            # 写进当前目录
  python3 install_for_agent.py --agent codex  --dest ~/my-novel
  python3 install_for_agent.py --agent all   --dest ~/my-novel
"""

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent

AGENTS = {
    "codex": {
        "name": "Codex CLI / 以及任何读 AGENTS.md 的工具",
        "path": "AGENTS.md",
        "fmt": "plain",
    },
    "cursor": {
        "name": "Cursor",
        "path": ".cursor/rules/novel-pipeline.mdc",
        "fmt": "mdc",
    },
    "cline": {
        "name": "Cline",
        "path": ".clinerules",
        "fmt": "plain",
    },
    "copilot": {
        "name": "GitHub Copilot",
        "path": ".github/copilot-instructions.md",
        "fmt": "plain",
    },
    "windsurf": {
        "name": "Windsurf",
        "path": ".windsurfrules",
        "fmt": "plain",
    },
    "aider": {
        "name": "Aider",
        "path": "CONVENTIONS.md",
        "fmt": "plain",
    },
}

BODY = """# 写长篇网文：请先读 {guide}

这个工作目录在用一套**分层记忆**的流水线写中文长篇网络小说。动手之前读
`{guide}`，它是权威指南；细节在 `{refs}` 下按需读，不要一次全读。

脚本在 `{scripts}`，全是 Python 标准库加 `openai`，命令行直接可跑：

- `gen_premise.py pitch` / `expand` —— 立项：先出几个方案让作者挑，再展开成开篇
- `write_novel.py init` / `write` / `status` / `new-arc` —— 建项目、续写、看进度、换弧
- `review_chapter.py diagnose` / `revise` / `restore` —— 章节诊断、按作者选定的问题改稿
- `setup_api.py set` —— 配作者自己的 API（六家预设，也接受任何 OpenAI 兼容接口）
- `selftest.py` —— 113 项回归自检，不调 API

## 四条不能违反的纪律

1. **立项后先把 `bible.json` 的 `hard_constraints` 念给作者听**。那是"违反即整章作废"
   的红线（主角的生理限制、金手指代价、谁已经死了）。实测过的事故：主角设定是哑巴，
   这条没进红线，第三章他就开口说了四次话。
2. **大纲类的东西永远给多个版本**。立项方案和每 30 章的弧线计划都支持一次出几个；
   作者挑的过程本身会暴露他真正想要什么，比你猜准得多。
3. **诊断可以随便跑，改稿要作者点名**。不要自己 `--fix all`，那会把写得好的段落
   一起换掉。改稿会自动备份原稿并同步重写该章摘要。
4. **一章要回退就连摘要一起退**。只删正文不删 `summaries.jsonl` 对应行，错误剧情会
   作为既成事实进入后面所有章节。

## 不要替作者决定的事

人物命运、结局走向、剧情大方向是作者的。你负责在既定框架内把每章写好，以及在框架
开始自我矛盾时提醒他。也不要替他猜 API——问他用哪家、有没有 key，再跑 `setup_api.py`。
"""


def render(agent_key: str, guide_rel: str, scripts_rel: str, refs_rel: str) -> str:
    body = BODY.format(guide=guide_rel, scripts=scripts_rel, refs=refs_rel)
    if AGENTS[agent_key]["fmt"] == "mdc":
        # Cursor 的 .mdc 需要 frontmatter；alwaysApply=false 让它按描述按需加载
        return (
            "---\n"
            "description: 用分层记忆写中文长篇网络小说——立项、续写、诊断改稿、发章。"
            "作者提到写小说、续写、下一章、大纲、改稿、发章时适用。\n"
            "alwaysApply: false\n"
            "---\n\n" + body
        )
    return body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", help="目标 agent，或 all")
    ap.add_argument("--dest", type=Path, default=Path.cwd(), help="写到哪个目录，默认当前目录")
    ap.add_argument("--list", action="store_true", help="列出支持的 agent")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的入口文件")
    args = ap.parse_args()

    if args.list or not args.agent:
        print("支持的 agent（入口文件的位置是各家自己的约定）：\n")
        for k, v in AGENTS.items():
            print(f"  {k:<10} {v['name']}")
            print(f"             → {v['path']}")
        print("\n这条流水线不依赖任何 agent，不用 agent 直接跑命令也行（见 README.md）。")
        print("用法：python3 install_for_agent.py --agent cursor --dest ~/my-novel")
        return 0

    keys = list(AGENTS) if args.agent == "all" else [args.agent]
    bad = [k for k in keys if k not in AGENTS]
    if bad:
        print(f"[ERROR] 不认识的 agent：{bad}。可用：{', '.join(AGENTS)}")
        return 1

    dest = args.dest.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    guide = SKILL_DIR / "AGENTS.md"
    if not guide.exists():
        print(f"[ERROR] 找不到权威指南 {guide}")
        return 1

    for k in keys:
        out = dest / AGENTS[k]["path"]
        if out.exists() and not args.force:
            print(f"  跳过 {out}（已存在，加 --force 覆盖）")
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        # 入口文件里用绝对路径，这样作者在任何工作目录下 agent 都找得到
        out.write_text(render(k, str(guide), str(SKILL_DIR / "scripts"),
                              str(SKILL_DIR / "references")), encoding="utf-8")
        print(f"  ✓ {AGENTS[k]['name']:<40} → {out}")

    print(f"\n入口文件只是指回 {guide}，内容永远只有一份，不会分叉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

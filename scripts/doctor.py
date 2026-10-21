#!/usr/bin/env python3
"""
doctor.py — 一条命令查完所有前置条件，并告诉你（或你的 agent）下一步做什么。

写给**完全不懂 Python、也没配过 API key 的人**。它不假设你知道什么叫包管理器、
什么叫环境变量，每一项查完都直接给出照着做就行的下一步。

这个脚本自己不依赖任何第三方包——否则"没装 openai"这件事它自己就报不出来了。

用法：
  python3 doctor.py              # 只诊断
  python3 doctor.py --fix        # 能自动修的就修（目前只有装 openai 这一项）
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
OK, WARN, BAD = "✓", "!", "✗"
rows = []       # (状态, 标题, 说明, 下一步)


def say(status, title, detail="", nxt=""):
    rows.append((status, title, detail, nxt))


def py_cmd() -> str:
    """这台机器上该用哪个命令跑 Python。Windows 常常只有 py 或 python。"""
    for c in ("python3", "python", "py"):
        if shutil.which(c):
            return c
    return "python3"


def check_python():
    v = sys.version_info
    if v < (3, 9):
        say(BAD, f"Python 版本 {v.major}.{v.minor} 太旧",
            "这套脚本需要 3.9 或更新。",
            "装一个新的 Python：https://www.python.org/downloads/ ，装完重开终端再跑一次。")
    else:
        say(OK, f"Python {v.major}.{v.minor}.{v.micro}", f"命令是 {py_cmd()}")


def check_openai(fix: bool):
    try:
        import openai  # noqa: F401
        say(OK, "openai 包已安装", getattr(openai, "__version__", ""))
        return True
    except ImportError:
        pass

    cmd = [sys.executable, "-m", "pip", "install", "openai"]
    if not fix:
        say(BAD, "缺 openai 包", "这是唯一必须额外安装的东西（写作本身要用它调接口）。",
            f"跑这条命令装上：{' '.join(cmd)}\n"
            f"       —— 或者直接跟你的 agent 说「帮我装 openai」，它可以替你跑。")
        return False

    print("  正在安装 openai ...", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        say(OK, "openai 包已安装", "刚刚装好的")
        return True
    tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
    say(BAD, "装 openai 失败", "\n       ".join(tail),
        f"把上面的报错发给你的 agent，或手动跑：{' '.join(cmd)}")
    return False


def check_key():
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    try:
        import novel_config as nc
    except Exception as e:
        say(BAD, "读不到配置模块", str(e), "确认 skill 目录完整，scripts/ 下应有 novel_config.py")
        return False

    if nc.api_key():
        say(OK, "API key 已配置", nc.describe())
        return True

    say(BAD, "还没有 API key",
        "模型调用花的是你自己的钱，所以要有一个自己的 key。默认用 DeepSeek——"
        "大陆直连、写中文长篇便宜。",
        "三步，五分钟：\n"
        "       1. 打开 https://platform.deepseek.com/ 注册（手机号就行）\n"
        "       2. 充值（写十来章大概几块钱，先充最小额度即可）\n"
        "       3. 在「API keys」页面创建一个 key，复制那串 sk- 开头的字符\n"
        "       然后把 key 粘给你的 agent，说「帮我配上」；或者自己跑：\n"
        f"       {py_cmd()} {SKILL_DIR / 'scripts' / 'setup_api.py'} set --provider deepseek --key 你复制的那串\n"
        "       已经有别家的 key（OpenAI、通义、Kimi、智谱）也行，把 deepseek 换成对应的名字。")
    return False


def check_connection():
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    import novel_config as nc
    try:
        client = nc.make_client()
        r = client.chat.completions.create(
            model=nc.model(), messages=[{"role": "user", "content": "回答两个字：收到"}])
        say(OK, "接口能连通", f"模型 {nc.model()} 回了「{(r.choices[0].message.content or '').strip()[:10]}」")
        return True
    except SystemExit:
        return False
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if "model" in low and ("not found" in low or "does not exist" in low):
            nxt = ("模型名和服务商对不上。看这家有哪些模型：\n"
                   f"       {py_cmd()} {SKILL_DIR / 'scripts' / 'setup_api.py'} list")
        elif any(k in low for k in ("auth", "401", "api key", "unauthorized")):
            nxt = "key 不对或没复制全。回服务商后台重新复制一次，再跑 setup_api.py set --key ..."
        elif any(k in low for k in ("connect", "timeout", "resolve", "ssl")):
            nxt = "连不上这个地址。确认网络；如果用的是境外服务商（OpenAI），在中国大陆需要自备网络条件。"
        elif "quota" in low or "balance" in low or "余额" in msg:
            nxt = "账户余额不足，去服务商后台充值。"
        else:
            nxt = "把这段报错发给你的 agent，让它帮你判断。"
        say(BAD, "接口连不通", msg[:160], nxt)
        return False


def check_workdir():
    cwd = Path.cwd()
    if cwd == SKILL_DIR or SKILL_DIR in cwd.parents:
        say(WARN, "当前目录在 skill 安装目录里", str(cwd),
            "换到一个专门放小说的目录再写，否则你的书会和程序混在一起：\n"
            "       mkdir -p ~/my-novel && cd ~/my-novel")
        return
    novels = cwd / "novels"
    if novels.is_dir():
        books = [d.name for d in novels.iterdir() if d.is_dir()]
        say(OK, f"这个目录里已有 {len(books)} 本书", "、".join(books[:4]))
    else:
        say(OK, "工作目录可用", f"{cwd}（还没有书，接下来建的第一本会放在这里）")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true", help="能自动修的就修（目前只有装 openai）")
    args = ap.parse_args()

    print("检查写作环境 ...\n")
    check_python()
    has_pkg = check_openai(args.fix)
    has_key = check_key() if has_pkg else False
    if has_key:
        check_connection()
    check_workdir()

    width = max(len(t) for _, t, _, _ in rows) + 2
    for st, title, detail, nxt in rows:
        print(f"  {st} {title:<{width}}{detail}")
        if nxt:
            for i, line in enumerate(nxt.splitlines()):
                print(("    → " if i == 0 else "      ") + line.lstrip())
    bad = [r for r in rows if r[0] == BAD]
    print()
    if bad:
        print(f"还差 {len(bad)} 项。按上面的 → 逐条做完，再跑一次这个命令。")
        print("不想自己弄就把这段输出整个发给你的 agent，让它替你做——"
              "除了注册和充值必须你本人来，其余它都能代劳。")
        return 1
    print("都齐了。接下来跟你的 agent 说一句「我想写本小说」就能开始。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

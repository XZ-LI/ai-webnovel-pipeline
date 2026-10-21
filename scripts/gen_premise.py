#!/usr/bin/env python3
"""
gen_premise.py — 立项阶段：先出几个方案让作者挑，再把选中的那个展开成开篇文件。

为什么分两步：一次写满三份"大纲 + 第一章"，作者看完要扔掉两份，钱白花。
所以先用一次便宜的调用产出 N 个**方案摘要**（卖点、主角、金手指、开局、核心矛盾、
与同类套路的差异），作者挑一个（或说"再来几个"），再把它展开成完整开篇。

作者手上有多少东西都能进来：
  完全从零   不传 --seed，方案全部由模型提
  有点思路   --seed "重生成反派的管家，想保住小姐这条命"
  已有设定   --seed-file 我的设定.txt   （已定下的部分不会被改掉）

用法：
  python3 gen_premise.py pitch  --genre 西方奇幻 [--variants 3] [--seed ... | --seed-file ...]
  python3 gen_premise.py pitch  --genre 女频/快穿 --variants 4 --more <已有目录>   # 再来几个
  python3 gen_premise.py expand --dir premises/<目录> --pick 2
  python3 gen_premise.py show   --dir premises/<目录>

展开产出的文件可直接喂给 write_novel.py：
  python3 write_novel.py init --from premises/<目录>/variant_2_premise.txt
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from openai import OpenAI
except ImportError:
    print("[ERROR] openai 未安装。运行：pip install openai")
    sys.exit(1)

from write_novel import call, load_genre_layer  # noqa: E402  复用同一套调用与题材解析
import novel_config as _cfg

DEFAULT_MODEL = _cfg.model()
OUT_BASE = Path("premises")
PITCH_FIELDS = ["书名", "一句话卖点", "主角与处境", "金手指", "核心矛盾",
                "开局场景", "与同类的差异", "前十章节奏", "沿用与补充"]


def strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)


def read_seed(args) -> str:
    if args.seed_file:
        p = Path(args.seed_file)
        if not p.exists():
            print(f"[ERROR] 设定文件不存在：{p}")
            sys.exit(1)
        return p.read_text(encoding="utf-8").strip()
    return (args.seed or "").strip()


def build_pitch_prompt(genre: str, genre_rules: str, seed: str, n: int, avoid: list) -> str:
    if seed:
        seed_block = (
            "## 作者已经定下来的东西（必须沿用，不得改动）\n"
            f"{seed}\n\n"
            "凡是作者已经定了的（人物、设定、开局、禁忌），每个方案都要原样保留；"
            "只在他没定的地方发挥。每个方案的「沿用与补充」字段里写清：沿用了他哪几条、你补了哪几条。"
        )
    else:
        seed_block = (
            "## 作者没有给任何设定\n"
            "方案完全由你提。不要全部押在同一种套路上——这几个方案要让作者看出"
            "明显不同的选择，而不是同一个故事的三种措辞。\n"
            "每个方案的「沿用与补充」字段写「作者未给设定，全部由方案提出」。"
        )

    avoid_block = ""
    if avoid:
        avoid_block = (
            "\n## 已经给过作者的方案（这一批必须与它们明显不同）\n"
            + "\n".join(f"- {a}" for a in avoid) + "\n"
        )

    return f"""你在给一本《{genre}》题材的中文网络小说做立项，要一次提出 {n} 个**互相明显不同**的方案。

{seed_block}
{avoid_block}
## 本题材的规则与读者预期
{genre_rules}

## 硬要求
1. {n} 个方案必须在**金手指**和**核心矛盾**上互不相同——不是换个名字，是换一种故事引擎。
2. 每个方案都要兑现本题材读者来看的东西，不要因为求新而把读者预期丢掉。
3. 「一句话卖点」要像平台推荐位上的那一句：具体、有反差、让人想点进去。不要写"一个关于成长与救赎的故事"这种。
4. 「前十章节奏」写成 10 个短句，一章一句，看得出钩子落在哪几章。句子里不要自带「1.」「第3章」这类编号。
5. 「与同类的差异」要说清这本和同题材常见写法的区别在哪一条，以及为什么这个区别对读者有吸引力。

## 输出格式
只输出 JSON 数组，不要任何解释文字。每个元素含这些键：
{json.dumps(PITCH_FIELDS, ensure_ascii=False)}
其中「前十章节奏」是长度为 10 的字符串数组，其余都是字符串。
"""


def build_expand_prompt(genre: str, genre_rules: str, pitch: dict, seed: str) -> str:
    seed_block = f"\n## 作者已定下的设定（必须沿用）\n{seed}\n" if seed else ""
    return f"""把下面这个《{genre}》题材的立项方案展开成完整开篇。
{seed_block}
## 选中的方案
{json.dumps(pitch, ensure_ascii=False, indent=1)}

## 本题材的规则与读者预期
{genre_rules}

## 输出格式（严格照抄这个结构，解析器依赖它）

【书本名称】《书名》

【目标读者】男频 或 女频

【作品标签】标签1|标签2|标签3|标签4

【主角名1】姓名

【主角名2】姓名

【作品简介】150-250 字，平台简介的写法：身份、开局处境、核心看点、最大的悬念。

1. **核心设定** — 一句话说清世界规则与主角的根本困境。

2. **主角档案**
   - 基本信息：姓名、出身、初始身份、底牌。
   - **外形描写**：发色瞳色、体型、常穿什么、随身物件（要具体，后面几百章会反复引用）。
   - **性格成长弧（必填）**：起点性格 / 成长触发 / 终点性格 / 成长节奏（1-30章、31-60章、61-100章各一句）。

3. **感情线设计** — 每位恋人：姓名、【外形】、性格、登场章、退场或转化时机、感情基调。没有感情线就写"本书无感情线"。

4. **大环境迁移与考验** — 三个环境，各写：地点、核心考验类型、主角在此获得什么、离开的转折事件。

5. **反派/核心矛盾** — 反派是谁、为什么非和主角对上、他手里有什么牌。

6. **章节大纲** — 第1章到第10章，每章一行：`   - 第N章：标题 — 一句话剧情。`

开篇围绕：第一章的钩子，以及这本书怎么和同类拉开差距。

========================================

第1章 第一章的标题

（第一章正文，1500 字以上。首行的章节标题必须严格写成"第1章 标题"：行首是"第"，
数字后紧跟"章"，再一个空格，然后标题。正文要落实本题材规则里的段落、节奏、
对话、章末悬念要求，结尾留钩子。）
"""


def cmd_pitch(client, args) -> int:
    genre_rules = load_genre_layer(args.genre)
    seed = read_seed(args)

    avoid, out_dir = [], None
    if args.more:
        out_dir = Path(args.more)
        prev = json.loads((out_dir / "pitches.json").read_text(encoding="utf-8"))
        seed = seed or prev.get("seed", "")
        avoid = [f"{p.get('书名','')}：{p.get('一句话卖点','')}" for p in prev["pitches"]]
        print(f"在已有目录上追加方案：{out_dir}（已有 {len(prev['pitches'])} 个）")
    if out_dir is None:
        tag = re.sub(r"[^\w一-鿿]+", "_", args.genre).strip("_")
        out_dir = OUT_BASE / f"{tag}_{datetime.now():%Y%m%d_%H%M}"
        out_dir.mkdir(parents=True, exist_ok=True)

    mode = "完整设定" if args.seed_file else ("思路片段" if seed else "完全从零")
    print(f"题材：{args.genre}  入口：{mode}  方案数：{args.variants}")
    print("正在生成方案...", end=" ", flush=True)

    raw = call(client, args.model,
               system="你是中文网络小说的资深策划，擅长一次给出几个互相拉开距离的立项方案。只输出 JSON。",
               user=build_pitch_prompt(args.genre, genre_rules, seed, args.variants, avoid),
               temp=0.95)
    try:
        new_pitches = json.loads(strip_fences(raw))
        assert isinstance(new_pitches, list) and new_pitches
    except Exception:
        (out_dir / "pitches_raw.txt").write_text(raw, encoding="utf-8")
        print(f"\n[ERROR] 返回的不是可解析的 JSON，原文已存到 {out_dir}/pitches_raw.txt")
        return 1
    print("完成")

    all_pitches = (json.loads((out_dir / "pitches.json").read_text(encoding="utf-8"))["pitches"]
                   if args.more else [])
    all_pitches += new_pitches
    (out_dir / "pitches.json").write_text(json.dumps(
        {"genre": args.genre, "seed": seed, "entry_mode": mode, "pitches": all_pitches},
        ensure_ascii=False, indent=1), encoding="utf-8")
    render_pitches(out_dir, args.genre, mode, all_pitches)

    print(f"\n{len(all_pitches)} 个方案 → {out_dir}/pitches.md")
    print(f"挑中第 N 个后展开：python3 gen_premise.py expand --dir {out_dir} --pick N")
    print(f"都不满意再来几个：python3 gen_premise.py pitch --genre \"{args.genre}\" --more {out_dir}")
    return 0


def render_pitches(out_dir: Path, genre: str, mode: str, pitches: list) -> None:
    lines = [f"# 《{genre}》立项方案（{len(pitches)} 个）", "",
             f"入口：{mode}。挑一个展开，或要求再来几个。", ""]
    for i, p in enumerate(pitches, 1):
        lines += [f"## 方案 {i}：{p.get('书名', '(未命名)')}", "",
                  f"**卖点**：{p.get('一句话卖点', '')}", ""]
        for k in ["主角与处境", "金手指", "核心矛盾", "开局场景", "与同类的差异", "沿用与补充"]:
            if p.get(k):
                lines.append(f"- **{k}**：{p[k]}")
        pace = p.get("前十章节奏") or []
        if pace:
            lines += ["", "前十章：", ""]
            # 模型常自带「3.」「第3章」这类前缀，剥掉再编号，免得出现 "3. 3.…"
            for j, s in enumerate(pace, 1):
                s = re.sub(r"^\s*(?:第\s*\d+\s*章?[：:.、]?|\d+\s*[.、:：)]）?)\s*", "", str(s))
                lines.append(f"{j}. {s}")
        lines.append("")
    (out_dir / "pitches.md").write_text("\n".join(lines), encoding="utf-8")


def cmd_expand(client, args) -> int:
    out_dir = Path(args.dir)
    data = json.loads((out_dir / "pitches.json").read_text(encoding="utf-8"))
    pitches = data["pitches"]
    if not 1 <= args.pick <= len(pitches):
        print(f"[ERROR] --pick 要在 1..{len(pitches)} 之间（这个目录有 {len(pitches)} 个方案）")
        return 1
    pitch = pitches[args.pick - 1]
    genre = args.genre or data["genre"]
    genre_rules = load_genre_layer(genre)

    print(f"展开方案 {args.pick}：{pitch.get('书名', '')}")
    print("正在写大纲与第一章...", end=" ", flush=True)
    text = call(client, args.model,
                system="你是专业的中文网络小说作家，严格按给定结构输出，不写任何解释文字。",
                user=build_expand_prompt(genre, genre_rules, pitch, data.get("seed", "")),
                temp=0.9)
    print("完成")

    out = out_dir / f"variant_{args.pick}_premise.txt"
    out.write_text(text.strip() + "\n", encoding="utf-8")

    problems = []
    if not re.search(r"【书本名称】\s*\S", text):
        problems.append("缺【书本名称】行 —— init 会拿目录名当书名")
    if "=" * 40 not in text:
        problems.append("缺 40 个 = 的分隔线 —— 第一章正文不会被识别")
    body = text.split("=" * 40, 1)[1] if "=" * 40 in text else ""
    if len(body.strip()) < 500:
        problems.append(f"第一章正文只有 {len(body.strip())} 字，不足 500 字 —— 会被当成大纲忽略")

    print(f"\n开篇文件 → {out}")
    if problems:
        print("[警告] 格式有问题，建议人工改一下再 init：")
        for p in problems:
            print(f"        - {p}")
    else:
        print("格式自检通过。")
    print(f"\n下一步：python3 write_novel.py init --from {out}")
    return 0


def cmd_show(args) -> int:
    out_dir = Path(args.dir)
    md = out_dir / "pitches.md"
    if not md.exists():
        print(f"[ERROR] {md} 不存在")
        return 1
    print(md.read_text(encoding="utf-8"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pitch", help="生成 N 个立项方案")
    p.add_argument("--genre", "-g", required=True, help="题材，如 西方奇幻 或 女频/快穿")
    p.add_argument("--variants", "-n", type=int, default=3, help="方案个数（默认 3）")
    p.add_argument("--seed", help="作者的思路片段（一两句话也行）")
    p.add_argument("--seed-file", help="作者已有的设定文件")
    p.add_argument("--more", help="在这个已有目录上再追加几个方案")
    p.add_argument("--model", "-m", default=DEFAULT_MODEL)

    e = sub.add_parser("expand", help="把选中的方案展开成开篇文件")
    e.add_argument("--dir", "-d", required=True, help="pitch 产出的目录")
    e.add_argument("--pick", "-p", type=int, required=True, help="选第几个方案（从 1 开始）")
    e.add_argument("--genre", "-g", help="覆盖题材（默认用 pitch 时记下的）")
    e.add_argument("--model", "-m", default=DEFAULT_MODEL)

    s = sub.add_parser("show", help="重新打印某个目录里的方案")
    s.add_argument("--dir", "-d", required=True)

    args = ap.parse_args()
    if args.command == "show":
        return cmd_show(args)

    client = _cfg.make_client()
    print(f"[接口] {_cfg.describe()}")
    return cmd_pitch(client, args) if args.command == "pitch" else cmd_expand(client, args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
review_chapter.py — 章节诊断与改稿。诊断不改稿，改稿要你点头。

分两条命令是故意的：诊断便宜、改稿贵且会覆盖正文，中间必须有人决定改哪几条。
自动按"全部问题都修"去重写，往往会把本来写得好的段落一起换掉。

诊断做两层：
  机械层（不花钱）  字数越界、写作技巧标签残留、章节标题格式、超长段落、
                   与上一章开篇手法重复、AI 味套话（只查叙述，对话里不算）
  语义层（一次调用）按题材模版的质量自检表逐项打分，并逐条核对 bible.json 的
                   hard_constraints，每个问题都要给出原文证据

改稿时会：备份原稿到 chapters/.bak/、只针对指定问题改写、保持既成情节事实不变、
         **同步重新生成该章摘要**（正文改了摘要不改，后面每章都会继承错的事实）。

用法：
  python3 review_chapter.py diagnose --project "<书名>" --chapter 3 [--genre 西方奇幻]
  python3 review_chapter.py diagnose --project "<书名>" --last 3        # 最近三章
  python3 review_chapter.py diagnose --project "<书名>" --chapter 3 --mechanical-only
  python3 review_chapter.py revise   --project "<书名>" --chapter 3 --fix 1,4
  python3 review_chapter.py revise   --project "<书名>" --chapter 3 --fix all
  python3 review_chapter.py restore  --project "<书名>" --chapter 3     # 退回改稿前
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import novel_config as _cfg  # noqa: E402
from write_novel import (  # noqa: E402
    CHAPTER_MAX_CHARS,
    CHAPTER_MIN_CHARS,
    CHAPTER_TARGET_CHARS,
    SUMMARY_MAX_CHARS,
    build_compress_prompt,
    call,
    find_project,
    format_bible,
    load_chapter,
    load_genre_layer,
    load_json,
    save_chapter,
    strip_technique_labels,
    find_ai_cliches,
)

SKILL_DIR = Path(__file__).resolve().parent.parent
MAX_PARA_CHARS = 180   # 手机屏上一段超过这个长度就开始赶人


# ── 题材自检表 ────────────────────────────────────────────────────────────────

def load_rubric(genre: str) -> list:
    """从 assets/templates/<性别>/<题材>.md 的"质量自检表"里取维度。"""
    rel = genre.strip("/")
    base = SKILL_DIR / "assets" / "templates"
    cands = [base / f"{rel}.md"] if "/" in rel else sorted(base.glob(f"*/{rel}.md"))
    for path in cands:
        if not path.exists():
            continue
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|$", line)
            if m:
                rows.append({"name": m.group(1), "weight": int(m.group(2)), "desc": m.group(3)})
        if rows:
            return rows
    return []


# ── 机械检查 ──────────────────────────────────────────────────────────────────

def mechanical_checks(text: str, chapter_n: int, prev_text: str = "") -> list:
    """确定性的检查。不花钱，也不会看错。"""
    issues = []
    body = text.strip()
    lines = [l for l in body.splitlines() if l.strip()]

    n_chars = len(body)
    if n_chars < CHAPTER_MIN_CHARS or n_chars > CHAPTER_MAX_CHARS:
        issues.append({
            "kind": "字数越界",
            "detail": f"本章 {n_chars} 字，目标区间 {CHAPTER_MIN_CHARS}-{CHAPTER_MAX_CHARS}"
                      f"（{CHAPTER_TARGET_CHARS} 字上下）",
            "evidence": "",
            "advice": "偏短就把一个场景写深，别硬塞情节；偏长就把后半段留给下一章",
        })

    if lines:
        head = lines[0].strip()
        if not re.match(rf"^第\s*{chapter_n}\s*章[ \t　]", head):
            issues.append({
                "kind": "标题格式",
                "detail": f"首行应为「第{chapter_n}章 标题」（数字后紧跟「章」再一个空格）",
                "evidence": head[:60],
                "advice": "发布端按首行取章节名，格式不对线上会缺标题",
            })

    cleaned = strip_technique_labels(body)
    if cleaned != body:
        leaked = [l.strip()[:50] for l in body.splitlines()
                  if l.strip() and strip_technique_labels(l) != l]
        issues.append({
            "kind": "技巧标签残留",
            "detail": f"{len(leaked)} 行把写作技巧标签写进了正文",
            "evidence": " / ".join(leaked[:3]),
            "advice": "读者会当成坏排版。改稿会自动剥掉，也可以直接跑 restore 之外的清洗",
        })

    long_paras = [l.strip()[:40] for l in lines[1:] if len(l.strip()) > MAX_PARA_CHARS]
    if long_paras:
        issues.append({
            "kind": "段落过长",
            "detail": f"{len(long_paras)} 段超过 {MAX_PARA_CHARS} 字",
            "evidence": " / ".join(f"{p}…" for p in long_paras[:2]),
            "advice": "网文在手机上读，一屏放不下三行就会掉读者；长段拆成 2-4 句一段",
        })

    if prev_text:
        def opener(t):
            ls = [l for l in t.strip().splitlines() if l.strip()]
            return ls[1][:14] if len(ls) > 1 else ""

        def style(t):
            """只认有把握的三类；认不准就返回空，宁可不报也不要误报。"""
            ls = [l for l in t.strip().splitlines() if l.strip()]
            first = ls[1] if len(ls) > 1 else ""
            if first[:1] in "「“\"'":
                return "对话开篇"
            if any(k in first for k in ("心想", "脑海", "回忆", "想起", "忽然明白")):
                return "内心独白开篇"
            if any(k in first for k in ("挥", "剑", "拳", "扑", "冲", "斩", "踹", "拔", "砸", "撞")):
                return "动作开篇"
            return ""

        if style(body) and style(prev_text) == style(body):
            issues.append({
                "kind": "开篇手法重复",
                "detail": f"与上一章同为「{style(body)}」",
                "evidence": f"上章：{opener(prev_text)}… / 本章：{opener(body)}…",
                "advice": "连续同样的切入方式会让读者觉得每章都一样",
            })

    # AI 味。读者说"一股 AI 味"时指的通常不是文笔差，是叙述者在替他总结感受。
    # 只查叙述：人物嘴里说套话是人物特征，叙述者说才是毛病。词表在 assets/ai_cliches.txt。
    cliches = find_ai_cliches(body)
    if cliches:
        by_cat = {}
        for word, cat, advice, n, hard in cliches:
            by_cat.setdefault((cat, advice), []).append(
                f"{word}{'×' + str(n) if n > 1 else ''}")
        for (cat, advice), words in by_cat.items():
            issues.append({
                "kind": f"AI 味·{cat}",
                "detail": f"{len(words)} 处：" + "、".join(words),
                "evidence": "",
                "advice": advice or "换成具体的动作或细节，别让叙述者替读者总结",
            })
    return issues


# ── 语义检查 ──────────────────────────────────────────────────────────────────

def build_diagnose_prompt(text: str, chapter_n: int, bible: dict,
                          rubric: list, hard: list, ch_type: str) -> str:
    rubric_lines = "\n".join(
        f"- {r['name']}（权重{r['weight']}）：{r['desc']}" for r in rubric
    ) or "- 钩子强度、人物鲜活度、悬念设计、世界观融合、节奏变化、画面感、对话自然度、情感张力、章节差异度、外形描写"
    hard_lines = "\n".join(f"- {h}" for h in hard) or "（本书未设定硬约束）"

    return f"""你在给一本中文网络小说的第{chapter_n}章做编辑诊断。本章类型：{ch_type}。

## 必须逐条核对的硬约束（违反即整章作废）
{hard_lines}

## 本书设定（判断是否自相矛盾时用）
{format_bible(bible)[:2500]}

## 本题材的质量维度（权重越高越重要）
{rubric_lines}

## 本章正文
{text[:9000]}

## 你的任务
1. 逐条核对硬约束，找出违反的地方。**必须引用正文原句作为证据**，找不到原句就不要报。
2. 按质量维度给分（1-10）。只对 6 分以下的维度写问题。
3. 找出与设定自相矛盾的地方（人物能力、时间线、谁已经死了、能力提前到手）。

宁可少报也不要编。没有问题就给空数组。

## 输出（只输出 JSON，不要任何解释）
{{
  "violations": [
    {{"constraint": "被违反的那条硬约束", "evidence": "正文原句", "advice": "怎么改，一句话"}}
  ],
  "scores": [{{"name": "维度名", "score": 7}}],
  "weak": [
    {{"name": "维度名", "score": 5, "problem": "具体问题", "evidence": "正文原句", "advice": "怎么改"}}
  ],
  "contradictions": [
    {{"problem": "和什么设定矛盾", "evidence": "正文原句", "advice": "怎么改"}}
  ]
}}"""


def strip_fences(t: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip(), flags=re.MULTILINE)


# ── 报告 ──────────────────────────────────────────────────────────────────────

SOURCE_RANK = {"硬约束": 0, "一致性": 1, "机械": 2, "质量": 3}


def dedupe_items(items: list) -> list:
    """同一处问题常被三个通道各报一遍（硬约束 / 一致性 / 质量都会说"哑巴开口了"）。

    按证据原文合并，保留最严重的那个来源，把其他来源的建议并进来——让作者看到
    一条问题一次，而不是同一句话被要求改四遍。
    """
    def frags(it):
        """证据切成句子片段。各通道引用同一处时边界常不同——有的报单句，有的把两句
        并在一起，有的多带一个「低声道」——所以按片段互为子串判同，比整段比对可靠。
        不在引号处切分：引号内外本来是一句话。阈值放到 4 字，中文短句常常只有 3-5 字。"""
        parts = re.split(r"[/、。！？…；;\n]+", it.get("evidence", ""))
        out = set()
        for p in parts:
            p = re.sub(r"[\s，,：:－—…「」『』“”\"']+", "", p)
            if len(p) >= 4:
                out.add(p)
        return out

    def same_spot(f1, f2) -> bool:
        return any(a in b or b in a for a in f1 for b in f2)

    items = sorted(items, key=lambda it: SOURCE_RANK.get(it["source"], 9))
    merged = _merge_once(items, frags, same_spot)
    # 合并会让某条的证据变长（取更全的那一份），于是原先匹配不上的条目这一轮就能并进来。
    # 跑到不再变化为止，否则同一个问题仍会分成两条摆在作者面前。
    for _ in range(3):
        again = _merge_once(merged, frags, same_spot)
        if len(again) == len(merged):
            return again
        merged = again
    return merged


def _merge_once(items: list, frags, same_spot) -> list:
    merged = []
    for it in items:
        f = frags(it)
        if not f:                      # 没有证据原文的（例如字数越界）不参与合并
            merged.append(it)
            continue
        hit = next((m for m in merged if same_spot(frags(m), f)), None)
        if hit is None:
            merged.append(it)
            continue
        hit["also"] = sorted(set(hit.get("also", []) + [f"{it['source']}·{it['kind']}"]))
        # 四个通道给的建议往往是同一句话的四种说法，留最具体的那一条就够了，
        # 全串起来反而让作者看不清要做什么。
        cand = (it.get("advice") or "").strip()
        if cand and len(cand) > len(hit.get("advice", "")):
            hit["advice"] = cand
        # 合并后证据取更全的那一份，方便改稿时定位
        if len(it.get("evidence", "")) > len(hit.get("evidence", "")):
            hit["evidence"] = it["evidence"]
    return merged


def render_report(proj: Path, chapter_n: int, mech: list, sem: dict) -> tuple:
    """返回 (报告文本, 编号后的问题清单)。编号就是 --fix 用的号。"""
    items = []
    for i in mech:
        items.append({"source": "机械", "kind": i["kind"], "problem": i["detail"],
                      "evidence": i["evidence"], "advice": i["advice"]})
    for v in (sem.get("violations") or []):
        items.append({"source": "硬约束", "kind": "违反硬约束", "problem": v.get("constraint", ""),
                      "evidence": v.get("evidence", ""), "advice": v.get("advice", "")})
    for c in (sem.get("contradictions") or []):
        items.append({"source": "一致性", "kind": "设定矛盾", "problem": c.get("problem", ""),
                      "evidence": c.get("evidence", ""), "advice": c.get("advice", "")})
    for w in (sem.get("weak") or []):
        items.append({"source": "质量", "kind": f"{w.get('name','')}（{w.get('score','?')}分）",
                      "problem": w.get("problem", ""), "evidence": w.get("evidence", ""),
                      "advice": w.get("advice", "")})

    items = dedupe_items(items)

    lines = [f"# 第{chapter_n}章 诊断报告", ""]
    scores = sem.get("scores") or []
    if scores:
        lines.append("## 各维度得分")
        lines.append("")
        lines.append(" · ".join(f"{s.get('name','')} {s.get('score','?')}" for s in scores))
        lines.append("")
    if not items:
        lines += ["## 结论", "", "没有发现问题。", ""]
    else:
        lines += [f"## 发现 {len(items)} 个问题", ""]
        for n, it in enumerate(items, 1):
            lines.append(f"### {n}. [{it['source']}] {it['kind']}")
            lines.append("")
            if it["problem"]:
                lines.append(f"- 问题：{it['problem']}")
            if it["evidence"]:
                lines.append(f"- 证据：{it['evidence']}")
            if it.get("also"):
                lines.append(f"- 同一处也被判为：{'、'.join(it['also'])}")
            if it["advice"]:
                lines.append(f"- 建议：{it['advice']}")
            lines.append("")
        sample = ",".join(str(i) for i in range(1, min(len(items), 2) + 1))
        lines += ["---", "",
                  f"改其中几条：`python3 review_chapter.py revise --project \"{proj.name}\" "
                  f"--chapter {chapter_n} --fix {sample}`",
                  f"全改：把 `--fix` 换成 `all`。改稿会备份原稿并同步更新摘要。", ""]

    out_dir = proj / "reviews"
    out_dir.mkdir(exist_ok=True)
    report = "\n".join(lines)
    (out_dir / f"ch{chapter_n:03d}_review.md").write_text(report, encoding="utf-8")
    (out_dir / f"ch{chapter_n:03d}_review.json").write_text(
        json.dumps({"chapter": chapter_n, "items": items, "scores": scores},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return report, items


# ── 命令 ──────────────────────────────────────────────────────────────────────

def cmd_diagnose(args) -> int:
    proj = find_project(args.project)
    from write_novel import get_chapter_type, load_state

    state = load_state(proj)
    written = state.get("current_chapter", 0)
    if args.last:
        chapters = list(range(max(1, written - args.last + 1), written + 1))
    else:
        chapters = [args.chapter or written]

    bible = load_json(proj / "bible.json")
    hard = bible.get("hard_constraints") or []
    rubric = load_rubric(args.genre) if args.genre else []
    if args.genre and not rubric:
        print(f"[warn] 没找到题材 {args.genre} 的自检表，改用通用维度")

    client = None
    if not args.mechanical_only:
        client = _cfg.make_client()
        print(f"[接口] {_cfg.describe()}")

    rc = 0
    for n in chapters:
        text = load_chapter(proj, n)
        if not text.strip():
            print(f"[ERROR] 第{n}章不存在")
            rc = 1
            continue
        prev = load_chapter(proj, n - 1) if n > 1 else ""
        mech = mechanical_checks(text, n, prev)
        sem = {}
        if client is not None:
            print(f"诊断第{n}章（{len(text.strip())}字）...", end=" ", flush=True)
            raw = call(client, args.model,
                       system="你是严格但不吹毛求疵的网文编辑。只输出 JSON。",
                       user=build_diagnose_prompt(text, n, bible, rubric, hard,
                                                  get_chapter_type(n)[0]),
                       temp=0.2)
            try:
                sem = json.loads(strip_fences(raw))
            except json.JSONDecodeError:
                (proj / "reviews").mkdir(exist_ok=True)
                (proj / "reviews" / f"ch{n:03d}_raw.txt").write_text(raw, encoding="utf-8")
                print(f"\n[warn] 返回的不是 JSON，原文存到 reviews/ch{n:03d}_raw.txt，只报机械检查")
            else:
                print("完成")
        report, items = render_report(proj, n, mech, sem)
        print()
        print(report)
    return rc


def build_revise_prompt(text: str, chapter_n: int, picked: list, hard: list,
                        genre_rules: str, ch_type: str) -> str:
    probs = "\n".join(
        f"{i}. [{p['kind']}] {p['problem']}\n   证据：{p['evidence']}\n   建议：{p['advice']}"
        for i, p in enumerate(picked, 1)
    )
    hard_lines = "\n".join(f"- {h}" for h in hard) or "（无）"
    return f"""改写一本中文网络小说的第{chapter_n}章。本章类型：{ch_type}。

## 只改下面这些问题，别的别动
{probs}

## 绝对不可违反
{hard_lines}

## 本题材的写作规则
{genre_rules[:3000]}

## 改写纪律
- **情节事实不许变**：本章发生过的事件、出场人物、结局位置都要保留，后面的章节已经建立在它们上面。
- 只针对上面列出的问题改；写得好的段落原样留下。
- 字数保持在 {CHAPTER_MIN_CHARS}-{CHAPTER_MAX_CHARS} 字。
- 正文里不许出现任何写作技巧标签（如"心理——""长句："）或元评论。
- 首行必须是「第{chapter_n}章 标题」，标题可以沿用原标题。
- **套话要替换，不要删掉**。技巧标签是垃圾，删了就干净；套话不是——删掉"他心头一紧"
  会留一个语义空洞，该换成一个具体的动作或生理反应（"他把铜铃按住，不让它响"）。
  删字数会掉，补回来的又往往是另一句套话，所以这里要的是换，不是删。
  同一个词在对话里出现不用改：人物爱说套话是人物特征，叙述者说才是毛病。

## 原文
{text}

## 输出
直接输出改写后的完整章节正文，不要任何说明文字。"""


def cmd_revise(args) -> int:
    proj = find_project(args.project)
    from write_novel import get_chapter_type

    n = args.chapter
    rep = proj / "reviews" / f"ch{n:03d}_review.json"
    if not rep.exists():
        print(f"[ERROR] 还没有第{n}章的诊断报告。先跑：\n"
              f"  python3 review_chapter.py diagnose --project \"{proj.name}\" --chapter {n}")
        return 1
    items = json.loads(rep.read_text(encoding="utf-8"))["items"]
    if not items:
        print(f"第{n}章诊断没有问题，不用改。")
        return 0

    if args.fix.strip().lower() == "all":
        picked = items
    else:
        try:
            idx = [int(x) for x in re.split(r"[,，\s]+", args.fix.strip()) if x]
        except ValueError:
            print("[ERROR] --fix 要么是 all，要么是编号，例如 --fix 1,3")
            return 1
        bad = [i for i in idx if not 1 <= i <= len(items)]
        if bad:
            print(f"[ERROR] 编号 {bad} 超出范围（本章共 {len(items)} 个问题）")
            return 1
        picked = [items[i - 1] for i in idx]

    text = load_chapter(proj, n)
    bible = load_json(proj / "bible.json")
    hard = bible.get("hard_constraints") or []
    genre_rules = load_genre_layer(args.genre) if args.genre else ""

    bak_dir = proj / "chapters" / ".bak"
    bak_dir.mkdir(parents=True, exist_ok=True)
    bak = bak_dir / f"{n:03d}.txt"
    if not bak.exists():          # 只备份最初那一版，反复改稿不会把它冲掉
        shutil.copy2(proj / "chapters" / f"{n:03d}.txt", bak)

    client = _cfg.make_client()
    print(f"[接口] {_cfg.describe()}")
    print(f"改写第{n}章，针对 {len(picked)} 个问题...", end=" ", flush=True)
    new_text = call(client, args.model,
                    system="你是中文网络小说作家兼编辑，改稿时只动该动的地方。",
                    user=build_revise_prompt(text, n, picked, hard, genre_rules,
                                             get_chapter_type(n)[0]),
                    temp=0.7)
    new_text = strip_technique_labels(new_text.strip())
    save_chapter(proj, n, new_text)
    print(f"完成（{len(text.strip())}字 → {len(new_text)}字）")
    print(f"原稿备份：{bak}")

    # 正文变了，摘要必须跟着变，否则后面每一章都会继承旧事实
    print("重新生成本章摘要...", end=" ", flush=True)
    summary = call(client, args.model,
                   system="请简洁准确地概括章节内容。",
                   user=build_compress_prompt(n, new_text), temp=0.3)
    title_m = re.search(r"第\s*\d+\s*章\s*(.+)", new_text[:200])
    title = title_m.group(1).strip()[:30] if title_m else ""
    path = proj / "summaries.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        if r.get("chapter") == n:
            r["title"], r["summary"] = title, summary.strip()[:SUMMARY_MAX_CHARS * 2]
            break
    else:
        rows.append({"chapter": n, "title": title, "summary": summary.strip()})
    rows.sort(key=lambda r: r.get("chapter", 0))
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    print("完成")
    print(f"\n建议再诊断一次确认：python3 review_chapter.py diagnose "
          f"--project \"{proj.name}\" --chapter {n}")
    return 0


def cmd_restore(args) -> int:
    proj = find_project(args.project)
    n = args.chapter
    bak = proj / "chapters" / ".bak" / f"{n:03d}.txt"
    if not bak.exists():
        print(f"[ERROR] 没有第{n}章的备份")
        return 1
    shutil.copy2(bak, proj / "chapters" / f"{n:03d}.txt")
    print(f"第{n}章已退回改稿前的版本。")
    print("注意：摘要还是改稿后那一版，需要重新生成 —— 跑一次 revise 或手工改 summaries.jsonl。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("diagnose", help="诊断，不改稿")
    d.add_argument("--project", "-p")
    d.add_argument("--chapter", "-c", type=int, help="章号，默认最新一章")
    d.add_argument("--last", type=int, help="诊断最近 N 章")
    d.add_argument("--genre", "-g", help="题材，用它的质量自检表与写作规则")
    d.add_argument("--mechanical-only", action="store_true", help="只做不花钱的机械检查")
    d.add_argument("--model", "-m", default=_cfg.model("review"))

    r = sub.add_parser("revise", help="按诊断结果改稿（会覆盖正文，自动备份与更新摘要）")
    r.add_argument("--project", "-p")
    r.add_argument("--chapter", "-c", type=int, required=True)
    r.add_argument("--fix", required=True, help="要改的问题编号，如 1,3；或 all")
    r.add_argument("--genre", "-g")
    r.add_argument("--model", "-m", default=_cfg.model())

    s = sub.add_parser("restore", help="退回改稿前的原稿")
    s.add_argument("--project", "-p")
    s.add_argument("--chapter", "-c", type=int, required=True)

    args = ap.parse_args()
    if args.command == "diagnose":
        return cmd_diagnose(args)
    if args.command == "revise":
        return cmd_revise(args)
    return cmd_restore(args)


if __name__ == "__main__":
    sys.exit(main())

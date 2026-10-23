#!/usr/bin/env python3
"""
write_novel.py — Long-form novel writer with hierarchical memory.

Maintains consistency across hundreds of chapters using four memory layers:

  Layer 1  Story Bible   — world rules, all character profiles, overall arc
                           Always in context. Updated every 10 chapters.
  Layer 2  Arc Plan      — detailed plan for the current 30-chapter arc
                           Auto-refreshed when an arc completes.
  Layer 3  Summary Log   — 2-3 sentence compression of every chapter written
                           Grows permanently; 100 chapters ≈ 5k tokens.
  Layer 4  Recent Prose  — last 2 full chapters for style + scene continuity

Project files (novels/<title>/):
  bible.json        — story bible (world, characters, overall arc)
  arc_plan.txt      — current arc plan
  summaries.jsonl   — one JSON line per chapter (永久记忆)
  state.json        — progress tracker
  chapters/         — full chapter text (001.txt, 002.txt, …)

Commands:
  init     Initialize project from a generate_novel.py output file
  write    Write the next chapter (use --count N to write N in sequence)
  status   Show current progress and memory sizes
  new-arc  Manually trigger a new arc plan
  update-bible  Manually trigger a bible update from recent summaries

Usage:
  python3 write_novel.py init --from generated/西方奇幻/novel.txt
  python3 write_novel.py write
  python3 write_novel.py write --count 10
  python3 write_novel.py status
  python3 write_novel.py new-arc

Requirements:
  pip install openai
  设置 API：python3 setup_api.py set --provider <服务商> --key sk-...
            （或直接 export OPENAI_API_KEY=sk-...；详见 novel_config.py）
"""

import argparse
import json
import os
import sys
import time
import textwrap
from pathlib import Path
from typing import Optional
import novel_config as _cfg

try:
    from openai import OpenAI
except ImportError:
    print("[ERROR] openai not installed. Run: pip install openai")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_MODEL        = _cfg.model()   # 配置文件/环境变量/内置默认，见 novel_config.py
NOVELS_DIR           = Path("novels")
CHAPTER_TARGET_CHARS = 3000   # target length per chapter in Chinese characters
CHAPTER_MIN_CHARS    = 2600   # 连载章节长度要稳，读者按章追更
CHAPTER_MAX_CHARS    = 3600
ARC_LENGTH           = 30     # chapters per arc before auto-refresh
BIBLE_UPDATE_EVERY   = 10     # chapters between automatic bible updates
RECENT_CHAPTERS_N    = 2      # how many full chapters to include as recent prose
SUMMARY_MAX_CHARS    = 150    # max chars per chapter summary
POV_ROTATION_EVERY   = 5      # every Nth chapter shifts to secondary character POV

# Solution 1 — Chapter type rotation cycle
# 每条都带一句"本章不要做什么"。只写"要什么"是不够的：题材规则层里有大量
# "每章必须有冲突/悬念/每千字一次转折"的通用要求，体量是章型指令的几十倍，
# 慢节奏章会被它推成追逃戏——实测第2章（人物章）和第3章（感情章）都变成了
# 负伤逃亡，章型轮换形同虚设。禁止项是把这条指令拉回来的唯一办法。
CHAPTER_TYPE_CYCLE = [
    ("战斗章",  "快节奏动作场面：短句切换镜头，动作描写细腻，力量对比强烈，紧张感贯穿全章"),
    ("人物章",  "慢节奏内省：大量内心独白，角色关系深化，情绪细腻，揭示性格层次。"
                "【本章不要】不要出现战斗、追兵、逃亡、负伤昏迷；冲突只能来自人物关系、"
                "回忆与内心抉择。读者在这一章要的是认识人，不是看他跑。"),
    ("感情章",  "浪漫张力：暧昧对话、欲言又止、感官细节（气味/触感/眼神），感情推进一步。"
                "【本章不要】不要用战斗或逃命来推进感情；张力来自两人之间的试探和没说出口的话。"),
    ("揭秘章",  "信息揭露：读者提前于主角获得关键信息，悬念层层剥开，伏笔兑现。"
                "【本章不要】不要用打斗代替揭秘；关键信息必须靠发现、对话或文献被读到。"),
    ("喘息章",  "幽默或生活化场景：笑点或温情时刻，节奏放松，为下一波高潮积蓄情绪能量。"
                "【本章不要】不要出现生死危机、不要引入新的追兵；这一章的作用就是让读者喘口气。"),
    ("转折章",  "颠覆预期：重大反转或抉择，改变故事走向，结尾让读者目瞪口呆"),
]


# ── File helpers ──────────────────────────────────────────────────────────────

def project_dir(title: str) -> Path:
    safe = title.replace("/", "_").replace(" ", "_").replace("《", "").replace("》", "")
    return NOVELS_DIR / safe


GENRE_PROMPT_DIR = Path(__file__).resolve().parent.parent / "assets" / "prompts"


UNIVERSAL_CRAFT = GENRE_PROMPT_DIR / "_universal_craft.txt"


def load_universal_craft() -> str:
    """所有题材共用的写作手艺。

    这批规则来自西方奇幻那轮 10 次迭代，但逐条都与题材无关（段落、节奏、对话、
    信息释放、人物塑形），所以提到这一层给所有题材共享——否则只有西方奇幻有
    4000 字的具体规则，其他 36 个题材各自只有 900-1100 字，质量天然不在一个档。
    与 --genre 无关，始终生效。
    """
    if not UNIVERSAL_CRAFT.exists():
        return ""
    return "\n".join(l for l in UNIVERSAL_CRAFT.read_text(encoding="utf-8").splitlines()
                      if not l.lstrip().startswith("#")).strip()


def load_genre_layer(genre: str) -> str:
    """Resolve a genre writing template into extra rules appended to the system prompt.

    `genre` is a path to a .txt file, or a template name: "西方奇幻", or
    "女频/悬疑脑洞" when the same genre name exists for both readerships.
    An `<genre>.author.txt` next to it is appended too — that file is never
    touched by build_templates.py, so hand-written rules survive a rebuild.
    """
    direct = Path(genre)
    if direct.suffix == ".txt" and direct.exists():
        paths = [direct]
    else:
        rel = genre.strip("/")
        matches = []
        if "/" in rel:
            cand = GENRE_PROMPT_DIR / f"{rel}.txt"
            if cand.exists():
                matches = [cand]
        else:
            matches = sorted(GENRE_PROMPT_DIR.glob(f"*/{rel}.txt"))
        if not matches:
            available = sorted(p.parent.name + "/" + p.stem for p in GENRE_PROMPT_DIR.glob("*/*.txt"))
            print(f"[ERROR] 找不到题材模版 '{genre}'。可用的：")
            for a in available:
                print(f"        {a}")
            sys.exit(1)
        if len(matches) > 1:
            print(f"[ERROR] 题材 '{genre}' 男频/女频都有，请写明，例如 女频/{genre}")
            sys.exit(1)
        paths = matches

    author = paths[0].with_suffix(".author.txt")
    if author.exists():
        paths.append(author)

    chunks = []
    for path in paths:
        text = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        if text.strip():
            chunks.append(text.strip())
        print(f"题材模版：{path.relative_to(GENRE_PROMPT_DIR.parent.parent)}")
    return "\n\n".join(chunks)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_summaries(proj: Path) -> list[dict]:
    f = proj / "summaries.jsonl"
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_summary(proj: Path, entry: dict) -> None:
    f = proj / "summaries.jsonl"
    with f.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_chapter(proj: Path, n: int) -> str:
    f = proj / "chapters" / f"{n:03d}.txt"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def save_chapter(proj: Path, n: int, text: str) -> Path:
    d = proj / "chapters"
    d.mkdir(exist_ok=True)
    f = d / f"{n:03d}.txt"
    f.write_text(text, encoding="utf-8")
    return f


def load_state(proj: Path) -> dict:
    f = proj / "state.json"
    if not f.exists():
        return {"current_chapter": 0, "arc_number": 1, "arc_start": 1}
    return load_json(f)


def save_state(proj: Path, state: dict) -> None:
    save_json(proj / "state.json", state)


def load_arc_plan(proj: Path) -> str:
    f = proj / "arc_plan.txt"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def save_arc_plan(proj: Path, text: str) -> None:
    (proj / "arc_plan.txt").write_text(text, encoding="utf-8")


# ── Bible formatter ───────────────────────────────────────────────────────────

# ── 信息边界（谁知道什么） ────────────────────────────────────────────────────
# 长篇最隐蔽的一类崩坏：人物用了他不该知道的信息。A 在第 7 章死了，第 20 章 B 的反应
# 像是知道这件事——可正文里没人告诉过他。这类错不像"哑巴说话"那样显眼，读者却会立刻
# 察觉"他怎么会知道"，而且一旦写出去就无法回收：后面的章节全都建立在这个错误之上。
#
# 人物档案记的是"他是谁"（性格、能力、处境），不是"他知道什么"。后者和伏笔一样是会变的
# 状态，也和伏笔一样，靠最近 10 章摘要兜不住——第 3 章谁听见了什么，到第 40 章只能靠
# 这里记着。所以它跟着 open_threads 用同一套机制：每章登记、常驻上下文、渲染层截断。

MAX_NEW_KNOWLEDGE_PER_CHAPTER = 3   # 单章能登记几条。和伏笔同理：不截断的话模型会把
                                    # 人物看见的每个细节都报上来，真正要紧的被稀释
MAX_KNOWS_SHOWN = 6                 # 每个人物渲染进提示词的条数，取最近的


def iter_characters(bible: dict):
    """遍历人物条目，产出 (名字, 可原地修改的 dict)。

    圣经里的人物有三种形状，模型每次刷新都可能换一种：扁平 {"protagonist": {...}}、
    嵌套 {"supporting": {"莉安娜": {...}}}、以及列表。format_bible 为了渲染把它们拍平成
    一个新列表，那份是拷贝，改了不会写回去。这里产出的是原始 dict 的引用，所以调用方
    改完直接 save_json 就能落盘。
    """
    raw = bible.get("characters", [])
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                yield str(item["name"]), item
        return
    if not isinstance(raw, dict):
        return
    for role_key, val in raw.items():
        if isinstance(val, dict):
            if "name" in val:
                yield str(val["name"]), val
            else:
                for char_name, char_data in val.items():
                    if isinstance(char_data, dict):
                        yield str(char_name), char_data
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and item.get("name"):
                    yield str(item["name"]), item


def match_character(bible: dict, who: str):
    """按模型给的名字找人物条目。找不到返回 None。

    模型报上来的名字常是简称（"伊利亚"），圣经里是全名（"伊利亚·洛恩"），反过来也有。
    所以双向包含都算命中，取最长的那个——"塞拉" 同时像 "塞拉菲妮" 和 "塞拉斯" 时，
    宁可不猜：两个都匹配上就返回 None，让这条记不进去，也比记到错人头上好。
    """
    who = (who or "").strip()
    if not who:
        return None
    hits = [(name, ref) for name, ref in iter_characters(bible)
            if name and (who in name or name in who)]
    if len(hits) != 1:
        return None
    return hits[0][1]


def update_knowledge(proj: Path, chapter_n: int, changes: list) -> int:
    """把本章「谁知道了什么」写进人物档案。返回登记条数。"""
    bible_path = proj / "bible.json"
    if not bible_path.exists() or not changes:
        return 0
    bible = load_json(bible_path)
    added = 0
    for item in changes[:MAX_NEW_KNOWLEDGE_PER_CHAPTER]:
        if not isinstance(item, dict):
            continue
        what = str(item.get("learned") or "").strip()
        ref = match_character(bible, str(item.get("who") or ""))
        if not what or ref is None:
            continue
        knows = ref.setdefault("knows", [])
        if not isinstance(knows, list):
            knows = ref["knows"] = []
        if any(isinstance(k, dict) and str(k.get("what", ""))[:12] == what[:12]
               for k in knows):
            continue                      # 已经记过，别重复
        knows.append({"what": what, "chapter": chapter_n})
        added += 1
    if added:
        save_json(bible_path, bible)
    return added


def format_knows(ch: dict) -> str:
    """渲染一个人物的已知信息。空的就返回空串，不占提示词。"""
    knows = ch.get("knows") or []
    items = [k for k in knows if isinstance(k, dict) and k.get("what")]
    if not items:
        return ""
    shown = items[-MAX_KNOWS_SHOWN:]
    more = f"（更早 {len(items) - len(shown)} 条略）" if len(items) > len(shown) else ""
    body = "；".join(f"{k['what']}（第{k.get('chapter','?')}章得知）" for k in shown)
    return f"  已知{more}：{body}"


def format_bible(bible: dict) -> str:
    """Render the story bible as readable text for prompt injection."""
    lines = []
    lines.append(f"【书名】《{bible.get('title', '未知')}》  品类：{bible.get('category', '')}")
    lines.append(f"【主题】{bible.get('theme', '')}")
    lines.append("")

    # 硬约束放最前：这些是"违反了这一章就废"的事实，必须在一堆设定里先被看见
    hard = bible.get("hard_constraints") or []
    if hard:
        lines.append("══ 绝对不可违反的事实 ══")
        for h in hard:
            lines.append(f"  ! {h}")
        lines.append("")

    world = bible.get("world", {})
    lines.append("── 世界设定 ──")
    lines.append(f"背景：{world.get('setting', '')}")
    lines.append(f"力量体系：{world.get('power_system', '')}")
    rules = world.get("key_rules", [])
    if rules:
        lines.append("核心规则：" + "；".join(rules))
    factions = world.get("factions", [])
    if factions:
        def _fmt_faction(f):
            if isinstance(f, dict):
                return f"{f['name']}（{f.get('description', '')}）"
            return str(f)
        lines.append("主要势力：" + "、".join(_fmt_faction(f) for f in factions))
    lines.append("")

    lines.append("── 角色档案 ──")
    raw_chars = bible.get("characters", [])
    if isinstance(raw_chars, dict):
        char_list = []
        for role_key, val in raw_chars.items():
            if isinstance(val, dict):
                if "name" in val:
                    # flat form: {"protagonist": {"name": "...", ...}}
                    val.setdefault("role", role_key)
                    char_list.append(val)
                else:
                    # nested form: {"supporting": {"莉安娜·黑荆": {...}}}
                    for char_name, char_data in val.items():
                        if isinstance(char_data, dict):
                            entry = {"name": char_name, "role": role_key, **char_data}
                        else:
                            entry = {"name": char_name, "role": role_key}
                        char_list.append(entry)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        item.setdefault("role", role_key)
                        char_list.append(item)
        raw_chars = char_list
    for ch in raw_chars:
        lines.append(f"【{ch.get('role','?')}】{ch['name']}")
        if ch.get("appearance"):
            lines.append(f"  外形：{ch['appearance']}")
        lines.append(f"  性格：{ch.get('personality','')}")
        if ch.get("growth_arc"):
            ga = ch["growth_arc"]
            lines.append(f"  成长弧：起点({ga.get('start','')}) → 终点({ga.get('end','')})  当前阶段：{ga.get('current_stage','')}")
            # 里程碑每 10 章刷新时只增不减，几百章后能堆成几千字，挤掉近期原文的预算。
            # 靠提示词要求模型自觉精简不可靠，这里在渲染层硬性只取最近几条。
            ms = ga.get("milestones") or []
            if ms:
                shown = ms[-MAX_MILESTONES_SHOWN:]
                more = f"（更早 {len(ms) - len(shown)} 条略）" if len(ms) > len(shown) else ""
                lines.append(f"  近期里程碑{more}：" + "；".join(str(x) for x in shown))
        lines.append(f"  当前状态：{ch.get('current_state','')}")
        lines.append(f"  能力：{ch.get('abilities','')}")
        kn = format_knows(ch)
        if kn:
            lines.append(kn)
        rels = ch.get("relationships", {})
        if rels:
            lines.append("  关系：" + "；".join(f"{k}→{v}" for k, v in rels.items()))
    lines.append("")

    romance = bible.get("romance_lines", [])
    if isinstance(romance, dict):
        romance = [{"name": k, **v} for k, v in romance.items() if isinstance(v, dict)]
    if romance:
        lines.append("── 感情线（错峰出现，同时最多两条） ──")
        for r in romance:
            desc = r.get('beauty_style') or r.get('descriptor', '')
            tone = r.get('tone') or r.get('chapters', '')
            lines.append(f"  {r.get('name','')}：{desc} | {tone} | 状态：{r.get('status','')}")
        lines.append("")

    envs = bible.get("environments", [])
    if isinstance(envs, dict):
        envs = [{"name": k, **v} for k, v in envs.items() if isinstance(v, dict)]
    if envs:
        lines.append("── 大环境迁移 ──")
        for e in envs:
            growth = e.get('growth') or e.get('gain', '')
            challenge = e.get('challenge_type') or e.get('theme', '')
            lines.append(f"  {e.get('name','')}：{challenge} | 主角所获成长：{growth} | 状态：{e.get('status','')}")
        lines.append("")

    arc = bible.get("overall_arc", {})
    lines.append("── 整体弧线 ──")
    lines.append(f"开篇：{arc.get('beginning','')}")
    lines.append(f"中段：{arc.get('midpoint','')}")
    lines.append(f"高潮：{arc.get('climax','')}")
    lines.append(f"结局：{arc.get('resolution','')}")

    return "\n".join(lines)


FORMAT_SUMMARY_RECENT = 10   # 进上下文的摘要章数；更早的靠圣经/伏笔台账/弧线提要承载

# ── 伏笔台账 ──────────────────────────────────────────────────────────────────
# 只有最近 FORMAT_SUMMARY_RECENT 章的摘要进上下文，所以跨二十章以上的伏笔靠摘要是
# 记不住的。台账存在圣经的 open_threads 里，常驻每章提示词，直到被兑现。

MAX_OPEN_THREADS = 12          # 常驻上下文的未兑现伏笔条数上限
STALE_THREAD_CHAPTERS = 30     # 超过这么多章没兑现就标记为"该收了"
MAX_NEW_THREADS_PER_CHAPTER = 2  # 单章能新登记几条。实测模型会把每个未解释的名词
                                 # 都当伏笔报上来（一章 5 条），台账很快变成噪音，
                                 # 真正的核心悬念被碎屑稀释。提示词管不住就在这里截断。


def parse_summary_result(raw: str, chapter_n: int) -> tuple:
    """解析摘要调用的返回。拿不到 JSON 就退回把整段当摘要，不要让一章写作失败。"""
    import re as _re
    cleaned = _re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=_re.MULTILINE)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and data.get("summary"):
            return (str(data["summary"]).strip(),
                    [s for s in (data.get("new_threads") or []) if str(s).strip()],
                    [s for s in (data.get("resolved_threads") or []) if str(s).strip()],
                    [k for k in (data.get("knowledge_changes") or []) if isinstance(k, dict)])
    except json.JSONDecodeError:
        pass
    return raw.strip(), [], [], []


def update_threads(proj: Path, chapter_n: int, new_threads: list, resolved: list) -> tuple:
    """把本章的伏笔变化写进圣经。返回 (新增数, 兑现数)。"""
    bible_path = proj / "bible.json"
    if not bible_path.exists():
        return 0, 0
    bible = load_json(bible_path)
    threads = bible.get("open_threads") or []

    paid = 0
    for r in resolved:
        key = str(r).strip()[:12]
        for th in threads:
            if th.get("status", "open") == "open" and key and key in th.get("thread", ""):
                th["status"] = "paid"
                th["paid_chapter"] = chapter_n
                paid += 1
                break

    existing = {th.get("thread", "")[:14] for th in threads}
    added = 0
    for n in new_threads[:MAX_NEW_THREADS_PER_CHAPTER]:
        n = str(n).strip()
        if n and n[:14] not in existing:
            threads.append({"thread": n, "planted_chapter": chapter_n, "status": "open"})
            existing.add(n[:14])
            added += 1

    bible["open_threads"] = threads
    save_json(bible_path, bible)
    return added, paid


def format_threads(bible: dict, current_chapter: int) -> str:
    threads = [th for th in (bible.get("open_threads") or [])
               if th.get("status", "open") == "open"]
    if not threads:
        return ""
    # 埋得最久的排前面：它们最该被收掉，也最容易被忘记
    threads.sort(key=lambda th: th.get("planted_chapter", 0))
    lines = []
    for th in threads[:MAX_OPEN_THREADS]:
        age = current_chapter - th.get("planted_chapter", current_chapter)
        tag = "  ← 埋了很久，该考虑兑现了" if age >= STALE_THREAD_CHAPTERS else ""
        lines.append(f"  - {th.get('thread','')}（第{th.get('planted_chapter','?')}章埋下，"
                     f"已过{age}章）{tag}")
    if len(threads) > MAX_OPEN_THREADS:
        lines.append(f"  （另有 {len(threads) - MAX_OPEN_THREADS} 条更晚埋下的未兑现伏笔）")
    return "\n".join(lines)




def format_summaries(summaries: list[dict], max_recent: int = FORMAT_SUMMARY_RECENT) -> str:
    if not summaries:
        return "（暂无章节摘要）"
    lines = []
    cutoff = max(0, len(summaries) - max_recent)
    if cutoff > 0:
        lines.append(f"  （第1-{summaries[cutoff-1]['chapter']}章：已压缩入故事圣经）")
    for s in summaries[cutoff:]:
        lines.append(f"  第{s['chapter']:03d}章《{s.get('title','')}》：{s['summary']}")
    return "\n".join(lines)


def load_corpus_style_examples(
    current_chapter: int,
    max_chars: int = 8000,
    category: str = "西方奇幻",
    window: int = 2,
    style_dir: str = "corpus",
) -> str:
    """Load as many chapter excerpts as fit within max_chars from top-rated novels.

    Samples chapters [current_chapter-window .. current_chapter+window] per novel,
    cycling through all top novels until the character budget is exhausted.
    style_dir: reference corpus root, laid out as <style_dir>/<category>/*.txt.
    """
    import re as _re
    category_dir = Path(style_dir) / category
    if not category_dir.exists():
        return ""
    novels = []
    for txt_path in sorted(category_dir.glob("*.txt")):
        try:
            content = txt_path.read_text(encoding="utf-8")
            m = _re.search(r"评分：(\d+\.?\d*)", content[:500])
            score = float(m.group(1)) if m else 0.0
            novels.append((score, txt_path.stem, content))
        except Exception:
            continue
    novels.sort(reverse=True)
    if not novels:
        return ""

    ch_low  = max(1, current_chapter - window)
    ch_high = current_chapter + window
    header = (
        f"## 高分《{category}》第{ch_low}-{ch_high}章风格参考"
        f"（对应当前写作位置，仅学习节奏与笔法，勿抄内容）\n"
    )
    segments: list[str] = []
    used_chars = len(header)

    for score, title, content in novels:
        # Build chapter index: ch_n -> position (keep last occurrence = full text body)
        seen: dict[int, int] = {}
        for m in _re.finditer(r"^第(\d+)章[ \t]", content, _re.MULTILINE):
            ch_n = int(m.group(1))
            seen[ch_n] = m.start()

        candidates = [(n, p) for n, p in sorted(seen.items()) if ch_low <= n <= ch_high]
        if not candidates:
            all_sorted = sorted(seen.items())
            if not all_sorted:
                continue
            closest = min(all_sorted, key=lambda x: abs(x[0] - current_chapter))
            candidates = [closest]

        novel_lines: list[str] = [f"### 《{title}》（评分{score}）"]
        for ch_n, pos in candidates:
            # Use remaining budget split evenly across remaining candidates
            remaining_budget = max_chars - used_chars - len("\n".join(novel_lines))
            if remaining_budget <= 200:
                break
            excerpt_chars = min(remaining_budget, 1500)
            excerpt = content[pos: pos + excerpt_chars].strip()
            novel_lines.append(f"── 第{ch_n}章节选 ──")
            novel_lines.append(excerpt)
            novel_lines.append("")

        chunk = "\n".join(novel_lines) + "\n"
        if used_chars + len(chunk) > max_chars:
            break
        segments.append(chunk)
        used_chars += len(chunk)

    if not segments:
        return ""
    return header + "\n".join(segments)


# ── Prompt builders ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = textwrap.dedent("""\
    你是一位专业的中文网络小说作家，擅长维持跨数百章的剧情连贯性与风格统一性。

    ## 写作规范（必须遵守）
    【语言风格】用现代白话文写作，贴近口语节奏。
               ✗ 避免：文言化表达（如"吾""汝""尔等""此乃""当真""岂料""然则"）。
               ✗ 避免：过于书面的四字成语堆砌，让句子显得硬邦邦。
               ✓ 正确：人物说话像真实的人在说话，内心独白像读者自己会想的那种话。
               ✓ 正确：情绪和动作用口语化、接地气的方式呈现，读起来流畅自然。
               ✓ 正确：善用虚词让句子呼吸自然——"着"表示动作持续（他盯着她）、
                       "了"表示状态变化（她笑了，心里却凉了半截）、
                       "和"连接并列让节奏不生硬（血和汗混在一起）。
                       虚词用得自然，句子才有人味，不像机器在排列词语。
    【段落】每段2-4句，绝不写大段连续文字。重要时刻单独成行。
    【节奏变化】不同章节的句式主导风格必须不同，禁止每章都用相同公式：
               - 战斗章：以极短句为主（≤8字/句），偶尔插入一句长句渲染爆发瞬间。
               - 感情章：以中长句为主（15-30字），节奏舒缓，情绪在字里行间渗透。
               - 揭秘章：短句与长句交替，信息在节奏变化中释放张力。
               - 喘息章：口语化短句为主，轻松随意，间或一句带情绪的长句点睛。
               - 人物章：长句内省为主，让角色的思绪在句子里流动，不急不躁。
               - 转折章：前半舒缓，后半骤然加速，用节奏本身传递"剧变"感。
               章节开篇方式也须与上一章明显不同：上章动作开篇则本章用对话或环境；
               上章内心独白开篇则本章用外部事件切入。
    【内心独白】用直接引语，口语化：他心想：「这不对劲。」或「完了，跑不掉了。」
    【系统提示】格式：【叮咚：获得×××】
    【对话】每句对话单独成段，附带动作或表情描写。对话要像真人说话，不要像在背台词。
    【章节结尾】必须以悬念、反转或紧迫感收尾，让读者无法停下来。
    【外形描写】角色首次登场时必须有具体外形描写：面容、身材、气质、穿着，用细节和比喻让读者留下印象。
               女性角色须从主角视角感受其美貌，不同女性有各自鲜明的美丽风格，不可千篇一律。
    【性格成长】主角的性格须随剧情推进逐步蜕变，当前章节的行为和反应须与其所处成长阶段一致。
    【环境切换】当主角进入新的大环境时，须用笔墨渲染环境氛围，并让主角意识到旧经验在此处的局限。
    【感情线节奏】同一时间最多两条感情线并行，不得三位恋人同时缠绕主角。
    【禁止】不写章节总结、不写元评论、不写"本章完"字样。
    【章节过滤词】正文中禁止出现单独成行的"---悬"、"---章节悬"或"---章节结尾---"；破折号变体如"——悬"、"——章节悬"、"——章节结尾——"也禁止。
               需要悬念时直接写剧情画面，不要用这些标记行。
    【严禁出现写作技巧标注】正文中绝对不能出现任何写作手法的名称或说明，包括但不限于：
               "超短句""长句渲染""长句描写""悬念收尾""悬念""短景""心理描写""动作描写"
               "节奏变化""对话爆发""单独成行""伏笔""反转""氛围渲染"等词语作为标注出现。
               ——这些技巧只能被执行，不能被说出来。违反此规则视为严重错误。
    【一致性】严格遵守故事圣经中的人物性格、外形、世界规则、力量体系。

    所有输出一律使用简体中文。
""")


CHAPTER_LINE_FILTER_WORDS = {"---悬", "---章节悬", "---章节结尾---"}


def normalize_chapter_filter_line(line: str) -> str:
    """Normalize standalone filter lines so dash and punctuation variants are caught."""
    import re as _re

    text = line.strip()
    text = _re.sub(r"^[—–-]+", "---", text)
    text = _re.sub(r"[—–-]+$", "---", text)
    text = _re.sub(r"[。．.!！…\s]+$", "", text)
    return text


def filter_chapter_lines(text: str) -> str:
    """Remove whole-line chapter filter words after generation."""
    kept = []
    for line in text.splitlines():
        if normalize_chapter_filter_line(line) in CHAPTER_LINE_FILTER_WORDS:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


# ── AI 味 ────────────────────────────────────────────────────────────────────
# 读者说"一股 AI 味"的时候，指的通常不是文笔差，是叙述者在替他总结感受：
# 「他心中百感交集」代替了把那件事写出来。模型偏爱这类词，因为它们在任何语境里都成立。
#
# 词表在 assets/ai_cliches.txt，作者的补充在 ai_cliches.author.txt。分两档：铁套话出现
# 一次就报，常见套话达到密度阈值才报——「然而」出现一次正常，出现六次才是毛病，一刀切
# 会把报告刷成噪音，作者看两次就不看了，比不报更糟。

AI_CLICHE_SOFT_MIN = 3   # 非铁套话在一章叙述里出现几次才算毛病


def strip_dialogue(text: str) -> str:
    """把对话内容挖掉，只留叙述。

    人物嘴里说套话是人物特征（一个爱说"命运如此"的角色是立住了的），叙述者说套话才是
    AI 味。所以查套话之前先把引号里的内容剥掉，否则会把写得好的台词当成毛病报上来。
    """
    import re as _re
    # 成对的引号都挖掉；中文网文主要用「」和""，兼容 ''、《》不算对话所以不碰
    for a, b in (("「", "」"), ("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'")):
        text = _re.sub(_re.escape(a) + r"[^" + _re.escape(a + b) + r"]{0,200}?" + _re.escape(b),
                       "　", text)
    return text


def load_ai_cliches() -> list:
    """读套话词表，返回 [(词, 分类, 修法, 是否铁套话)]。

    读不到就返回空表——这一项是增量检查，缺了它其余检查照跑，不该让整章写作失败。
    """
    out = []
    for name in ("ai_cliches.txt", "ai_cliches.author.txt"):
        f = GENRE_PROMPT_DIR.parent / name          # assets/ 下，与 prompts/ 同级
        if not f.exists():
            continue
        cat, advice = "", ""
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                head, _, rest = line.partition("]")
                cat = head.lstrip("[").strip()
                advice = rest.strip()
                continue
            hard = line.startswith("!")
            word = line.lstrip("!").strip()
            if word:
                out.append((word, cat, advice, hard))
    return out


def find_ai_cliches(text: str) -> list:
    """在叙述里找套话。返回 [(词, 分类, 修法, 次数, 是否铁套话)]，按次数降序。"""
    narration = strip_dialogue(text)
    hits = []
    for word, cat, advice, hard in load_ai_cliches():
        n = narration.count(word)
        if n and (hard or n >= AI_CLICHE_SOFT_MIN):
            hits.append((word, cat, advice, n, hard))
    # 同一分类里，长词包含短词时只留长的（「涌上心头」命中就不必再报「涌起一股」）
    hits.sort(key=lambda h: (-len(h[0]), -h[3]))
    kept = []
    for h in hits:
        if not any(h[0] != k[0] and h[0] in k[0] for k in kept):
            kept.append(h)
    kept.sort(key=lambda h: (not h[4], -h[3]))
    return kept


def strip_technique_labels(text: str) -> str:
    """Remove writing-technique annotations the model accidentally includes in prose."""
    import re as _re

    # Technique keyword pattern
    _KW = (
        r"超短句|短句起势|短句收击|短句|长句细描|长句渲染|长句描写|长句|对话爆发|对话推进|短景"
        r"|节奏三拍|节奏变化|节奏切换|氛围渲染|氛围烘托|情绪缓冲|节奏喘息|下一场景"
        r"|悬念收尾|悬念定格|悬念钩子|单独成行|内心独白|伏笔埋设|伏笔|反转|心理描写|动作描写"
        r"|情绪渲染|情感张力|画面感|感官描写|视觉冲击|开篇钩子|钩子|爽点|爆发点"
        r"|冲突动词|战句|律动"
    )

    # Whole-line preview pattern: "下一场景XXX：..." → delete entire line
    _PREVIEW_LINE = _re.compile(r"^[\s—─—─]*下一场景.{0,20}[：:].{0,200}$")
    # Separators that can follow a label keyword
    _SEP = r"[。！？，,：:—\-—─]+"

    lines_out = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("—─—─ \t")

        # Case 0: "下一场景XXX：..." whole-line preview → delete
        if _PREVIEW_LINE.match(line.strip()):
            continue

        # Case 0b: the demonstration format from the genre rules — a bare role label at
        # line start followed by a dash ("心理——他心想：…"). Only the dash form is
        # matched on purpose: "心理，" or "动作：" also occur inside ordinary prose,
        # and stripping those would eat real sentences.
        m0 = _re.match(r"^[\s—─]*(?:短景|心理|动作|环境|镜头|画面|对白|台词|内心|神态)\s*(?:——|—|──)\s*(.*)$", line)
        if m0:
            rest = m0.group(1).strip()
            if len(rest) >= 4:
                lines_out.append(rest)
            continue

        # Case 1+2 combined: label at line start (with optional leading dashes/spaces)
        # If real prose (≥4 chars) follows → keep prose only; else → delete the label line
        m = _re.match(rf"^([—─—─\s]*({_KW})[^\n]{{0,8}}{_SEP})(.*)", line)
        if m:
            rest = m.group(3)
            if len(rest.strip()) >= 4:
                lines_out.append(rest)
            # else: pure label/stub → drop
            continue

        # Case 3: bracket/paren annotations 【超短句】（长句渲染）
        line = _re.sub(rf"[【（][^】）]{{0,15}}({_KW})[^】）]{{0,15}}[】）]", "", line)

        # Case 3b: 括号里的元注释，例如
        #   （十五字解释他童年被修女安提娅救，满足动机补位）
        # 这是模型把题材规则的名字和执行说明直接抄进了正文。盲评里读者一眼看到就出戏，
        # 而 Case 3 的词表只收技巧名词，收不住"满足动机补位""限定20字"这类说法。
        # 只删同时满足"在括号内"且"含写作术语"的，免得误伤正文里正常的括号。
        line = _re.sub(
            r"[（(][^）)]{0,60}?("
            r"满足|动机补位|节奏减速带|信息聚焦|章节功能锚点|场景换色|标签全禁"
            r"|字解释|字说明|字交代|字补|此处补|此处交代|按规则|对应规则|符合要求"
            r"|限定\d+字|不超过\d+字|约\d+字"
            r")[^）)]{0,60}[）)]",
            "", line)

        # Case 4: label after sentence-end punct or comma: "啪！超短句。prose"
        # Replace label+sep with just the preceding punct
        line = _re.sub(
            rf"([。！？，,])({_KW})[^\n]{{0,8}}{_SEP}",
            r"\1",
            line,
        )

        # Case 5: trailing "下一场景..." appended to a prose line → strip
        line = _re.sub(r"下一场景[^\n]*$", "", line).rstrip()

        # Drop the stubs Case 4 can leave behind — a line with nothing but punctuation.
        # Length alone is the wrong test: "血。" and "他愣住了。" are deliberate
        # ultra-short paragraphs that the genre rules ask for, and blank lines are the
        # paragraph separation the author intended. Both must survive.
        if line.strip() and not _re.search(r"[\w\u4e00-\u9fff]", line):
            continue

        lines_out.append(line)

    return filter_chapter_lines("\n".join(lines_out))


def get_chapter_type(chapter_n: int) -> tuple[str, str]:
    """Return (type_name, type_instruction) for this chapter number."""
    return CHAPTER_TYPE_CYCLE[(chapter_n - 1) % len(CHAPTER_TYPE_CYCLE)]


def extract_recent_techniques(recent_chapters: list[tuple[int, str]]) -> str:
    """Solution 2 — scan recent chapters for structural patterns to avoid repeating."""
    if not recent_chapters:
        return ""
    used = []
    for _, text in recent_chapters:
        t = text[:500]
        if any(k in t for k in ["挥拳", "出剑", "攻击", "战斗", "对决"]):
            used.append("战斗/对决开篇")
        if any(k in t for k in ["心想", "脑海", "回忆", "想起"]):
            used.append("内心独白开篇")
        if text.strip().startswith("「") or text.strip()[:1] in ("\u201c", "\u201d"):
            used.append("对话开篇")
        if any(k in t for k in ["突然", "没想到", "万万没想到"]):
            used.append("突然反转")
    if not used:
        return ""
    return "【近期已用手法，本章须避开】：" + "、".join(set(used))


def build_chapter_prompt(
    bible: dict,
    arc_plan: str,
    summaries: list[dict],
    recent_chapters: list[tuple[int, str]],
    next_chapter_n: int,
    next_chapter_hint: str,
    style_dir: str = "corpus",
    proj: Path = None,
) -> str:
    bible_text    = format_bible(bible)
    summary_text  = format_summaries(summaries)
    recent_text   = ""
    for n, text in recent_chapters:
        recent_text += f"\n\n── 第{n:03d}章（近期原文，仅供风格参考）──\n{text}\n"

    # Solution 1 — chapter type
    ch_type_name, ch_type_instr = get_chapter_type(next_chapter_n)

    # Solution 2 — anti-repetition
    avoid_note = extract_recent_techniques(recent_chapters)

    # Solution 4 — POV rotation every Nth chapter
    if next_chapter_n % POV_ROTATION_EVERY == 0:
        raw_chars = bible.get("characters", [])
        if isinstance(raw_chars, dict):
            char_list = []
            for role_key, val in raw_chars.items():
                if isinstance(val, dict):
                    if "name" in val:
                        char_list.append({"role": role_key, **val})
                    else:
                        for char_name, char_data in val.items():
                            entry = {"name": char_name, "role": role_key}
                            if isinstance(char_data, dict):
                                entry.update(char_data)
                            char_list.append(entry)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            char_list.append({"role": role_key, **item})
        else:
            char_list = raw_chars
        supporting = [c for c in char_list if isinstance(c, dict) and c.get("role") == "supporting"]
        pov_char = supporting[0]["name"] if supporting else None
        pov_note = (
            f"\n        【视角转换】本章从配角\u300c{pov_char}\u300d的视角叙述，"
            f"展示主角不知道的信息，制造戏剧性反差。主角可出现但不是叙事中心。"
        ) if pov_char else ""
    else:
        pov_note = ""

    # Hard cap style examples to avoid exceeding TPM limit.
    # Output reserve 4000 tokens; measured input settles around 20k from ch.10 on.
    # Style examples get at most 3,000 chars (~1,800 tokens).
    style_budget_chars = 3000

    arc_digest_text = format_arc_digests(proj) if proj is not None else ""
    arc_digest_block = ("## 往事提要（每弧一段，第10章之前的事都在这里）\n"
                        + arc_digest_text + "\n") if arc_digest_text else ""

    threads_text = format_threads(bible, next_chapter_n)
    threads_block = ("\n## 还没兑现的伏笔（埋得越久越该收）\n" + threads_text +
                     "\n兑现其中任意一条都比埋新伏笔更值；本章若兑现了，在摘要里写明。\n"
                     ) if threads_text else ""

    # 信息边界。记录在人物档案里、也渲染在圣经里了，但光给状态不够——必须明确写出
    # 禁止项，否则模型会为了把场景写顺而让人物"顺理成章地"知道他不该知道的事。
    # 这类错和哑巴说话一样属于写出去就收不回：后面每章都建立在它之上。
    knows_any = any((ch.get("knows") or []) for _, ch in iter_characters(bible))
    knowledge_block = ("\n## 信息边界（谁知道什么）\n"
                       "上面每个人物的「已知」是他**目前掌握的全部关键信息**。\n"
                       "- 人物不得依据不在他「已知」里的信息行动、推理或反应。\n"
                       "- 他可以猜、可以怀疑、可以误解——但不能直接知道。\n"
                       "- 要让某人获知一条新信息，就在正文里**写出他怎么知道的**"
                       "（听见、看见、被告知、亲历），不要让它凭空成立。\n"
                       "读者对这类错极其敏感：他会立刻想「他怎么会知道」，而这一问会把"
                       "前面几十章的信任一起带走。\n") if knows_any else ""

    hard = bible.get("hard_constraints") or []
    hard_block = ""
    if hard:
        # 这几条在圣经开头已经出现过一次。放在离"开始写"最近的位置再说一遍，是因为
        # 设定漂移几乎都发生在"为了把这个场景写顺"的时候——例如让哑巴开口说话。
        hard_block = (
            "\n## 本章绝不可违反（违反即本章作废，宁可改场景也不要改这些）\n"
            + "\n".join(f"- {h}" for h in hard)
            + "\n如果本章提示与上面任何一条冲突，以上面为准，并换一种写法达到提示的效果。\n"
        )

    style_cat = bible.get("category", "西方奇幻")
    style_examples = load_corpus_style_examples(
        current_chapter=next_chapter_n,
        max_chars=style_budget_chars,
        category=style_cat,
        style_dir=style_dir,
    )
    style_block = f"\n{style_examples}\n" if style_examples else ""

    return textwrap.dedent(f"""\
        ## 故事圣经
        {bible_text}

        ## 当前弧线计划
        {arc_plan}

        {arc_digest_block}
        ## 历史章节摘要（最近{FORMAT_SUMMARY_RECENT}章）
        {summary_text}
        {threads_block}
        {recent_text}
        {style_block}
        {knowledge_block}
        {hard_block}
        ## 任务：写第{next_chapter_n}章

        【本章类型】{ch_type_name} — {ch_type_instr}
        {avoid_note}{pov_note}

        本章要点：{next_chapter_hint}

        要求：
        - 字数：{CHAPTER_TARGET_CHARS}字上下，可浮动到 {CHAPTER_MIN_CHARS}-{CHAPTER_MAX_CHARS}字之间。
          这是连载的章节，读者按章追更，忽长忽短会打乱阅读节奏。
          写不满就把一个场景写深，不要硬塞情节；要超了就把本章的后半段留给下一章。
        - 【本章类型】优先于下面任何通用要求，也优先于写作规范里"每章都要有冲突"这类话。
          慢节奏的章节就让它慢，不要为了凑冲突加进追逃或战斗。
        - 第一句直接进入场景，无铺垫废话
        - 严格遵守故事圣经中的人物性格和世界规则
        - 本章结构、开篇方式、情绪基调须与上两章明显不同
        - 混合至少三种句式节奏（超短句 / 对话爆发 / 长句渲染）
        - 章节结尾要留下让读者想翻下一章的理由：悬念、情绪余味、或一个新问题都算。
          慢节奏章不必强行反转。

        请直接输出章节正文（包含章节标题"第{next_chapter_n}章 XXXX"）。
    """)


def build_compress_prompt(chapter_n: int, chapter_text: str, open_threads: list = None) -> str:
    open_list = "\n".join(
        f"  - {th.get('thread','')}（埋于第{th.get('planted_chapter','?')}章）"
        for th in (open_threads or []) if th.get("status", "open") == "open"
    ) or "  （暂无）"
    return textwrap.dedent(f"""\
        以下是小说第{chapter_n}章的完整正文。

        请用{SUMMARY_MAX_CHARS}字以内概括本章内容，要求：
        1. 记录关键情节事件（发生了什么）
        2. 记录角色状态变化（谁的处境改变了）
        3. 记录任何新揭露的信息或伏笔
        4. 说明章节结尾的悬念点

        **只许写正文里真实发生过的事**。这份摘要会成为后续几百章的事实依据，
        写进去的东西之后会被当成既成事实继续用，所以：
        - 不得补上正文没写的事件、人物、地点、能力或伤愈情况；
        - 不得把"可能即将发生"写成"已经发生"；
        - 正文没交代的就不写，宁可短，不要补全。

        ## 顺带记下伏笔的变化

        只有最近 {FORMAT_SUMMARY_RECENT} 章的摘要会进入写作上下文，更早的章节靠故事圣经
        承载。所以本章埋下的伏笔如果不在这里登记，几十章后它就彻底消失了——这是长篇
        最常见的烂尾原因。

        - new_threads：本章**有意留给后文**的核心悬念，**最多 2 条，宁可 0 条**。
          算：可疑的物件、没说完的话、来历不明的人、被打断的仪式、读者会记住并期待答案的东西。
          不算：人物随口提到的小设定、还没验证的传闻、环境里的装饰细节、
          以及任何"某某机制尚未说明"式的技术性空白——那些不是伏笔，是还没写到。
          判断标准：**如果三十章后它没有下文，读者会不会觉得被骗**。不会，就别登记。
          每条写成一句可检查的话，例如"兄长尸体上的符文与主角掌心的裂痕同形，来历未解释"。
        - resolved_threads：本章兑现或解释了哪些**以前**埋下的伏笔，照抄下面清单里的原文。

        ## 顺带记下谁知道了什么

        长篇最隐蔽的崩坏是人物用了他不该知道的信息——A 在第 7 章死了，第 20 章 B 的反应
        像是知道这件事，可正文里没人告诉过他。人物档案记的是"他是谁"，不记"他知道什么"，
        所以这里要登记。

        - knowledge_changes：本章有人**获知了一条他原先不知道的重要信息**，最多 3 条。
          算：听见了关键对话、看见了尸体或证据、被告知了身份或计划、亲历了某个事件。
          不算：读者知道但人物不知道的事、人物的猜测和怀疑（那是伏笔不是知情）、
          环境里的常识、以及"他意识到自己很愤怒"这类心理活动。
          判断标准：**这条信息会改变他之后的行动吗**。不会，就别登记。
          who 写正文里出现的人名（简称也行），learned 写一句话，例如
          {{"who": "塞拉菲妮", "learned": "器皿计划的真正目标是伊利亚"}}

        ### 目前还没兑现的伏笔
        {open_list}

        ## 输出（只输出 JSON，不要任何解释）
        {{
          "summary": "摘要正文",
          "new_threads": ["……"],
          "resolved_threads": ["……"],
          "knowledge_changes": [{{"who": "人名", "learned": "他知道了什么"}}]
        }}

        --- 正文 ---
        {chapter_text[:4000]}
    """)


def build_arc_plan_prompt(
    bible: dict,
    summaries: list[dict],
    arc_number: int,
    arc_start: int,
    written_through: int = 0,
) -> str:
    arc_end = arc_start + ARC_LENGTH - 1
    bible_text   = format_bible(bible)
    summary_text = format_summaries(summaries)

    # 本弧区间内可能已经有写好的章节（立项时导入的开篇就是这种情况）。
    # 不说清楚的话，模型会把已写的章节重新编排一遍，计划和正文当场冲突。
    written_note = ""
    if written_through >= arc_start:
        written_note = textwrap.dedent(f"""\

            ## 注意：第{arc_start}-{written_through}章已经写完
            这些章节的正文已经存在，内容见上面的摘要。为它们写逐章提示时，
            必须与摘要里的既成事实一致，不得改写、不得安排摘要里没有的事件。
            真正需要你规划的是第{written_through + 1}章往后。
            """)

    return textwrap.dedent(f"""\
        ## 故事圣经
        {bible_text}

        ## 已完成章节摘要
        {summary_text}
        {written_note}
        ## 任务：制定第{arc_number}弧弧线计划（第{arc_start}-{arc_end}章）

        根据故事圣经中的整体走向和已完成的剧情，为接下来{ARC_LENGTH}章制定详细计划。

        输出格式：
        第{arc_number}弧：[弧线标题]（第{arc_start}-{arc_end}章）

        弧线目标：[这条弧线要完成什么，推进整体剧情到什么位置]

        关键节点：
        第{arc_start}章：[开篇事件]
        第{arc_start+5}章：[中期转折]
        第{arc_start+15}章：[高潮升级]
        第{arc_start+25}章：[弧线收尾，为下弧埋下伏笔]

        章节类型循环（自动按此顺序分配，可根据剧情微调）：
        战斗章→人物章→感情章→揭秘章→喘息章→转折章→（循环）
        每{POV_ROTATION_EVERY}章安排一次配角视角章（POV轮换）。

        逐章提示：
        （格式：第X章【类型】：一句话写作提示，共{ARC_LENGTH}行）
        第{arc_start}章【战斗章】：...
        第{arc_start+1}章【人物章】：...
        ...
        第{arc_end}章：...

        用简体中文输出。
    """)


def build_bible_update_prompt(
    bible: dict,
    recent_summaries: list[dict],
) -> str:
    bible_text   = format_bible(bible)
    summary_text = format_summaries(recent_summaries)

    return textwrap.dedent(f"""\
        ## 当前故事圣经
        {bible_text}

        ## 最近章节摘要
        {summary_text}

        ## 任务：更新故事圣经

        根据最近章节中发生的事件，更新故事圣经中需要修改的内容。

        以JSON格式输出完整的更新后故事圣经，**字段名必须和上面给你的那份一字不差**
        （hard_constraints、world、characters、open_threads、overall_arc 等都照抄，
        不要改成同义词，不要翻译成中文键名）。改了字段名，读取方就什么都拿不到。
        `hard_constraints` 原样照抄，那是作者定的红线，不随剧情变。
        需要更新的字段包括（如有变化）：
        - 角色的 current_state、relationships、growth_arc.current_stage
        - romance_lines 中各恋人的 status（active/dormant/transitioned）
        - environments 中各环境的 status（current/completed/upcoming）
        - 新揭露的世界信息（key_rules、factions）
        - open_threads：已经兑现的伏笔把 status 改成 paid，不要删掉条目
        不要改变 appearance、整体弧线设定、感情线基调，除非剧情已明确改变。

        **保持精简**：这份圣经每 10 章更新一次，会一直被喂进写作上下文，所以不能只增不减。
        每个人物的 milestones 最多留 6 条最关键的（旧的合并成一句，例如"前期：从杂役
        到被通缉"）；已经走完的环境只留一句结论；current_state 只写当前，不要堆历史。

        直接输出JSON，不要加代码块标记。
    """)


def build_init_bible_prompt(novel_text: str) -> str:
    return textwrap.dedent(f"""\
        以下是一部中文网络小说的策划案和第一章内容。

        请从中提取信息，生成结构化的故事圣经（Story Bible），以JSON格式输出。

        **人物档案要齐全**：`characters` 必须覆盖策划案里的所有主要人物——主角、
        感情线对象、反派都要有，**包括尚未登场的**。尚未登场的人照样写全外形、性格、
        背景、动机，只把 `current_state` 写成"尚未登场，预计第N章出现"。
        漏掉他们的后果是：他们登场那一章只能靠即兴发挥，外形和性格每次都不一样。

        **但要区分"已经发生"和"计划发生"**：策划案里的章节大纲、成长节奏、里程碑讲的
        是将来要写的内容，第一章正文才是已经发生的事。所以 `current_state`、
        `abilities`、`growth_arc.current_stage`、`milestones` 这几个字段只许写
        已经确立的东西；将来才会得到的能力、将来才发生的处境，放在 `growth_arc.end`
        或 `overall_arc` 里描述，不要写成"目前掌握""当前处境"。
        这两条不矛盾：**人物要全，状态要准**。

        **`knows` 写他知道什么，不是写他是谁**。这个字段是用来防止后面几百章里人物
        用上他不该知道的信息——第 7 章死的人，第 20 章不该有人无端知道。所以：
        只写第一章正文里他确实已经掌握的关键信息，一两条就够，写不出来就留空数组；
        不要把读者知道而人物不知道的事写进去，也不要写他的猜测和怀疑（那是伏笔，
        不是知情）。未登场的人物这一项留空。

        JSON结构如下（严格遵守字段名）：
        {{
          "title": "书名",
          "category": "品类",
          "theme": "一句话主题",
          "hard_constraints": [
            "3-6 条绝对不可违反的事实。只写那种'违反了整章就废'的：主角的生理限制（例如不能说话、看不见、缺一条腿）、金手指的代价与边界、世界的铁律、谁已经死了。每条写成可直接检查的断言，例如'主角天生哑症，全书不得让他发出声音说话，对话只能用手势、书写、心声或他人代述'。不要写'人物要有成长'这种无法检查的话。",
            "……"
          ],
          "world": {{
            "setting": "世界背景描述",
            "power_system": "力量/修炼/魔法体系说明",
            "key_rules": ["规则1", "规则2"],
            "factions": [
              {{"name": "势力名", "description": "简介"}}
            ],
            "geography": "地理概要"
          }},
          "characters": [
            {{
              "name": "角色名",
              "role": "protagonist/antagonist/supporting",
              "appearance": "外形描写：面容、身材、气质、标志性穿着（女性角色须突出美貌特色）",
              "personality": "性格特点",
              "growth_arc": {{
                "start": "开篇性格缺陷或局限",
                "end": "最终蜕变后的性格",
                "current_stage": "当前所处成长阶段名称",
                "milestones": ["阶段1触发事件", "阶段2触发事件"]
              }},
              "background": "背景经历",
              "current_state": "当前处境和状态",
              "abilities": "能力/技能",
              "knows": [{{"what": "他目前已经掌握的一条关键信息", "chapter": 1}}],
              "relationships": {{"其他角色名": "关系描述"}}
            }}
          ],
          "romance_lines": [
            {{
              "name": "恋人姓名",
              "beauty_style": "美貌风格（清冷仙气/娇媚/英气飒爽/温婉等）",
              "appearance": "具体外形描写",
              "personality": "性格",
              "tone": "感情基调（青梅竹马/一见钟情/欢喜冤家等）",
              "entry_arc": "预计登场章节范围",
              "exit_or_transition": "退场或转化时机",
              "status": "active/dormant/transitioned"
            }}
          ],
          "environments": [
            {{
              "name": "环境名称",
              "description": "地点/世界层级简介",
              "challenge_type": "核心考验类型（生存/权谋/情感/信念等）",
              "growth": "主角在此获得的成长或领悟",
              "status": "current/completed/upcoming"
            }}
          ],
          "overall_arc": {{
            "beginning": "故事起点和初始冲突",
            "midpoint": "中段转折和升级",
            "climax": "高潮对决",
            "resolution": "结局走向"
          }},
          "total_planned_chapters": 200
        }}

        --- 策划案与第一章 ---
        {novel_text[:6000]}

        直接输出JSON，不要加任何前缀或代码块标记。
    """)


# ── API calls ─────────────────────────────────────────────────────────────────

def call(client: OpenAI, model: str, system: str, user: str, temp: float = 0.7) -> str:
    import time
    kwargs = dict(
        model    = model,
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    if not any(m in model for m in ("o1", "o3")):
        kwargs["temperature"] = temp
    for attempt in range(6):
        try:
            return client.chat.completions.create(**kwargs).choices[0].message.content
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                wait = 60 * (attempt + 1)
                print(f"\n  [限速] 等待{wait}秒后重试（第{attempt+1}次）...", end=" ", flush=True)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("API 调用失败：超过最大重试次数")


# ── Core operations ───────────────────────────────────────────────────────────

def init_project(client: OpenAI, model: str, novel_path: Path, max_chapter: int = 9999) -> None:
    novel_text = novel_path.read_text(encoding="utf-8")

    # Extract chapter 1 text (after the === separator)
    sep = "=" * 40
    parts = novel_text.split(sep, 1)
    outline_text = parts[0]
    chapter1_text = parts[1].strip() if len(parts) > 1 else ""

    # Extract title from outline
    import re
    title_match = re.search(r"【书本名称】\s*(.+)", outline_text)
    title = title_match.group(1).strip() if title_match else novel_path.parent.name

    proj = project_dir(title)
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "chapters").mkdir(exist_ok=True)

    print(f"初始化项目：{proj}")

    # Step 1: Generate story bible
    print("  正在生成故事圣经...", end=" ", flush=True)
    bible_raw = call(client, model,
        system="你是专业的小说策划，请从文本中提取信息生成结构化故事圣经。直接输出JSON。",
        user=build_init_bible_prompt(novel_text),
        temp=0.3,
    )
    # Clean JSON if wrapped in code blocks
    bible_raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "", bible_raw.strip(), flags=re.MULTILINE)
    try:
        bible = json.loads(bible_raw)
    except json.JSONDecodeError:
        print("\n[WARN] 无法解析JSON，使用最小圣经结构。")
        bible = {"title": title, "category": "", "theme": "", "world": {}, "characters": [], "overall_arc": {}}
    save_json(proj / "bible.json", bible)
    print("完成")

    # Step 2: Import the chapters that came with the premise, and summarise them.
    # This happens before the arc plan on purpose: the plan has to be written against
    # what the opening chapters actually say, or it re-invents them and the two conflict.
    # Step 2: Extract ALL existing chapters from the novel file
    import re as _re
    # Find all chapter blocks: "第N章 Title\n...text..."
    all_chapter_matches = list(_re.finditer(r"^第(\d+)章[ \t]+(.+)$", novel_text, _re.MULTILINE))
    # Filter to full chapter blocks (not brief outline entries): a full chapter has >500 chars of body
    existing_chapters: list[tuple[int, str, str]] = []  # (chapter_n, title, text)
    for i, m in enumerate(all_chapter_matches):
        ch_n = int(m.group(1))
        ch_title = m.group(2).strip()
        body_start = m.start()
        body_end = all_chapter_matches[i + 1].start() if i + 1 < len(all_chapter_matches) else len(novel_text)
        body = novel_text[body_start:body_end].strip()
        if len(body) > 500 and ch_n <= max_chapter and ch_n not in [c[0] for c in existing_chapters]:
            existing_chapters.append((ch_n, ch_title, body))

    if not existing_chapters and chapter1_text:
        existing_chapters = [(1, "", chapter1_text)]

    last_chapter_saved = 0
    for ch_n, ch_title, ch_text in existing_chapters:
        save_chapter(proj, ch_n, ch_text)
        print(f"  第{ch_n}章已保存（{len(ch_text)}字）。")
        print(f"  正在压缩第{ch_n}章摘要...", end=" ", flush=True)
        summary_raw = call(client, model,
            system="请简洁准确地概括章节内容。",
            user=build_compress_prompt(ch_n, ch_text,
                                       load_json(proj / "bible.json").get("open_threads")),
            temp=0.3,
        )
        summary, new_th, paid_th, know_ch = parse_summary_result(summary_raw, ch_n)
        clean_title = ch_title.split("（")[0].strip()[:30]
        append_summary(proj, {"chapter": ch_n, "title": clean_title, "summary": summary})
        added, paid = update_threads(proj, ch_n, new_th, paid_th)
        known = update_knowledge(proj, ch_n, know_ch)
        tags = ([f"伏笔 +{added}"] if added else []) + ([f"知情 +{known}"] if known else [])
        print("完成" + (f"（{'，'.join(tags)}）" if tags else ""))
        last_chapter_saved = max(last_chapter_saved, ch_n)

    if not existing_chapters:
        print("  [WARN] 未找到章节正文，请手动添加至 chapters/001.txt")

    # Step 3: Arc plan — now that the opening chapters and their summaries exist,
    # the plan can be built on them instead of guessing at them.
    print("  正在生成第1弧计划（第1-30章）...", end=" ", flush=True)
    arc_plan = call(client, model,
        system="你是专业的小说策划师，请制定详细的弧线计划。",
        user=build_arc_plan_prompt(bible, load_summaries(proj), arc_number=1, arc_start=1,
                                   written_through=last_chapter_saved),
        temp=0.5,
    )
    save_arc_plan(proj, arc_plan)
    print("完成")

    # Step 5: Save initial state
    save_state(proj, {
        "current_chapter": last_chapter_saved,
        "arc_number": 1,
        "arc_start": 1,
    })

    print(f"\n项目初始化完成 → {proj}/")
    print(f"  下一步：python3 write_novel.py write --project \"{title}\"")


def find_outline_file(proj, chapter_n: int):
    """Find a hand-made outline covering chapter_n.

    Looks for `outline_<start>_<end>.txt` in the project dir (e.g. outline_31_60.txt).
    Returning None means "no manual outline for this chapter" — the arc plan is then
    the only source of per-chapter direction, which is the normal case.
    """
    import re as _re
    if proj is None:
        return None
    for path in sorted(proj.glob("outline_*_*.txt")):
        m = _re.fullmatch(r"outline_(\d+)_(\d+)", path.stem)
        if m and int(m.group(1)) <= chapter_n <= int(m.group(2)):
            return path
    return None


def get_next_chapter_hint(arc_plan: str, chapter_n: int, proj=None) -> str:
    """Extract the hint for chapter_n. A manual outline file wins over the arc plan."""
    import re
    outline_file = find_outline_file(proj, chapter_n)
    if outline_file is not None:
        outline = outline_file.read_text(encoding="utf-8")
        m = re.search(rf"第{chapter_n}章【[^】]*】[：:]?\s*(.+)", outline)
        if m:
            return m.group(1).strip()
    # 弧线计划里的行长这样：「第2章【人物章】：躲进废弃香烛仓，回忆失声童年」。
    # 旧正则匹配到「第2章【」就开始捕获，于是提示词里混进「人物章】：」这种残渣，
    # 而章型本来由 CHAPTER_TYPE_CYCLE 单独注入，重复且脏。这里先吃掉【类型】再取正文。
    m = re.search(rf"第{chapter_n}章\s*(?:【[^】]*】)?\s*[：:]?\s*(.+)", arc_plan)
    if m:
        return m.group(1).strip()
    return f"根据弧线计划自然推进剧情，为第{chapter_n}章写出精彩内容。"


def write_chapter(client: OpenAI, model: str, proj: Path, style_dir: str = "corpus",
                  system_prompt: str = SYSTEM_PROMPT) -> int:
    """Write the next chapter. Returns the chapter number written."""
    state    = load_state(proj)
    bible    = load_json(proj / "bible.json")
    arc_plan = load_arc_plan(proj)
    summaries = load_summaries(proj)

    next_n = state["current_chapter"] + 1
    hint   = get_next_chapter_hint(arc_plan, next_n, proj=proj)

    # A manual outline, when one covers this chapter, is the primary arc context
    outline_file = find_outline_file(proj, next_n)
    if outline_file is not None:
        outline_text = outline_file.read_text(encoding="utf-8")
        arc_plan = outline_text[:6000]

    # Load last RECENT_CHAPTERS_N full chapters
    recent = []
    for i in range(max(1, next_n - RECENT_CHAPTERS_N), next_n):
        text = load_chapter(proj, i)
        if text:
            recent.append((i, text))

    print(f"  写第{next_n}章  提示：{hint[:60]}...", end=" ", flush=True)

    chapter_text = call(client, model,
        system=system_prompt,
        user=build_chapter_prompt(bible, arc_plan, summaries, recent, next_n, hint,
                                  style_dir=style_dir, proj=proj),
        temp=0.9,
    )
    chapter_text = strip_technique_labels(chapter_text)
    save_chapter(proj, next_n, chapter_text)
    print("已写入", end=" ", flush=True)

    # Wait before summary call to avoid back-to-back TPM exhaustion
    time.sleep(65)

    # Compress to summary
    summary_raw = call(client, model,
        system="请简洁准确地概括章节内容。只输出 JSON。",
        user=build_compress_prompt(next_n, chapter_text, bible.get("open_threads")),
        temp=0.3,
    )
    summary_text, new_th, paid_th, know_ch = parse_summary_result(summary_raw, next_n)
    added, paid = update_threads(proj, next_n, new_th, paid_th)
    known = update_knowledge(proj, next_n, know_ch)
    if added or paid:
        print(f"[伏笔 +{added}/兑现{paid}]", end=" ", flush=True)
    if known:
        print(f"[知情 +{known}]", end=" ", flush=True)
    import re
    title_m = re.search(r"第\s*\d+\s*章\s*(.+)", chapter_text[:200])
    ch_title = title_m.group(1).strip()[:30] if title_m else ""
    append_summary(proj, {"chapter": next_n, "title": ch_title, "summary": summary_text.strip()})
    print("→ 摘要已保存")

    # Update state
    state["current_chapter"] = next_n
    save_state(proj, state)

    # Check if bible update is needed
    if next_n % BIBLE_UPDATE_EVERY == 0:
        print(f"  [自动] 第{next_n}章：更新故事圣经...", end=" ", flush=True)
        update_bible(client, model, proj)
        print("完成")

    # Check if arc refresh is needed
    arc_start = state.get("arc_start", 1)
    if next_n >= arc_start + ARC_LENGTH - 1:
        new_arc_n     = state.get("arc_number", 1) + 1
        new_arc_start = arc_start + ARC_LENGTH
        print(f"  [自动] 压缩第{state.get('arc_number', 1)}弧提要...", end=" ", flush=True)
        make_arc_digest(client, model, proj, state.get("arc_number", 1),
                        arc_start, new_arc_start - 1)
        print("完成")
        outline_file  = find_outline_file(proj, new_arc_start)
        if outline_file is not None:
            # a hand-made outline already covers this arc — don't overwrite it with AI
            print(f"  [大纲] 第{new_arc_n}弧（第{new_arc_start}章起）由 {outline_file.name} 覆盖，跳过自动生成")
        else:
            print(f"  [自动] 弧线完成，生成第{new_arc_n}弧计划（第{new_arc_start}章起）...", end=" ", flush=True)
            refresh_arc(client, model, proj, new_arc_n, new_arc_start)
            print("完成")
        state["arc_number"] = new_arc_n
        state["arc_start"]  = new_arc_start
        save_state(proj, state)

    return next_n


# ── 弧线提要 ──────────────────────────────────────────────────────────────────
# 摘要只展开最近 10 章，弧线计划只覆盖当前 30 章，跨弧的整体走向原本只有 overall_arc
# 那四句话撑着——200 章的书会散成"一弧一个独立副本"。每弧结束时把那一弧压成一段，
# 常驻上下文，成本是每弧一次调用、每段约 250 字。

MAX_ARC_DIGESTS = 8   # 常驻的弧线提要条数；再早的合并成一句总述
MAX_MILESTONES_SHOWN = 4   # 每个人物渲染进提示词的里程碑条数


def load_arc_digests(proj: Path) -> list:
    f = proj / "arc_digests.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def append_arc_digest(proj: Path, entry: dict) -> None:
    with (proj / "arc_digests.jsonl").open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, ensure_ascii=False) + "\n")


def format_arc_digests(proj: Path) -> str:
    digests = load_arc_digests(proj)
    if not digests:
        return ""
    lines = []
    if len(digests) > MAX_ARC_DIGESTS:
        old = digests[:-MAX_ARC_DIGESTS]
        lines.append(f"  第{old[0].get('start','?')}-{old[-1].get('end','?')}章："
                     + "；".join(d.get("digest", "")[:40] for d in old))
        digests = digests[-MAX_ARC_DIGESTS:]
    for d in digests:
        lines.append(f"  第{d.get('arc','?')}弧（第{d.get('start','?')}-{d.get('end','?')}章）："
                     f"{d.get('digest','')}")
    return "\n".join(lines)


def make_arc_digest(client: OpenAI, model: str, proj: Path, arc_number: int,
                    arc_start: int, arc_end: int) -> None:
    """把一条弧的摘要压成一段，供后续所有章节长期参考。"""
    summaries = [s for s in load_summaries(proj)
                 if arc_start <= s.get("chapter", 0) <= arc_end]
    if not summaries:
        return
    body = "\n".join(f"第{s['chapter']}章：{s['summary']}" for s in summaries)
    digest = call(client, model,
        system="你是小说编辑，擅长把一大段剧情压成几句不丢主线的提要。",
        user=f"""下面是一本长篇小说第{arc_start}-{arc_end}章（第{arc_number}弧）的逐章摘要。

请压成 200-300 字的一段提要，供后面几百章长期参考。必须保住这几样：
- 这一弧主角的处境发生了什么根本变化
- 哪些人物登场、死亡或转变了立场
- 建立了哪些会长期有效的世界规则或实力设定
- 留下了什么还没解决的问题

不要逐章罗列，不要写"本弧讲述了"这种开场话。直接输出提要正文。

{body[:9000]}""",
        temp=0.3)
    append_arc_digest(proj, {"arc": arc_number, "start": arc_start, "end": arc_end,
                             "digest": digest.strip()})


def refresh_arc_variants(client: OpenAI, model: str, proj: Path, arc_number: int,
                         arc_start: int, variants: int) -> list:
    """Write `variants` candidate arc plans to arc_candidates/ without applying any.

    An arc plan steers the next 30 chapters, so it is worth looking at a few options
    before committing — the same reason the project premise is picked from several
    pitches. Nothing touches arc_plan.txt until the author picks one with --use.
    """
    bible     = load_json(proj / "bible.json")
    summaries = load_summaries(proj)
    out_dir   = proj / "arc_candidates"
    out_dir.mkdir(exist_ok=True)

    base = build_arc_plan_prompt(bible, summaries, arc_number, arc_start)
    written, gist = [], []
    for i in range(1, variants + 1):
        extra = ""
        if gist:
            extra = ("\n\n## 已经给过作者的方案（本方案必须在主要事件与转折上明显不同，"
                     "不是换措辞）\n" + "\n".join(f"- 方案{j+1}：{g}" for j, g in enumerate(gist)))
        print(f"  [{i}/{variants}] 生成第{arc_number}弧方案{i}...", end=" ", flush=True)
        text = call(client, model,
            system="你是专业的小说策划师，请制定详细的弧线计划。",
            user=base + extra,
            temp=0.95,
        )
        f = out_dir / f"arc{arc_number}_v{i}.txt"
        f.write_text(text, encoding="utf-8")
        written.append(f)
        gist.append(" / ".join(l.strip() for l in text.splitlines() if l.strip())[:300])
        print("完成")
    return written


def refresh_arc(client: OpenAI, model: str, proj: Path, arc_number: int, arc_start: int) -> None:
    bible     = load_json(proj / "bible.json")
    summaries = load_summaries(proj)
    arc_plan  = call(client, model,
        system="你是专业的小说策划师，请制定详细的弧线计划。",
        user=build_arc_plan_prompt(bible, summaries, arc_number, arc_start),
        temp=0.5,
    )
    save_arc_plan(proj, arc_plan)


# 模型重写整份圣经时会自作主张改字段名。实测过一次：hard_constraints 被改成
# absolute_facts、world 被改成 world_setting，于是 format_bible 一个也读不到——
# 硬约束**静默地**从此不再进提示词，而硬约束正是用来防这类事故的那一层。
# 所以不能信任返回的结构，必须对账。
BIBLE_ALIASES = {
    "hard_constraints": ("absolute_facts", "inviolable_facts", "hard_rules",
                         "absolute_rules", "constraints", "红线", "绝对事实"),
    "world": ("world_setting", "setting", "worldbuilding", "world_building"),
    "open_threads": ("threads", "foreshadowing", "open_foreshadowing", "伏笔"),
    "overall_arc": ("arc", "story_arc", "overall_story_arc"),
    "characters": ("character_profiles", "cast"),
}


def _count_chars(bible: dict) -> int:
    c = bible.get("characters")
    if isinstance(c, list):
        return len(c)
    if isinstance(c, dict):
        return sum(len(v) if isinstance(v, (list, dict)) else 1 for v in c.values())
    return 0


def reconcile_bible(old: dict, new: dict) -> tuple:
    """把模型返回的新圣经对回规范结构。返回 (结果, 这次做了什么的说明)。

    三件事，按严重性排：
    1. 字段名改回来（模型爱用同义词）
    2. hard_constraints 一律沿用旧的——那是作者手写的红线，刷新剧情状态不该动它
    3. 关键字段丢了就从旧的回填；人物数骤减也按丢失处理
    """
    notes = []
    merged = dict(new)

    for canon, aliases in BIBLE_ALIASES.items():
        if canon in merged:
            continue
        for a in aliases:
            if a in merged:
                merged[canon] = merged.pop(a)
                notes.append(f"字段 {a} → {canon}")
                break

    # 红线是人工定的，模型无权改写
    if old.get("hard_constraints"):
        if merged.get("hard_constraints") != old["hard_constraints"]:
            notes.append(f"硬约束沿用原有 {len(old['hard_constraints'])} 条（不随剧情刷新）")
        merged["hard_constraints"] = old["hard_constraints"]

    for key in ("world", "characters", "overall_arc", "open_threads",
                "title", "category", "theme"):
        if old.get(key) and not merged.get(key):
            merged[key] = old[key]
            notes.append(f"{key} 丢失，已从原版回填")

    old_n, new_n = _count_chars(old), _count_chars(merged)
    if old_n and new_n < old_n:
        merged["characters"] = old["characters"]
        notes.append(f"人物从 {old_n} 个掉到 {new_n} 个，判定为丢失，已回滚人物档案")

    # 信息边界按人物逐个回填。刷新圣经的提示词不会提 knows，模型照它自己的结构重写一遍
    # 人物档案，这个字段就整个消失了——和当初 hard_constraints 被改名成 absolute_facts
    # 是同一类事故。提示词管不住的东西就在代码里兜住，别指望模型记得一个它没见过的字段。
    old_knows = {name: ch.get("knows") for name, ch in iter_characters(old)
                 if ch.get("knows")}
    if old_knows:
        restored = 0
        for name, ch in iter_characters(merged):
            if ch.get("knows"):
                continue
            keep = old_knows.get(name) or next(
                (v for k, v in old_knows.items() if k and (k in name or name in k)), None)
            if keep:
                ch["knows"] = keep
                restored += 1
        if restored:
            notes.append(f"{restored} 个人物的已知信息丢失，已回填（刷新圣经不该清空信息边界）")

    return merged, notes


def update_bible(client: OpenAI, model: str, proj: Path) -> None:
    bible    = load_json(proj / "bible.json")
    state    = load_state(proj)
    summaries = load_summaries(proj)
    # Pass only the most recent summaries for efficiency
    recent_summaries = summaries[-BIBLE_UPDATE_EVERY:]

    import re
    updated_raw = call(client, model,
        system="你是专业的小说策划师，请根据最新剧情更新故事圣经。直接输出JSON。",
        user=build_bible_update_prompt(bible, recent_summaries),
        temp=0.2,
    )
    updated_raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "", updated_raw.strip(), flags=re.MULTILINE)
    try:
        updated = json.loads(updated_raw)
    except json.JSONDecodeError:
        print("\n  [WARN] 圣经更新JSON解析失败，保留原版。")
        return
    updated, notes = reconcile_bible(bible, updated)
    save_json(proj / "bible.json", updated)
    for n in notes:
        print(f"\n  [圣经对账] {n}", end="")


# ── Status display ────────────────────────────────────────────────────────────

def show_status(proj: Path, genre: str = "") -> None:
    if not proj.exists():
        print(f"[ERROR] 项目目录不存在：{proj}")
        return

    state     = load_state(proj)
    summaries = load_summaries(proj)
    bible     = load_json(proj / "bible.json") if (proj / "bible.json").exists() else {}
    arc_plan  = load_arc_plan(proj)

    current   = state.get("current_chapter", 0)
    arc_n     = state.get("arc_number", 1)
    arc_start = state.get("arc_start", 1)

    print(f"\n{'='*50}")
    print(f"  项目：《{bible.get('title','?')}》  品类：{bible.get('category','?')}")
    print(f"{'='*50}")
    print(f"  已写章节：{current} 章")
    print(f"  当前弧线：第{arc_n}弧（从第{arc_start}章开始）")
    print(f"  弧线进度：{current - arc_start + 1}/{ARC_LENGTH} 章")
    print(f"  摘要数量：{len(summaries)} 条")

    chapters_dir = proj / "chapters"
    total_chars = sum(
        len(f.read_text(encoding="utf-8"))
        for f in chapters_dir.glob("*.txt")
    ) if chapters_dir.exists() else 0
    print(f"  总字数约：{total_chars:,} 字")

    # Token estimate — 实测而不是按公式估。旧版把历史摘要按"章数 × 75"线性累加，
    # 但 format_summaries 只展开最近 10 章，所以真实用量在第 10 章之后是常数；
    # 旧版还漏算了 system prompt（通用规范 + 题材层），那是几千 token 的大头。
    def _tok(s: str) -> int:
        try:
            import tiktoken
            return len(tiktoken.get_encoding("o200k_base").encode(s))
        except Exception:
            return int(len(s) * 0.9)   # 中文实测约 0.9 token/字

    recent = []
    for i in range(max(1, current - RECENT_CHAPTERS_N + 1), current + 1):
        txt = load_chapter(proj, i)
        if txt:
            recent.append((i, txt))
    sys_prompt = SYSTEM_PROMPT
    craft = load_universal_craft()
    if craft:
        sys_prompt = sys_prompt.rstrip() + "\n\n" + craft
    if genre:
        sys_prompt = sys_prompt.rstrip() + "\n\n" + load_genre_layer(genre)
    user_prompt = build_chapter_prompt(bible, arc_plan, summaries, recent, current + 1,
                                       get_next_chapter_hint(arc_plan, current + 1, proj=proj),
                                       proj=proj)
    sys_t, user_t = _tok(sys_prompt), _tok(user_prompt)
    print(f"\n  下一章的实际输入 token：")
    print(f"    写作规范{'（含题材层）' if genre else '（未带 --genre）'}：~{sys_t:,}")
    print(f"    正文提示（圣经+弧线+摘要+近期原文）：~{user_t:,}")
    print(f"    输入合计：~{sys_t + user_t:,}   加上约 4,000 输出 ≈ {sys_t + user_t + 4000:,}")
    print(f"    这个数在第 {FORMAT_SUMMARY_RECENT} 章之后基本不再增长——摘要只展开最近"
          f"{FORMAT_SUMMARY_RECENT} 章，更早的靠故事圣经、伏笔台账、弧线提要三层承载。")

    print(f"\n  下一章：第{current+1}章")
    hint = get_next_chapter_hint(arc_plan, current + 1)
    print(f"  写作提示：{hint[:80]}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def find_project(title_or_path: Optional[str]) -> Optional[Path]:
    """Resolve --project argument to a project directory."""
    if title_or_path is None:
        # Auto-detect: use the only project if there's exactly one
        if NOVELS_DIR.exists():
            projects = [d for d in NOVELS_DIR.iterdir() if d.is_dir()]
            if len(projects) == 1:
                return projects[0]
            if len(projects) > 1:
                print("[ERROR] 多个项目存在，请用 --project 指定：")
                for p in projects:
                    print(f"  {p.name}")
                sys.exit(1)
        print("[ERROR] 未找到项目，请先运行 init。")
        sys.exit(1)

    # Try as a path first, then as a title
    p = Path(title_or_path)
    if p.exists():
        return p
    p2 = project_dir(title_or_path)
    if p2.exists():
        return p2
    print(f"[ERROR] 找不到项目：{title_or_path}")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Long-form novel writer with hierarchical memory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("command", choices=["init", "write", "status", "new-arc", "update-bible"])
    parser.add_argument("--from", dest="from_file",
        help="[init] Path to generate_novel.py output file.")
    parser.add_argument("--max-chapter", type=int, default=9999,
        help="[init] Only import existing chapters up to this number (e.g. --max-chapter 2 keeps ch1-2, rewrites from ch3).")
    parser.add_argument("--project", "-p",
        help="Project title or path. Auto-detected if only one project exists.")
    parser.add_argument("--count", "-n", type=int, default=1,
        help="[write] Number of chapters to write in sequence (default: 1).")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL,
        help=f"OpenAI model (default: {DEFAULT_MODEL}).")
    parser.add_argument("--style-dir", default="corpus",
        help="[write] Reference corpus to sample style from, laid out as <dir>/<category>/*.txt "
             "where <category> matches bible.json's category field. Missing dir = no style "
             "samples, which is fine. Default: corpus.")
    parser.add_argument("--system-prompt", dest="system_prompt_file",
        help="[write] Path to a .txt file whose contents replace the default SYSTEM_PROMPT.")
    parser.add_argument("--variants", type=int, default=1,
        help="[new-arc] Draft N candidate arc plans into arc_candidates/ instead of "
             "applying one directly, so the author can choose (default: 1).")
    parser.add_argument("--use", dest="use_file",
        help="[new-arc] Apply this candidate file as the current arc plan.")
    parser.add_argument("--genre", "-g",
        help="[write] Genre template whose rules are APPENDED to the system prompt, e.g. "
             "--genre 西方奇幻 (or 女频/悬疑脑洞, or a path to a .txt). See assets/templates/INDEX.md.")
    args = parser.parse_args()

    client = _cfg.make_client()
    print(f"[接口] {_cfg.describe()}")

    if args.command == "init":
        if not args.from_file:
            print("[ERROR] init 命令需要 --from 参数。")
            print("  示例：python3 write_novel.py init --from generated/西方奇幻/novel.txt")
            sys.exit(1)
        novel_path = Path(args.from_file)
        if not novel_path.exists():
            print(f"[ERROR] 文件不存在：{novel_path}")
            sys.exit(1)
        init_project(client, args.model, novel_path, max_chapter=args.max_chapter)

    elif args.command == "write":
        proj = find_project(args.project)
        sys_prompt = SYSTEM_PROMPT
        if args.system_prompt_file:
            sp = Path(args.system_prompt_file)
            if not sp.exists():
                print(f"[ERROR] system-prompt 文件不存在：{sp}")
                sys.exit(1)
            sys_prompt = sp.read_text(encoding="utf-8")
            print(f"使用自定义 system prompt：{sp}")
        craft = load_universal_craft()
        if craft:
            sys_prompt = sys_prompt.rstrip() + "\n\n" + craft + "\n"
        if args.genre:
            layer = load_genre_layer(args.genre)
            if layer:
                sys_prompt = sys_prompt.rstrip() + "\n\n" + layer + "\n"
        print(f"项目：{proj.name}")
        for i in range(args.count):
            print(f"[{i+1}/{args.count}]", end=" ")
            chapter_n = write_chapter(client, args.model, proj, style_dir=args.style_dir,
                                      system_prompt=sys_prompt)
            if i < args.count - 1:
                print("  [冷却] 等待120秒，避免限速...", flush=True)
                time.sleep(120)
        print(f"\n完成。共写至第{chapter_n}章。")
        print(f"文件目录：{proj}/chapters/")

    elif args.command == "status":
        proj = find_project(args.project)
        show_status(proj, args.genre or "")

    elif args.command == "new-arc":
        proj  = find_project(args.project)
        state = load_state(proj)
        arc_n = state.get("arc_number", 1) + 1
        arc_start = state.get("arc_start", 1) + ARC_LENGTH

        if args.use_file:
            src_f = Path(args.use_file)
            if not src_f.exists():
                print(f"[ERROR] 文件不存在：{src_f}")
                sys.exit(1)
            save_arc_plan(proj, src_f.read_text(encoding="utf-8"))
            state["arc_number"] = arc_n
            state["arc_start"]  = arc_start
            save_state(proj, state)
            print(f"已采用 {src_f.name} 作为第{arc_n}弧计划（第{arc_start}章起）")
            print(f"  → {proj}/arc_plan.txt")

        elif args.variants > 1:
            print(f"生成第{arc_n}弧的 {args.variants} 个候选方案（第{arc_start}章起）...")
            files = refresh_arc_variants(client, args.model, proj, arc_n, arc_start, args.variants)
            print("\n候选方案（arc_plan.txt 和进度都没动）：")
            for f in files:
                print(f"  {f}")
            print(f"\n挑定后落地：python3 write_novel.py new-arc --project \"{proj.name}\" "
                  f"--use {files[0].parent}/arc{arc_n}_v<N>.txt")

        else:
            print(f"生成第{arc_n}弧计划（第{arc_start}章起）...", end=" ", flush=True)
            refresh_arc(client, args.model, proj, arc_n, arc_start)
            state["arc_number"] = arc_n
            state["arc_start"]  = arc_start
            save_state(proj, state)
            print("完成")
            print(f"弧线计划已保存 → {proj}/arc_plan.txt")

    elif args.command == "update-bible":
        proj = find_project(args.project)
        print("更新故事圣经...", end=" ", flush=True)
        update_bible(client, args.model, proj)
        print("完成")
        print(f"故事圣经已更新 → {proj}/bible.json")


if __name__ == "__main__":
    main()

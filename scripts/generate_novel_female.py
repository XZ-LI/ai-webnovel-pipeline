#!/usr/bin/env python3
"""
generate_novel_female.py — Generate female-oriented (女频) webnovel openings.

Usage:
  python3 generate_novel_female.py --category 玄幻言情 --learn
  python3 generate_novel_female.py --category 玄幻言情 --analyze
  python3 generate_novel_female.py --list

Requirements:
  pip install openai
  设置 API：python3 setup_api.py set --provider <服务商> --key sk-...
            （或直接 export OPENAI_API_KEY=sk-...；详见 novel_config.py）
"""

import argparse
import json
import os
import random
import re
import sys
import textwrap
from collections import Counter
from pathlib import Path
import novel_config as _cfg
from typing import Optional

try:
    from openai import OpenAI
except ImportError:
    print("[ERROR] openai package not installed. Run: pip install openai")
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────────────

NOVELS_DIR    = Path("corpus/女频")  # 可选的参考语料库：corpus/女频/<题材>/*.txt
OUTPUT_DIR    = Path("generated_female")
DEFAULT_MODEL = _cfg.model()

TOP_CHAPTER_EXAMPLES = 5
EXCERPT_CHARS        = 2000
ENDING_CHARS         = 300
CROSS_CATEGORY_COUNT = 2
CHAPTER_WORD_TARGET  = 2000

# ── Category metadata ──────────────────────────────────────────────────────────

CATEGORY_META: dict[str, dict] = {
    "玄幻言情": {
        "en": "Female Xuanhuan Romance",
        "tropes": [
            "Female protagonist transmigrates into a novel/game as a cannon-fodder or villain side character",
            "She knows the original plot and uses this knowledge to avoid her death flag",
            "Anti-romance-brain: she refuses to be a tool for the male lead's love story",
            "Comedic subversion of tropes — she acts 'out of character', confusing everyone",
            "Hidden overwhelming power revealed gradually; face-slapping arcs against those who underestimated her",
            "Multiple love interests who fall for her; she is not actively pursuing anyone",
            "Cultivation / martial arts system with clear power ranks",
            "Strong sisterhood or loyal side-character support group",
        ],
        "hook": "the moment the protagonist realizes she has transmigrated and recognizes the plot she's in",
    },
    "古风世情": {
        "en": "Ancient-Style Slice of Life Romance",
        "tropes": [
            "Ancient China setting with detailed customs, food, and clothing",
            "Female protagonist with a modern mindset navigating feudal society",
            "Business-building or craft-mastery as a path to independence",
            "Slow-burn romance with a male lead who respects her autonomy",
            "Family politics, scheming relatives, and inheritance disputes",
        ],
        "hook": "the protagonist's first act of defiance against a social constraint",
    },
    "古言脑洞": {
        "en": "Ancient-Style Brain Holes",
        "tropes": [
            "Wildly creative premise set in ancient China (transmigration, system, unusual ability)",
            "Protagonist solves ancient-era problems with lateral thinking",
            "Humor from the clash of her unique perspective with historical norms",
            "Fast-paced plot with frequent comedic reversals",
            "Romance is secondary to the protagonist's personal goals and growth",
        ],
        "hook": "the absurd premise revealed in the very first scene",
    },
    "现言脑洞": {
        "en": "Modern Romance Brain Holes",
        "tropes": [
            "Modern setting with an unconventional twist (supernatural, system, hidden world)",
            "Female protagonist with a sharp tongue and unorthodox problem-solving",
            "Workplace or social satire woven into the romance",
            "Male lead initially antagonistic or indifferent, gradually charmed",
            "Fast comedic pacing with emotional payoffs",
        ],
        "hook": "the protagonist's unconventional first encounter with the male lead or the system",
    },
    "星光璀璨": {
        "en": "Entertainment Circle Romance",
        "tropes": [
            "Female protagonist enters the entertainment industry (acting, singing, variety shows)",
            "She has hidden talent or a past identity that shocks the industry",
            "Scandal management, anti-fans, and industry politics",
            "Male lead is a top celebrity or powerful figure behind the scenes",
            "Public vs. private persona contrast for both leads",
        ],
        "hook": "the protagonist's first public moment that goes viral for unexpected reasons",
    },
    "豪门总裁": {
        "en": "Wealthy CEO Romance",
        "tropes": [
            "Contract marriage, accidental encounter, or enemies-to-lovers with a powerful CEO",
            "Female protagonist is independent and refuses to be a kept woman",
            "Family business intrigue, inheritance battles, and corporate warfare",
            "Male lead has a cold exterior but deep emotional wound only she can reach",
            "Luxurious settings contrasted with genuine emotional vulnerability",
        ],
        "hook": "the first unexpected power reversal between the female protagonist and the CEO",
    },
    "悬疑脑洞": {
        "en": "Mystery Brain Holes (Female)",
        "tropes": [
            "Female protagonist with unusual detective or supernatural ability",
            "Mystery plot intertwined with romance — solving cases together",
            "Unreliable reality: things are not what they seem",
            "Dark humor and absurdist logic",
            "Dual male leads (双男主) possible — contrasting personalities both drawn to her",
        ],
        "hook": "the first impossible mystery the protagonist encounters",
    },
    "青春甜宠": {
        "en": "Youth Sweet Romance",
        "tropes": [
            "Campus or early-adult setting with pure, sweet romance",
            "Female protagonist is bright and warm; male lead is aloof but devoted",
            "Misunderstandings resolved quickly — low angst, high fluff",
            "Slice-of-life warmth: food, seasons, study, and friendship",
            "First love purity contrasted with small life obstacles",
        ],
        "hook": "the first charged moment between the two leads that neither can explain",
    },
    "快穿": {
        "en": "Quick Transmigration",
        "tropes": [
            "Female protagonist transmigrates through multiple worlds completing system tasks",
            "Each world is a different genre (ancient, modern, fantasy, apocalypse)",
            "Consistent core personality across worlds; adapts her methods to each setting",
            "Male lead soul follows her across worlds, recognizing her each time",
            "Face-slapping arcs in every world against the 'white lotus' or villain of that world",
        ],
        "hook": "the protagonist arriving in a new world and instantly assessing her situation",
    },
    "宫斗宅斗": {
        "en": "Palace/Household Political Intrigue",
        "tropes": [
            "Female protagonist navigating cutthroat harem or clan politics",
            "Alliance-building and intelligence-gathering as primary tools",
            "Enemies who appear as allies; dramatic betrayals",
            "Female protagonist's sharp mind vs. male lead's political power",
            "High stakes: survival, not just romance, drives every decision",
        ],
        "hook": "the protagonist surviving her first political trap through unexpected cleverness",
    },
    "科幻末世": {
        "en": "Sci-Fi Apocalypse (Female)",
        "tropes": [
            "Post-apocalyptic world with supernatural powers (zombies, aliens, void beasts)",
            "Female protagonist awakens rare or underestimated ability",
            "Survival first, romance second — pragmatic and ruthless decision-making",
            "Base-building and resource management alongside action",
            "Male lead respects her strength; no damsel-in-distress dynamics",
        ],
        "hook": "the protagonist's first use of her awakened power in a life-or-death situation",
    },
    "职场婚恋": {
        "en": "Workplace Marriage Romance",
        "tropes": [
            "Modern workplace setting with a married or soon-to-be-married couple",
            "Career ambition vs. relationship tension",
            "Hidden marriage or identity revealed to colleagues",
            "Female protagonist proves her professional worth independently of her partner",
            "Warm domestic moments contrasted with workplace competition",
        ],
        "hook": "the awkward first day working with someone she has a secret connection to",
    },
    "种田": {
        "en": "Farming / Homesteading Romance",
        "tropes": [
            "Female protagonist rebuilds life through farming, cooking, or crafting",
            "Space inventory or cheat item that accelerates production",
            "Rural community warmth and gradual acceptance",
            "Male lead is initially cold but won over by her practical competence",
            "Slow-burn romance built through shared labor and daily life",
        ],
        "hook": "the protagonist's first harvest or product that surprises everyone around her",
    },
    "年代": {
        "en": "Republican / Mid-Century Era Romance",
        "tropes": [
            "1930s-1980s China setting with period-accurate social constraints",
            "Female protagonist navigates marriage, work, and social change",
            "Modern sensibility transplanted into a era of hardship",
            "War, reform, or cultural revolution as backdrop to personal story",
            "Romance built through shared survival and ideological growth",
        ],
        "hook": "the protagonist's first act that defies the era's expectations of women",
    },
    "民国言情": {
        "en": "Republican Era Romance",
        "tropes": [
            "1910s-1940s Shanghai or Beijing setting, mixing East and West",
            "Female protagonist is educated, independent, and ahead of her time",
            "Espionage, revolution, or journalism intertwined with romance",
            "Love triangle between tradition and modernity",
            "Rich period detail: qipao, tea houses, concessions, warlord politics",
        ],
        "hook": "the protagonist's first dangerous entanglement with a man from a rival world",
    },
    "女频悬疑": {
        "en": "Female Mystery / Thriller",
        "tropes": [
            "Female protagonist investigates supernatural or criminal cases",
            "Her unusual background (forensics, psychology, spiritual ability) drives the plot",
            "Romance develops through shared danger and mutual respect",
            "Psychological tension and atmospheric dread",
            "Twist revelations that reframe everything the reader thought they knew",
        ],
        "hook": "the protagonist's first encounter with a case that defies rational explanation",
    },
    "游戏体育": {
        "en": "Game / Sports Romance (Female)",
        "tropes": [
            "Female protagonist excels in a male-dominated esports or sports arena",
            "Hidden identity: she is secretly a legendary player or athlete",
            "Team dynamics, rivals, and championship arcs",
            "Male lead is her equal or superior who recognizes her skill first",
            "Online and offline identity gap driving romantic tension",
        ],
        "hook": "the moment her real skill is accidentally revealed to the male lead",
    },
    "女频衍生": {
        "en": "Female Fan Fiction Derivative",
        "tropes": [
            "Transmigration into a known IP (drama, novel, game)",
            "Female protagonist knows the original plot and subverts it",
            "Saving doomed characters or redirecting the story's tragedy",
            "Humor from meta-awareness of fiction conventions",
            "Emotional resonance from loving the source material while changing it",
        ],
        "hook": "the protagonist recognizing which story she has entered and her first deviation from the script",
    },
}


# ── Post-processing ───────────────────────────────────────────────────────────

def strip_technique_labels(text: str) -> str:
    """Remove writing-technique annotations the model accidentally includes in prose."""
    _KW = (
        r"超短句|短句|长句渲染|长句描写|长句|对话爆发|对话推进|短景|节奏变化|节奏切换|氛围渲染|氛围烘托"
        r"|悬念收尾|悬念定格|悬念钩子|单独成行|内心独白|伏笔埋设|伏笔|反转|心理描写|动作描写"
        r"|情绪渲染|情感张力|画面感|感官描写|视觉冲击|开篇钩子|钩子|爽点|爆发点"
    )
    lines_out = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("—─\u2014\u2500 \t")
        if re.fullmatch(rf"({_KW})[。．：:，,\s]*", stripped):
            continue
        m = re.match(rf"^([—─\u2014\u2500\s]*({_KW})[：:——\-\u2014]+)(.*)", line)
        if m and m.group(3).strip():
            lines_out.append(m.group(3).strip())
            continue
        clean = re.sub(rf"[（(【\[][^）)】\]]*({_KW})[^）)】\]]*[）)】\]]", "", line)
        lines_out.append(clean)
    return "\n".join(lines_out)


# ── Novel parser ───────────────────────────────────────────────────────────────

def parse_novel_txt(path: Path) -> dict:
    content = path.read_text(encoding="utf-8", errors="ignore")
    header_end = content.find("第一章")
    if header_end == -1:
        header_end = content.find("第1章")
    header_raw = content[:header_end] if header_end != -1 else content[:800]
    body       = content[header_end:] if header_end != -1 else ""

    def field(name: str) -> str:
        m = re.search(rf"{name}[：:]\s*(.+)", header_raw)
        return m.group(1).strip() if m else ""

    desc_match  = re.search(r"简介[：:]\s*\n([\s\S]*?)(?:\n={10,}|$)", header_raw)
    description = desc_match.group(1).strip() if desc_match else ""

    ch1_match = re.search(r"第\s*[1一]\s*章\s*(.+?)\n([\s\S]*?)(?=\n第\s*[2二]\s*章|\Z)", body)
    if ch1_match:
        ch1_title = ch1_match.group(1).strip()
        ch1_full  = ch1_match.group(2).strip()
    else:
        ch1_any   = re.search(r"第\s*\d+\s*章\s*(.+?)\n([\s\S]{200,})", body)
        ch1_title = ch1_any.group(1).strip() if ch1_any else ""
        ch1_full  = ch1_any.group(2).strip() if ch1_any else body

    ch1_text   = ch1_full[:EXCERPT_CHARS]
    ch1_ending = ch1_full[-ENDING_CHARS:] if len(ch1_full) > ENDING_CHARS else ""

    # Extract chapters 2-5 openings for variation learning
    ch_sequence = []
    for ch_num in range(2, 6):
        pattern = rf"第\s*{ch_num}\s*章\s*(.+?)\n([\s\S]{{100,}}?)(?=\n第\s*{ch_num+1}\s*章|\Z)"
        m = re.search(pattern, body)
        if m:
            ch_sequence.append({
                "chapter": ch_num,
                "title":   m.group(1).strip(),
                "opening": m.group(2).strip()[:600],
            })

    return {
        "title":           field("书名"),
        "author":          field("作者"),
        "rating":          field("评分"),
        "tags":            field("标签"),
        "description":     description,
        "chapter1_title":  ch1_title,
        "chapter1_text":   ch1_text,
        "chapter1_ending": ch1_ending,
        "ch_sequence":     ch_sequence,
    }


def load_content_examples(category: str) -> dict:
    cat_dir = NOVELS_DIR / category
    if not cat_dir.exists():
        return {"all_descriptions": [], "top_chapters": [], "tag_frequency": Counter()}

    parsed = []
    for f in cat_dir.glob("*.txt"):
        try:
            parsed.append(parse_novel_txt(f))
        except Exception:
            pass

    parsed.sort(key=lambda x: float(x.get("rating") or 0), reverse=True)

    tag_counter: Counter = Counter()
    for ex in parsed:
        for tag in re.split(r"[|｜,，、]", ex.get("tags") or ""):
            t = tag.strip()
            if t:
                tag_counter[t] += 1

    return {
        "all_descriptions": parsed,
        "top_chapters":     parsed[:TOP_CHAPTER_EXAMPLES],
        "tag_frequency":    tag_counter,
    }


def load_cross_category_examples(exclude: str) -> list[dict]:
    other_cats = [c for c in CATEGORY_META if c != exclude]
    random.shuffle(other_cats)
    examples = []
    for cat in other_cats:
        cat_dir = NOVELS_DIR / cat
        if not cat_dir.exists():
            continue
        files = list(cat_dir.glob("*.txt"))
        if not files:
            continue
        try:
            ex = parse_novel_txt(random.choice(files))
            ex["_category"] = cat
            examples.append(ex)
        except Exception:
            continue
        if len(examples) >= CROSS_CATEGORY_COUNT:
            break
    return examples


# ── Prompt builders ────────────────────────────────────────────────────────────

def _format_descriptions(novels: list[dict]) -> str:
    blocks = []
    for i, ex in enumerate(novels, 1):
        line = f"  {i:2}. 《{ex['title']}》"
        if ex.get("rating"):
            line += f"  ★{ex['rating']}"
        if ex.get("tags"):
            line += f"  [{ex['tags']}]"
        if ex.get("description"):
            line += f"\n      {ex['description'][:200]}"
        blocks.append(line)
    return "\n".join(blocks)


def _format_tag_frequency(tag_counter: Counter, top_n: int = 20) -> str:
    lines = []
    for tag, count in tag_counter.most_common(top_n):
        lines.append(f"  {count:2}次  {tag}")
    return "\n".join(lines)


def _format_chapter_examples(novels: list[dict]) -> str:
    blocks = []
    for i, ex in enumerate(novels, 1):
        block = f"  --- #{i} 《{ex['title']}》  ★{ex.get('rating','?')} ---\n"
        if ex.get("description"):
            block += f"\n  【简介】\n  {ex['description']}\n"
        label = ex.get("chapter1_title") or "第一章"
        if ex.get("chapter1_text"):
            block += f"\n  【{label} · 开篇节选】\n  {ex['chapter1_text']}\n"
        if ex.get("chapter1_ending"):
            block += f"\n  【{label} · 章末节选】\n  {ex['chapter1_ending']}\n"
        seq = ex.get("ch_sequence", [])
        if seq:
            block += f"\n  【第2-{seq[-1]['chapter']}章开篇节选（学习章节间节奏变化）】\n"
            for ch in seq:
                block += f"  ▸ 第{ch['chapter']}章《{ch['title']}》：{ch['opening'][:300]}\n"
        blocks.append(block)
    return "\n".join(blocks)


def build_system_prompt(cross_examples: Optional[list[dict]] = None) -> str:
    formatting_rules = textwrap.dedent("""\
        ## 女频网文写作规范（必须遵守）

        【语言风格】现代白话文，口语化，自然流畅。避免文言化表达和四字成语堆砌。
                    善用"着""了""和"等虚词让句子有呼吸感。
        【女主角】独立、清醒、有自己的目标，不以男性为中心。内心想法真实接地气。
        【段落】每段2-4句，重要时刻单独成行。
        【节奏】按章节类型调整主导节奏——行动章以短句为主，情感章以中长句为主，不同章节间须有明显变化。
        【内心独白】口语化直接引语：她心想：「这不对劲。」或「完了完了。」
        【对话】每句对话单独成段，附带动作或神情描写，说话像真人，不像背台词。
        【章节结尾】必须以悬念、反转或紧迫感收尾。
        【外形描写】角色首次登场须有外形描写，男女主角各自有鲜明气质，男性角色的美须与性格形成张力。
        【禁止】不写章节总结、不写"本章完"、不在正文中标注写作技巧名称。
    """)

    craft_block = ""
    if cross_examples:
        craft_block = "## 跨品类参考——通用女频写作技巧\n\n"
        for ex in cross_examples:
            craft_block += f"  【{ex.get('_category','?')}】《{ex['title']}》 ★{ex.get('rating','?')}\n"
            if ex.get("chapter1_text"):
                craft_block += f"  开篇：{ex['chapter1_text'][:300]}\n\n"

    return textwrap.dedent(f"""\
        你是一位专业的中文女频网络小说作家，深谙玄幻言情、穿书爽文、反套路女主等女频类型的写作精髓。
        你的文字节奏快、画面感强、女主角鲜活立体，深度契合女频读者口味。

        {formatting_rules}
        {craft_block}
        所有输出一律使用简体中文。
    """)


def build_outline_prompt(category: str, meta: dict, content_pools: Optional[dict]) -> str:
    tropes_block = "\n".join(f"  • {t}" for t in meta["tropes"])

    if content_pools and content_pools["all_descriptions"]:
        all_desc    = content_pools["all_descriptions"]
        top_ch      = content_pools["top_chapters"]
        tag_counter = content_pools.get("tag_frequency", Counter())
        ref_section = textwrap.dedent(f"""\
            ## 品类全览 — 全部{len(all_desc)}部作品
            {_format_descriptions(all_desc)}

            ## 标签频率
            {_format_tag_frequency(tag_counter)}

            ## 精品参考 — 评分最高{len(top_ch)}部（重点学习前五章）
            {_format_chapter_examples(top_ch)}
        """)
    else:
        ref_section = ""

    return textwrap.dedent(f"""\
        ## 品类
        {category}（{meta['en']}）

        ## 品类惯例
        {tropes_block}

        {ref_section}

        ## 任务：生成大纲

        为该品类创作一部原创女频网络小说的完整策划案。

        ===== 平台投稿信息 =====
        【书本名称】（10字以内，吸引女频读者）
        【目标读者】女频
        【作品标签】用"|"分隔，选3-5个
        【主角名】（女主角全名）
        【作品简介】50-500字，第一句直接抓住读者
        =======================

        1. **核心设定** — 一句话概括世界观与核心矛盾，穿书/系统类须说明原著是什么类型的文

        2. **女主角档案**
           - 基本信息：姓名、穿越前身份/穿越后身份、初始处境
           - **外形描写**：具体生动，气质与性格相符
           - **性格**：独立清醒、有自己的目标；列出3个鲜明的性格特点和口头禅/行事风格
           - **成长弧**：开篇局限→触发事件→终点蜕变

        3. **男主角/主要男性角色**（1-2位）
           - 姓名、外形（帅气有特点）、性格、与女主的关系张力

        4. **前五章大纲**（重点）
           每章须包含：章节标题 + 类型（爽文推进/情感铺垫/喜剧反转/揭秘/转折）+ 三句内容概括 + 结尾钩子
           - 第1章必须在500字内完成：世界观建立 + 女主登场 + 第一个爽点或笑点 + 悬念钩子
           - 章节间开篇方式必须不同（动作/对话/环境/内心独白轮换）

        5. **6-20章走向** — 每章一句话概括，标注情感线进展和主要事件节点

        6. **核心爽点设计** — 列出前20章内的5个最爽的"打脸/逆袭/揭秘"时刻

        开篇围绕：{meta['hook']}
        必须颠覆该品类至少一个常见套路。
        用简体中文输出。
    """)


def build_chapter_prompt(outline: str, category: str, meta: dict) -> str:
    return textwrap.dedent(f"""\
        以下是已确定的小说策划案：

        {outline}

        ## 任务：写第一章正文

        根据上方策划案，写出第一章完整正文。要求：
        - 字数：{CHAPTER_WORD_TARGET}字以上
        - 开篇第一句必须直接抓住读者，不写任何铺垫废话
        - 女主角的内心独白要口语化、有趣、接地气
        - 章节结尾制造强烈悬念或笑点+悬念
        - 品类：{category}（{meta['en']}）

        直接输出章节正文（含章节标题），不要重复策划案内容。
        用简体中文输出。
    """)


# ── API call ───────────────────────────────────────────────────────────────────

def call(client: OpenAI, model: str, messages: list, temp: Optional[float] = None) -> str:
    import time
    kwargs: dict = {"model": model, "messages": messages}
    if temp is not None and not any(m in model for m in ("o1", "o3")):
        kwargs["temperature"] = temp
    for attempt in range(6):
        try:
            return client.chat.completions.create(**kwargs).choices[0].message.content
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                wait = 60 * (attempt + 1)
                print(f"\n  [限速] 等待{wait}秒后重试...", end=" ", flush=True)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("API调用失败：超过最大重试次数")


# ── Generation ─────────────────────────────────────────────────────────────────

def generate(client: OpenAI, category: str, model: str, use_content: bool) -> str:
    meta = CATEGORY_META.get(category)
    if meta is None:
        raise ValueError(f"未知品类 '{category}'，运行 --list 查看。")

    content_pools = None
    if use_content:
        content_pools = load_content_examples(category)
        n = len(content_pools["all_descriptions"])
        if n == 0:
            print(f"  [warn] {NOVELS_DIR}/{category}/ 无小说文件，继续生成（正常）。")
            content_pools = None
        else:
            print(f"  已加载 {n} 部作品 + {len(content_pools['top_chapters'])} 部章节节选")

    cross = load_cross_category_examples(category) if use_content else None
    system = build_system_prompt(cross)

    print("  生成大纲...", end=" ", flush=True)
    outline = call(client, model, [
        {"role": "system", "content": system},
        {"role": "user",   "content": build_outline_prompt(category, meta, content_pools)},
    ], temp=0.5)
    print("完成")

    print("  写第一章...", end=" ", flush=True)
    chapter = call(client, model, [
        {"role": "system",    "content": system},
        {"role": "user",      "content": build_outline_prompt(category, meta, content_pools)},
        {"role": "assistant", "content": outline},
        {"role": "user",      "content": build_chapter_prompt(outline, category, meta)},
    ], temp=0.9)
    chapter = strip_technique_labels(chapter)
    print("完成")

    sep = "=" * 40
    return f"{outline}\n\n{sep}\n\n{chapter}"


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Female webnovel generator (女频)")
    parser.add_argument("--category", "-c", help="Category name (e.g. 玄幻言情)")
    parser.add_argument("--learn", action="store_true", help="Load reference-corpus novels as style reference (optional)")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL)
    parser.add_argument("--list", action="store_true", help="List supported categories")
    args = parser.parse_args()

    if args.list:
        print("支持的女频品类：")
        for cat, meta in CATEGORY_META.items():
            n = len(list((NOVELS_DIR / cat).glob("*.txt"))) if (NOVELS_DIR / cat).exists() else 0
            print(f"  {cat:<12}  {meta['en']:<40}  {n}部")
        return

    if not args.category:
        parser.error("请指定 --category，例如：--category 玄幻言情")

    client = _cfg.make_client()
    print(f"[接口] {_cfg.describe()}")
    category = args.category

    if category not in CATEGORY_META:
        print(f"[ERROR] 未知品类 '{category}'，运行 --list 查看。")
        sys.exit(1)

    print(f"品类：{category}  模型：{args.model}  学习模式：{'开' if args.learn else '关'}")
    novel_text = generate(client, category, args.model, use_content=args.learn)

    out_dir = OUTPUT_DIR / category
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "novel.txt"
    out_path.write_text(novel_text, encoding="utf-8")
    print(f"\n已保存 → {out_path}")
    print(f"下一步：")
    print(f"  python3 write_novel.py init --from {out_path}")
    print(f"  之后续写加 --genre \"女频/{category}\" 用上题材模版")


if __name__ == "__main__":
    main()

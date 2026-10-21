#!/usr/bin/env python3
"""
build_templates.py — 把语料库迭代产出固化成按题材的写作模版，打包进本 skill。

为什么要有这一步：评分维度和写作硬规则是从真实高分作品里反推出来的，这个过程
需要一个几百本的参考语料库 + 很多轮模型调用。固化成模版后，使用者不必再建语料库，
直接 `--genre <分类>` 就能用上这批规则。

产出（写进本 skill 的 assets/ 下）：
  assets/prompts/<男频|女频>/<分类>.txt   追加到默认写作指令后面的题材规则层
  assets/templates/<男频|女频>/<分类>.md  给人读的模版：套路、开局钩子、质量自检表
  assets/templates/INDEX.md               全部题材一览 + 成熟度

数据来源（按可得性降级，并在模版里标注成熟度）：
  A 迭代规则  review_loop/<分类>/iter_<best>/system_prompt.txt + summary.json
  B 评分维度  review_loop/<分类>/rubrics.json
  C 题材套路  generate_novel*.py 里的 CATEGORY_META（始终可得）

用法：
  python3 build_templates.py --repo <语料库项目目录> [--skill <skill目录>]

没有 --repo 时只用 C 级数据重建，模版会明确标注"未经语料库迭代"。
"""

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


QUOTE_CLAUSE = re.compile(r"[，,；;]?\s*(?:如|例如|比如|参考)?《[^》]*》.*$")


def strip_source_quotes(text: str) -> str:
    """Remove clauses that quote a named published work.

    The derived criteria themselves are the useful part and are safe to ship; the
    illustrative snippets are lifted from commercial novels, so they stay out of the
    bundled templates unless the person building them asks for them.
    """
    kept = []
    for line in (text or "").splitlines():
        if "《" in line:
            line = QUOTE_CLAUSE.sub("", line).rstrip(" ：:，,。.、")
            if not line.strip():
                continue
        kept.append(line)
    return "\n".join(kept).strip()


def clean_rubrics(rubrics: list, keep_quotes: bool) -> list:
    if keep_quotes:
        return rubrics
    out = []
    for r in rubrics:
        r = dict(r)
        r["description"] = strip_source_quotes(r.get("description", ""))
        for k in ("high_score_example", "low_score_example"):
            v = strip_source_quotes(r.get(k, ""))
            r[k] = v if v else ""
        out.append(r)
    return out


def load_zh_overrides(skill_dir: Path) -> dict:
    """Chinese trope/hook table. The generators' CATEGORY_META is English, which reads
    badly inside a Chinese writing template, so this file takes precedence when present."""
    f = skill_dir / "assets" / "category_zh.json"
    if not f.exists():
        return {}
    data = json.loads(f.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


# 这些规则已经提到 assets/prompts/_universal_craft.txt 给所有题材共享，
# 题材层里不再重复——否则西方奇幻会把同样的 4000 字喂两遍。
UNIVERSAL_RULE_NAMES = {
    "人物鲜活度强化", "动作-心理挂钩", "矛盾行为塑形", "配角钩子扩展", "口吻一致·对话自然",
    "对话身份标签", "口语化停顿", "自然对话断裂化", "去口号宣言", "紧急口语化",
    "时代语言一致", "语言与背景一致", "动作节奏缓冲", "长句拆分", "节奏减速带",
    "动静交错节奏", "环境描述限长", "比喻限量", "形容精简", "信息聚焦", "名词引入节制",
    "动机补位", "情感伏笔递进", "章节功能锚点", "章节冲突刷新", "场景换色",
    "视角与场景差异", "隐形节奏切换", "标签全禁", "无摄影机标注",
}


def drop_universal_rules(rules: str) -> tuple:
    """把已提升到通用层的规则从题材层里摘掉，返回 (剩下的规则, 摘掉条数)。"""
    import re as _re
    if not rules.strip():
        return rules, 0
    blocks = _re.split(r"(?=^【)", rules, flags=_re.M)
    kept, dropped = [], 0
    for b in blocks:
        m = _re.match(r"^【([^】]+)】", b)
        if m and m.group(1) in UNIVERSAL_RULE_NAMES:
            dropped += 1
            continue
        kept.append(b)
    return "".join(kept).strip(), dropped


def load_rule_fixups(skill_dir: Path) -> dict:
    """Instruction-level rewrites for harvested genre rules.

    An iterated prompt sometimes names the setting of the book it was tuned on inside a
    rule ("foreshadow the 龙血契印"), which would push one author's world into everyone
    else's book. Illustrative examples keep their names — the header disclaimer covers
    those — but instructions get genericised here.
    """
    f = skill_dir / "assets" / "genre_rules_fixups.json"
    if not f.exists():
        return {}
    data = json.loads(f.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def apply_fixups(rules: str, pairs) -> tuple:
    applied = 0
    for old, new in pairs or []:
        if old in rules:
            rules = rules.replace(old, new)
            applied += 1
    return rules, applied


def load_category_meta(script_path: Path) -> dict:
    """Import a generator script just to read its CATEGORY_META table."""
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return getattr(mod, "CATEGORY_META", {}) or {}


def genre_layer(best_text: str, seed_text: str) -> str:
    """Strip the shared base rules, keeping only what the loop appended for this genre.

    The loop grows the prompt by appending blocks, so iter_1 is normally a prefix of the
    best iteration. When it isn't (a run that was restarted from a different seed), fall
    back to cutting everything up to the last base-rule line.
    """
    if seed_text and best_text.startswith(seed_text):
        return best_text[len(seed_text):].strip()
    lines = best_text.splitlines()
    cut = max((i for i, l in enumerate(lines) if l.strip().startswith("【禁止】")), default=-1)
    return "\n".join(lines[cut + 1:]).strip() if cut >= 0 else best_text.strip()


def rubric_rules(rubrics: list) -> str:
    """Turn derived scoring criteria into writing rules, heaviest weight first."""
    out = []
    for r in sorted(rubrics, key=lambda x: -x.get("weight", 1)):
        name = r.get("name", "").strip()
        if not name:
            continue
        desc = (r.get("description") or "").strip()
        hi = (r.get("high_score_example") or "").strip()
        lo = (r.get("low_score_example") or "").strip()
        block = f"【{name}（权重{r.get('weight', 1)}）】{desc}"
        if hi:
            block += f"\n           ✓ 高分：{hi}"
        if lo:
            block += f"\n           ✗ 低分：{lo}"
        out.append(block)
    return "\n".join(out)


def find_review_dir(review_root: Path, category: str, gender: str):
    """review_loop dirs are sometimes suffixed __Nanpin / __Nvpin for shared genre names."""
    if review_root is None:
        return None
    suffix = "__Nanpin" if gender == "男频" else "__Nvpin"
    for name in (category + suffix, category):
        d = review_root / name
        if d.is_dir():
            return d
    return None


def harvest(review_dir):
    """Return (genre_rules, rubrics, maturity) from one review_loop category dir."""
    if review_dir is None:
        return "", [], {"level": "C", "note": "未经语料库迭代，仅套路库"}

    rubrics = []
    rp = review_dir / "rubrics.json"
    if rp.exists():
        try:
            rubrics = json.loads(rp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rubrics = []

    sp = review_dir / "summary.json"
    best, score, rounds = None, None, None
    if sp.exists():
        try:
            s = json.loads(sp.read_text(encoding="utf-8"))
            best, score, rounds = s.get("best_iteration"), s.get("best_score"), s.get("total_rounds")
        except json.JSONDecodeError:
            pass

    rules = ""
    if best:
        bf = review_dir / f"iter_{best}" / "system_prompt.txt"
        sf = review_dir / "iter_1" / "system_prompt.txt"
        if bf.exists():
            rules = genre_layer(
                bf.read_text(encoding="utf-8"),
                sf.read_text(encoding="utf-8") if sf.exists() else "",
            )

    if rules:
        note = f"已迭代 {rounds} 轮，采用第 {best} 轮（评审分 {score}）"
        return rules, rubrics, {"level": "A", "note": note}
    if rubrics:
        return "", rubrics, {"level": "B", "note": "有语料库反推的评分维度，写作规则未迭代"}
    return "", [], {"level": "C", "note": "未经语料库迭代，仅套路库"}


DISCLAIMER = (
    "# 下面是本题材的专项规则，追加在通用写作规范之后。\n"
    "# 示例里的人名与设定只是演示用的，不要照搬进正文。\n"
)


def write_prompt_file(path: Path, category: str, meta: dict, rules: str, rubrics: list) -> None:
    parts = [DISCLAIMER, f"## 《{category}》题材专项规则\n"]

    tropes = meta.get("tropes") or []
    if tropes:
        parts.append("【本题材读者预期】以下套路是这个品类的读者来看的东西，要兑现而不是回避：")
        parts.extend(f"           - {t}" for t in tropes)
        parts.append("")
    if meta.get("hook"):
        parts.append(f"【推荐开局】{meta['hook']}\n")

    if rules:
        parts.append(rules + "\n")
    elif rubrics:
        parts.append("以下规则由本题材高分作品反推出的评分维度转写而成，权重越高越优先：\n")
        parts.append(rubric_rules(rubrics) + "\n")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")


def write_template_file(path: Path, category: str, gender: str, meta: dict,
                        rubrics: list, maturity: dict, has_rules: bool) -> None:
    en = meta.get("en", "")
    lines = [
        f"# {category}（{gender}）写作模版",
        "",
        f"英文名：{en}" if en else "",
        f"成熟度：**{maturity['level']} 级** — {maturity['note']}",
        "",
        "## 读者预期与核心套路",
        "",
        "读者点进这个品类就是为了看这些，要兑现：",
        "",
    ]
    lines += [f"- {t}" for t in (meta.get("tropes") or ["（本题材套路库缺失，立项时和作者聊清楚）"])]
    lines += ["", "## 推荐开局钩子", "", meta.get("hook", "（无）"), ""]

    lines += [
        "## 立项时必须定下来的",
        "",
        "除了 `assets/premise_template.txt` 里的通用字段，这个题材还要特别问清：",
        "",
    ]
    tr = meta.get("tropes") or []
    if tr:
        lines.append("- 上面每条套路，这本书是照着写、反着写，还是换个壳？逐条给个说法，"
                     "全部照搬会像同人，全部反写会丢掉读者来看的东西。")
    lines += [
        "- 主角的核心优势从第几章开始显形？太晚读者会走。",
        "- 第一个让读者“憋着一口气想看下一章”的点，落在第几章？",
        "",
    ]

    if rubrics:
        lines += [
            "## 质量自检表",
            "",
            "这些维度是从本题材高分作品里反推出来的。写完一章拿它逐项过，权重高的先看。",
            "",
            "| 维度 | 权重 | 看什么 |",
            "|---|---|---|",
        ]
        for r in sorted(rubrics, key=lambda x: -x.get("weight", 1)):
            desc = (r.get("description") or "").replace("|", "｜")
            lines.append(f"| {r.get('name','')} | {r.get('weight',1)} | {desc} |")
        lines.append("")
    else:
        lines += [
            "## 质量自检表",
            "",
            "本题材暂无语料库反推的评分维度。退回通用维度：钩子强度、核心矛盾、节奏变化、"
            "人物鲜活度、画面感、对话自然度、悬念设计、情感感染力、世界观融合、戏剧性反转。",
            "",
        ]

    g = "" if gender == "男频" else "女频/"
    lines += [
        "## 怎么用",
        "",
        "```bash",
        f'python3 $SK/write_novel.py write --project "<书名>" --genre "{g}{category}"',
        "```",
        "",
        f"`--genre` 会把 `assets/prompts/{gender}/{category}.txt` 追加到默认写作规范之后。"
        + ("" if has_rules else "（本题材的规则层目前只有套路和评分维度，没有迭代出的细则。）"),
        "",
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(l for l in lines if l is not None), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=None,
                    help="语料库项目目录（含 review_loop/ 与 generate_novel*.py）")
    ap.add_argument("--skill", type=Path, default=SKILL_DIR, help="skill 目录，默认本 skill")
    ap.add_argument("--quotes", choices=("strip", "keep"), default="strip",
                    help="评分维度里引用了真实商业作品的原文片段。strip（默认）只保留判分标准，"
                         "适合要分发的版本；keep 保留引文，仅供自己本机参考。")
    args = ap.parse_args()

    repo = args.repo
    gen_male = gen_female = None
    if repo:
        gen_male = repo / "generate_novel.py"
        gen_female = repo / "generate_novel_female.py"
    for cand in (args.skill / "scripts" / "generate_novel.py",):
        if gen_male is None or not gen_male.exists():
            gen_male = cand
    for cand in (args.skill / "scripts" / "generate_novel_female.py",):
        if gen_female is None or not gen_female.exists():
            gen_female = cand

    meta_by_gender = {
        "男频": load_category_meta(gen_male),
        "女频": load_category_meta(gen_female),
    }
    fixups = load_rule_fixups(args.skill)
    zh = load_zh_overrides(args.skill)
    if zh:
        for gender, metas in meta_by_gender.items():
            for category, meta in metas.items():
                o = zh.get(f"{gender}/{category}")
                if o:
                    meta["tropes"] = o.get("tropes", meta.get("tropes"))
                    meta["hook"] = o.get("hook", meta.get("hook"))
        print(f"  套路库：用 assets/category_zh.json 覆盖了 {len(zh)} 个题材的中文版")

    review_root = (repo / "review_loop") if repo and (repo / "review_loop").is_dir() else None
    if review_root is None:
        print("[warn] 没有 review_loop/，只能用套路库重建模版（全部标注 C 级）")

    index = []
    for gender, metas in meta_by_gender.items():
        for category, meta in metas.items():
            rd = find_review_dir(review_root, category, gender)
            rules, rubrics, maturity = harvest(rd)
            rubrics = clean_rubrics(rubrics, args.quotes == "keep")
            rules, n_fix = apply_fixups(rules, fixups.get(f"{gender}/{category}"))
            rules, n_drop = drop_universal_rules(rules)
            if n_drop:
                print(f"       ↳ {n_drop} 条规则已在通用手艺层，题材层不再重复")
            if n_fix:
                print(f"       ↳ 指令级措辞修正 {n_fix} 处（genre_rules_fixups.json）")
            write_prompt_file(args.skill / "assets" / "prompts" / gender / f"{category}.txt",
                              category, meta, rules, rubrics)
            write_template_file(args.skill / "assets" / "templates" / gender / f"{category}.md",
                                category, gender, meta, rubrics, maturity, bool(rules))
            index.append((gender, category, maturity, len(rubrics), len(rules)))
            print(f"  [{maturity['level']}] {gender}/{category}  规则{len(rules)}字  维度{len(rubrics)}项")

    idx_lines = [
        "# 题材写作模版一览",
        "",
        "成熟度：**A** = 规则经语料库迭代并评分挑出最优轮；**B** = 有语料库反推的评分维度，"
        "规则层由维度转写；**C** = 只有套路库。",
        "",
        "A 级最可靠，C 级也能直接写——通用写作规范本身已经覆盖了段落、节奏、对话、章末悬念等基本功。",
        "",
        "| 题材 | 读者 | 成熟度 | 说明 | 评分维度 |",
        "|---|---|---|---|---|",
    ]
    for gender, category, maturity, nrub, nrule in sorted(index, key=lambda x: (x[2]["level"], x[0], x[1])):
        idx_lines.append(f"| {category} | {gender} | {maturity['level']} | {maturity['note']} | {nrub} 项 |")
    idx_lines += ["", f"共 {len(index)} 个题材模版。重建：`python3 scripts/build_templates.py --repo <语料库项目>`", ""]
    (args.skill / "assets" / "templates" / "INDEX.md").write_text("\n".join(idx_lines), encoding="utf-8")

    by_level = {}
    for *_, m, _n, _r in [(g, c, m, n, r) for g, c, m, n, r in index]:
        by_level[m["level"]] = by_level.get(m["level"], 0) + 1
    print(f"\n共 {len(index)} 个题材：" + "，".join(f"{k} 级 {v} 个" for k, v in sorted(by_level.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())

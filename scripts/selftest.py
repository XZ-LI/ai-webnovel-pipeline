#!/usr/bin/env python3
"""
selftest.py — 不花钱的自检：改完 skill 跑一遍，确认没有把已有行为改坏。

覆盖的是**确定性**的部分：文件结构、提示词装配、各种解析与清洗逻辑、配置优先级。
产出质量（写得好不好）测不了，那要靠 review_chapter.py 和真机跑稿，见
references/testing.md。

不调用任何 API，几秒钟跑完。

用法：
  python3 selftest.py            # 全部
  python3 selftest.py -v         # 连通过的项目一起列出
"""

import argparse
import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

PASS, FAIL = [], []


def check(name: str, cond, detail: str = "") -> None:
    (PASS if cond else FAIL).append((name, detail))


def load(mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, SCRIPTS / f"{mod_name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── A 结构完整性 ──────────────────────────────────────────────────────────────

def test_structure():
    expected_scripts = [
        "write_novel", "gen_premise", "review_chapter", "generate_novel",
        "generate_novel_female", "review_loop", "build_templates",
        "novel_config", "setup_api", "selftest", "trigger_test",
        "install_for_agent", "conf",
    ]
    check("触发测试集存在", (SKILL / "evals" / "trigger_eval.json").exists())
    try:
        ev = json.loads((SKILL / "evals" / "trigger_eval.json").read_text(encoding="utf-8"))
        qs = ev.get("queries", [])
        check("触发测试集有正反两类",
              sum(1 for q in qs if q["should_trigger"]) >= 8
              and sum(1 for q in qs if not q["should_trigger"]) >= 8,
              f"共 {len(qs)} 条")
    except Exception as e:
        check("触发测试集可解析", False, str(e))
    skill_md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    check("描述带排除条款", "不要用在这些事上" in skill_md,
          "删了它，书评/睡前故事/切分txt 这类会开始误触发")
    for s in expected_scripts:
        f = SCRIPTS / f"{s}.py"
        check(f"脚本存在：{s}.py", f.exists())
        if f.exists():
            try:
                ast.parse(f.read_text(encoding="utf-8"))
                check(f"语法正确：{s}.py", True)
            except SyntaxError as e:
                check(f"语法正确：{s}.py", False, str(e))

    # agent 无关性：AGENTS.md 是权威指南，SKILL.md 只是 Claude Code 的壳
    agents_md = SKILL / "AGENTS.md"
    check("AGENTS.md 存在（agent 无关的权威指南）", agents_md.exists())
    if agents_md.exists():
        a = agents_md.read_text(encoding="utf-8")
        body = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---\n", 2)
        skill_body = body[2] if len(body) > 2 else ""
        # 抽几条关键纪律，确认两份没分叉
        for key in ("hard_constraints", "大纲类的东西别只给一个版本", "连摘要一起回退"):
            check(f"AGENTS.md 与 SKILL.md 同步：{key[:12]}",
                  (key in a) == (key in skill_body),
                  "两份指南内容分叉了，改完 SKILL.md 要重新生成 AGENTS.md")
        check("AGENTS.md 说明了非 Claude Code 怎么用", "如果你不是 Claude Code" in a)
    # 双语：两份 README 都要有语言切换，且互相指得到
    for doc, other in (("README.md", "README.zh-CN.md"), ("README.zh-CN.md", "README.md"),
                       ("INTRO.md", "INTRO.zh-CN.md"), ("INTRO.zh-CN.md", "INTRO.md")):
        f = SKILL / doc
        if f.exists():
            check(f"{doc} 有语言切换链接", other in f.read_text(encoding="utf-8")[:400],
                  "顶部 400 字内应有指向另一语言版本的链接")
    ag = load("install_for_agent")
    check("入口生成器覆盖主流 agent", len(ag.AGENTS) >= 6, f"只有 {len(ag.AGENTS)} 家")
    check("入口生成器含 Cursor 的 mdc 格式",
          any(v["fmt"] == "mdc" for v in ag.AGENTS.values()))

    docs = ["README.md", "README.zh-CN.md", "SKILL.md", "AGENTS.md",
            "INTRO.md", "INTRO.zh-CN.md"] + [
        f"references/{n}.md" for n in
        ("from-scratch", "genres", "method", "setup", "corpus", "publishing", "reviewing", "testing")
    ]
    for d in docs:
        check(f"文档存在：{d}", (SKILL / d).exists())

    # 文档里引用的 reference 必须真实存在
    refs = set()
    for d in docs:
        p = SKILL / d
        if p.exists():
            refs |= set(re.findall(r"references/[a-z-]+\.md", p.read_text(encoding="utf-8")))
    for r in sorted(refs):
        check(f"引用有效：{r}", (SKILL / r).exists())

    # 题材模版齐全
    prompts = sorted((SKILL / "assets" / "prompts").glob("*/*.txt"))
    tmpls = sorted((SKILL / "assets" / "templates").glob("*/*.md"))
    check("题材规则层 37 份", len(prompts) == 37, f"实际 {len(prompts)}")
    check("题材模版 37 份", len(tmpls) == 37, f"实际 {len(tmpls)}")
    check("题材总览 INDEX.md", (SKILL / "assets/templates/INDEX.md").exists())
    craft = SKILL / "assets" / "prompts" / "_universal_craft.txt"
    check("通用手艺层存在", craft.exists())
    if craft.exists():
        ct = craft.read_text(encoding="utf-8")
        n_rules = ct.count("【")
        check("通用手艺层规则数充足", n_rules >= 25, f"只有 {n_rules} 条")
        check("通用手艺层已泛化人名",
              not any(w in ct for w in ("克莱德", "艾德里安", "莉瑟", "马里斯", "龙血")),
              "示例里还留着西方奇幻的专属人名/设定，会被推进其他题材的书里")
    empty = [p.name for p in prompts if len(p.read_text(encoding="utf-8").strip()) < 200]
    check("规则层都非空", not empty, f"过短：{empty[:3]}")
    no_disc = [p.name for p in prompts if "不要照搬" not in p.read_text(encoding="utf-8")[:300]]
    check("规则层都带免责声明", not no_disc, f"缺失：{no_disc[:3]}")

    # 分发安全：资产里不许夹带机器路径、密钥或作者的私人词。
    # 检测的是**通用模式**，不在这里写死任何人的用户名或作品名——那样等于把要保护的
    # 东西写进了会被分发的文件。需要额外过滤的私人词放 evals/private_terms.txt
    # （一行一个，已在 .gitignore 里，不随仓库分发）。
    patterns = [
        (r"/Users/[A-Za-z0-9._-]+/", "机器绝对路径"),
        (r"/home/[A-Za-z0-9._-]+/", "机器绝对路径"),
        (r"sk-[A-Za-z0-9]{20,}", "疑似 API key"),
        (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "邮箱"),
    ]
    private_file = SKILL / "evals" / "private_terms.txt"
    private = [w.strip() for w in private_file.read_text(encoding="utf-8").splitlines()
               if w.strip() and not w.startswith("#")] if private_file.exists() else []
    leak = []
    for f in list(prompts) + list(tmpls) + [SKILL / "assets/premise_template.txt"]:
        txt = f.read_text(encoding="utf-8")
        for pat, label in patterns:
            if re.search(pat, txt):
                leak.append(f"{f.name}:{label}")
        for w in private:
            if w in txt:
                leak.append(f"{f.name}:私人词")
    check("资产里无机器路径/密钥/私人词", not leak, f"命中：{sorted(set(leak))[:3]}")


# ── B premise 模板必须能被 init 的解析器吃下 ─────────────────────────────────

def test_premise_template():
    t = (SKILL / "assets" / "premise_template.txt").read_text(encoding="utf-8")
    sep = "=" * 40
    check("模板含 40 个 = 分隔线", sep in t)
    check("模板含【书本名称】", bool(re.search(r"【书本名称】\s*\S", t)))
    check("模板的章节标题符合解析正则",
          bool(re.search(r"^第(\d+)章[ \t]+(.+)$", t, re.MULTILINE)))
    check("模板能被切成大纲+正文两段", len(t.split(sep)) == 2)


# ── C 提示词装配 ──────────────────────────────────────────────────────────────

def test_prompt_assembly(tmp: Path):
    wn = load("write_novel")

    proj = tmp / "novels" / "测试书"
    (proj / "chapters").mkdir(parents=True, exist_ok=True)
    bible = {
        "title": "测试书", "category": "西方奇幻", "theme": "测试",
        "hard_constraints": ["主角不能说话，台词只能用心声或手势"],
        "characters": [{
            "name": "甲", "role": "protagonist", "appearance": "灰发",
            "personality": "谨慎", "current_state": "逃亡中", "abilities": "低语",
            "growth_arc": {"start": "弱", "end": "强", "current_stage": "逃亡",
                           "milestones": [f"里程碑{i}" for i in range(1, 9)]},
        }],
        "open_threads": [
            {"thread": "符文来历未解释", "planted_chapter": 3, "status": "open"},
            {"thread": "早已兑现的旧债", "planted_chapter": 1, "status": "paid"},
        ],
        "overall_arc": {"beginning": "起", "midpoint": "中", "climax": "高", "resolution": "结"},
    }
    (proj / "bible.json").write_text(json.dumps(bible, ensure_ascii=False), encoding="utf-8")
    (proj / "arc_plan.txt").write_text(
        "逐章提示\n第2章【人物章】：躲进香烛仓，回忆童年\n第3章【感情章】：与幻影对话\n",
        encoding="utf-8")
    (proj / "outline_31_60.txt").write_text("第31章【雪线之上】：遇旧敌\n", encoding="utf-8")
    with (proj / "arc_digests.jsonl").open("w", encoding="utf-8") as f:
        for a in range(1, 11):
            f.write(json.dumps({"arc": a, "start": (a - 1) * 30 + 1, "end": a * 30,
                                "digest": f"第{a}弧提要"}, ensure_ascii=False) + "\n")

    check("通用手艺层会被加载", len(wn.load_universal_craft()) > 2000,
          f"实际 {len(wn.load_universal_craft())} 字")

    rendered = wn.format_bible(bible)
    check("硬约束渲染置顶", rendered.index("绝对不可违反") < rendered.index("角色档案"))
    check("里程碑按上限截断",
          f"里程碑{8}" in rendered and f"里程碑{1}" not in rendered,
          "应只留最近 MAX_MILESTONES_SHOWN 条")

    summaries = [{"chapter": i, "title": "某章", "summary": "摘要" * 40} for i in range(1, 51)]
    pr = wn.build_chapter_prompt(bible, (proj / "arc_plan.txt").read_text(encoding="utf-8"),
                                 summaries, [(49, "第49章 甲\n正文"), (50, "第50章 乙\n正文")],
                                 51, "某提示", proj=proj)
    for key in ("绝对不可违反", "还没兑现的伏笔", "往事提要", "本章类型"):
        check(f"提示词含「{key}」", key in pr)
    check("伏笔只渲染未兑现的", "符文来历未解释" in pr and "早已兑现的旧债" not in pr)
    check("超期伏笔有提醒", "该考虑兑现" in pr)
    check("字数区间进了提示词",
          str(wn.CHAPTER_MIN_CHARS) in pr and str(wn.CHAPTER_MAX_CHARS) in pr)
    check("摘要只展开最近 N 章",
          f"第{50 - wn.FORMAT_SUMMARY_RECENT}章" not in pr and "已压缩入故事圣经" in pr)
    check("弧线提要超限后合并", "第1-" in wn.format_arc_digests(proj))

    for n, kw in ((2, "人物章"), (3, "感情章"), (5, "喘息章")):
        name, instr = wn.get_chapter_type(n)
        check(f"章型带禁止项：{kw}", name == kw and "【本章不要】" in instr, instr[:40])

    arc = (proj / "arc_plan.txt").read_text(encoding="utf-8")
    hint = wn.get_next_chapter_hint(arc, 2, proj=proj)
    check("hint 不含【类型】残渣", "】" not in hint and hint.startswith("躲进"), hint[:30])
    check("手写大纲按区间命中",
          wn.find_outline_file(proj, 31) is not None and wn.find_outline_file(proj, 61) is None)


# ── D 正文清洗 ────────────────────────────────────────────────────────────────

def test_cleaner():
    wn = load("write_novel")
    cases = [
        ("心理——他心想：「别想那些。」", "他心想：「别想那些。」", "剥掉行首标签"),
        ("动作——指甲深陷掌心，掐住那道光痕。", "指甲深陷掌心，掐住那道光痕。", "剥掉行首标签"),
        ("他的心理，早已崩塌，连祷词都念不全。", None, "不误伤正常句子"),
        ("动作要快，他对自己说，再慢半步就被堵死。", None, "不误伤正常句子"),
    ]
    for src, want, label in cases:
        got = wn.strip_technique_labels(src)
        ok = (got == want) if want else (got == src)
        check(f"清洗器：{label}", ok, f"{src!r} → {got!r}")

    meta_cases = [
        ("——有人把我从井里捞起过。（十五字解释他童年被修女救，满足动机补位）", True, "删括号元注释"),
        ("他盯着火盆（节奏减速带），没说话。", True, "删括号里的规则名"),
        ("他想起母亲（那是十年前的事），喉头发紧。", False, "保留正常括号描写"),
        ("塞拉菲妮（赤发灰瞳）推门而入。", False, "保留正常括号描写"),
    ]
    for src, should_change, label in meta_cases:
        got = wn.strip_technique_labels(src)
        check(f"清洗器：{label}", (got != src) == should_change, f"{src[:20]} → {got[:24]}")

    multi = "第一段落写得够长了，不会被当成残行。\n\n血。\n他愣住了。\n\n。\n最后一段也够长。"
    out = wn.strip_technique_labels(multi)
    check("清洗器：保留段间空行", "\n\n" in out)
    check("清洗器：保留超短句段落", all(s in out for s in ("血。", "他愣住了。")))
    check("清洗器：丢掉纯标点残行", "\n。\n" not in out)


# ── E 伏笔台账 ────────────────────────────────────────────────────────────────

def test_threads(tmp: Path):
    wn = load("write_novel")
    check("JSON 摘要解析",
          wn.parse_summary_result('{"summary":"S","new_threads":["A"],"resolved_threads":["B"]}', 1)
          == ("S", ["A"], ["B"]))
    check("带代码围栏也能解析",
          wn.parse_summary_result('```json\n{"summary":"S","new_threads":[],"resolved_threads":[]}\n```', 1)[0] == "S")
    plain = "模型没给 JSON，直接一段文字。"
    check("非 JSON 退回纯文本摘要", wn.parse_summary_result(plain, 1) == (plain, [], []))

    proj = tmp / "novels" / "台账"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "bible.json").write_text(json.dumps(
        {"title": "T", "open_threads": [
            {"thread": "兄长尸体上的符文来历未解释", "planted_chapter": 4, "status": "open"}]},
        ensure_ascii=False), encoding="utf-8")
    added, paid = wn.update_threads(proj, 6, ["守墓骑士的动机未明"], ["兄长尸体上的符文"])
    check("台账：新增与兑现", (added, paid) == (1, 1), f"得到 {(added, paid)}")
    th = json.loads((proj / "bible.json").read_text(encoding="utf-8"))["open_threads"]
    check("台账：兑现是改状态不是删除",
          any(x["status"] == "paid" and x.get("paid_chapter") == 6 for x in th))
    check("台账：重复登记会被去掉",
          wn.update_threads(proj, 7, ["守墓骑士的动机未明"], []) == (0, 0))
    many = [f"碎屑伏笔{i}" for i in range(1, 6)]
    added2, _ = wn.update_threads(proj, 8, many, [])
    check("台账：单章登记有上限",
          added2 == wn.MAX_NEW_THREADS_PER_CHAPTER,
          f"模型报 5 条，登记了 {added2} 条（上限 {wn.MAX_NEW_THREADS_PER_CHAPTER}）")


# ── F 题材解析与诊断器 ────────────────────────────────────────────────────────

def test_genre_and_review():
    import contextlib
    import io

    wn = load("write_novel")
    with contextlib.redirect_stdout(io.StringIO()):
        layer = wn.load_genre_layer("西方奇幻")
    check("题材解析：唯一匹配", len(layer) > 500, f"{len(layer)} 字")
    for bad, label in (("悬疑脑洞", "男女频同名应报错"), ("不存在的题材", "找不到应报错")):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                wn.load_genre_layer(bad)
            check(f"题材解析：{label}", False, "没有报错")
        except SystemExit:
            check(f"题材解析：{label}", True)
    with contextlib.redirect_stdout(io.StringIO()):
        check("题材解析：指明性别可用", len(wn.load_genre_layer("女频/悬疑脑洞")) > 300)

    rc = load("review_chapter")
    check("自检表可解析", len(rc.load_rubric("西方奇幻")) >= 5)

    short = "第3章 标题\n" + "正文。" * 50
    issues = {i["kind"] for i in rc.mechanical_checks(short, 3)}
    check("诊断：抓字数越界", "字数越界" in issues, str(issues))
    bad_title = "第99章 标题\n" + "正文。" * 1000
    check("诊断：抓标题不符", "标题格式" in {i["kind"] for i in rc.mechanical_checks(bad_title, 3)})
    leaked = "第3章 标题\n心理——他心想：「完了」。\n" + "正文。" * 1000
    check("诊断：抓技巧标签", "技巧标签残留" in {i["kind"] for i in rc.mechanical_checks(leaked, 3)})
    longp = "第3章 标题\n" + "很长的一段。" * 60 + "\n" + "正文。" * 900
    check("诊断：抓段落过长", "段落过长" in {i["kind"] for i in rc.mechanical_checks(longp, 3)})

    raw = [
        {"source": "硬约束", "kind": "违反硬约束", "problem": "哑巴说话",
         "evidence": "低声道：「别骗人……安提娅已经埋在北丘。」", "advice": "改心声"},
        {"source": "硬约束", "kind": "违反硬约束", "problem": "哑巴说话",
         "evidence": "「你是谁？」他哑声质问。", "advice": "删去出声"},
        {"source": "一致性", "kind": "设定矛盾", "problem": "多次出声",
         "evidence": "「别骗人……安提娅已经埋在北丘。」/「你是谁？」他哑声质问。", "advice": "全改心声"},
        {"source": "质量", "kind": "节奏变化（6分）", "problem": "节奏紧",
         "evidence": "祈愿厅消失，他站在雪夜墓园", "advice": "加缓冲"},
    ]
    merged = rc.dedupe_items(raw)
    check("诊断：同一处问题合并", len(merged) == 2, f"合并成 {len(merged)} 条")
    check("诊断：合并后标注其他来源", any(m.get("also") for m in merged))


# ── G 配置层 ──────────────────────────────────────────────────────────────────

def test_config(monkey_env):
    nc = load("novel_config")
    for pid, p in nc.PROVIDERS.items():
        check(f"服务商预设完整：{pid}",
              p.get("base_url", "").startswith("http") and bool(p.get("models")))
    monkey_env("NOVEL_MODEL", "env-model")
    check("模型优先级：环境变量胜出", nc.model() == "env-model", nc.model())
    monkey_env("NOVEL_MODEL", None)
    check("模型优先级：回落内置默认", nc.model() == nc.BUILTIN_MODEL)
    monkey_env("OPENAI_API_KEY", None)
    try:
        nc.make_client()
        check("缺 key 时给出明确指引", False, "没有报错")
    except SystemExit as e:
        check("缺 key 时给出明确指引", "setup_api.py" in str(e))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true", help="连通过的项目一起列出")
    args = ap.parse_args()

    import os
    import tempfile

    saved = dict(os.environ)

    def monkey_env(k, v):
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cwd = Path.cwd()
        os.chdir(tmp)                      # 配置文件按 cwd 查找，隔开真实项目
        try:
            test_structure()
            test_premise_template()
            test_prompt_assembly(tmp)
            test_cleaner()
            test_threads(tmp)
            test_genre_and_review()
            test_config(monkey_env)
        finally:
            os.chdir(cwd)
            os.environ.clear()
            os.environ.update(saved)

    if args.verbose:
        for name, _ in PASS:
            print(f"  ✓ {name}")
    for name, detail in FAIL:
        print(f"  ✗ {name}" + (f"  —— {detail}" if detail else ""))
    print(f"\n{len(PASS)} 项通过，{len(FAIL)} 项失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

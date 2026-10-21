# ai-webnovel-pipeline

***English** · [简体中文](./README.zh-CN.md)*

Write a full-length Chinese webnovel with AI, and keep updating it.

You need no corpus of your own, and you do not need the plot worked out in advance. Once it is installed, say "I want to write a novel" in your terminal and it will walk you through pinning down the premise, hand you several opening options to choose from, set up the project, and then write chapter after chapter.

**Not tied to any one tool**: Cline, Roo Code, Aider, Cursor, Codex CLI, Copilot and Claude Code all work (see [below](#installing-into-other-ai-coding-tools)). It also works with no agent at all, straight from the command line — the pipeline is eleven Python scripts plus 37 genre templates, with zero agent dependency.

**The genre rules are reverse-engineered from a 1,110-novel corpus**, not hand-written prompts. See [where the rules come from](#where-the-rules-come-from).

## What problem this solves

Writing one good chapter is easy. The hard part is that **chapter 200 still remembers what chapter 3 planted**. Six hundred thousand characters do not fit in any model's context window, so if you just keep saying "write the next chapter", by chapter twenty the protagonist's voice has drifted and the world rules contradict each other.

The core of this skill is a layered memory:

| Layer | Stored as | What it holds | Refresh rate |
|---|---|---|---|
| Story bible | `bible.json` | World rules, character dossiers, overall arc, plus a set of **lines that must never be crossed** | Every 10 chapters |
| Foreshadowing ledger | `open_threads` in `bible.json` | What has been planted and not yet paid off, and in which chapter | Each chapter |
| Arc digests | `arc_digests.jsonl` | One paragraph per arc — how the distant past is remembered | When an arc completes |
| Arc plan | `arc_plan.txt` | What the current 30 chapters must deliver | When an arc completes |
| Summaries + recent prose | `summaries.jsonl` / `chapters/` | Last 10 summaries + last 2 full chapters | Each chapter |

Only these go into each chapter, which is why writing chapter 300 costs about what chapter 10 did — measured input flattens out after chapter 10 at roughly 20k tokens, and chapter 1,000 is the same number.

**So how long can it actually run?** There is no arithmetic ceiling; the limits are budget and patience. Only the last 10 summaries enter the context, so everything earlier is carried by the bible, the ledger and the arc digests — the ledger is what keeps "the rune from chapter 7" alive at chapter 200 (and flags it once 30 chapters pass unpaid), and the digests are what keep a 200-chapter book from degenerating into one self-contained episode per arc.

Feeding setting text in is not enough on its own: with dozens of facts flattened into one prompt, a model will quietly drop one of them to make the current scene work. So the bible carries a separate `hard_constraints` list — the lines that void a chapter if broken (the protagonist's physical limits, the cost of their advantage, who is already dead, which powers belong to the late arcs). They are rendered first and restated immediately before the model starts writing. This is a measured necessity, not a precaution: with a mute protagonist and that fact left out of the list, he spoke four times in chapter three.

## Where the rules come from

The repository ships **37 genre templates** (19 male-channel, 18 female-channel). They are not ten commandments someone sat down and wrote. The foundation is a **1,110-novel corpus** (570 male-channel, 540 female-channel, stratified by platform rating), with three things run on top of it:

1. **Per-genre criteria, reverse-engineered**: for each genre, the top-rated works in that category were used to derive what the category is actually good at — **370 weighted criteria**, each with explicit anchors for 9–10 and for below 5.
2. **Writing rules grown by self-iteration**: generate an outline plus three chapters under the current rules → a reviewer model scores them against those criteria → an editor model rewrites the rules from the low-scoring dimensions → repeat. Seven genres ran 10–15 rounds, with the best round selected by score.
3. **Genre-independent craft extracted**: auditing one genre's ten rounds of rules line by line showed **81% had nothing to do with the genre** (paragraphs, pacing, dialogue, information release). Those 31 rules were promoted to a shared layer, levelling rule density across all 37 genres.

The analysis is baked into the templates in this repository, so **you need no reference material of your own**. To thicken a genre yourself, use `review_loop.py --no-excerpts` (measured: with imitable excerpts in the seed the loop accumulates 85 characters of rules per round; without them, 744 — a factor of 8.7) and see `references/corpus.md`.

**A limit worth stating plainly**: this class of rubric has high test–retest reliability but weak correlation with real reader behaviour — a separate study of mine ([llm-rubric-validity](https://github.com/XZ-LI/llm-rubric-validity)) measured under 2% of variance explained, with the conclusion reversing when the judge changes. So the value of these rules is that they get prose **reliably up to the craft floor**, not "it scores high, therefore it is good". This project's own A/B agrees: the gap between with-templates and without was +0.22 under the model that also wrote the chapters and +0.06 under an independent judge — too small to claim. What held up was consistency of paragraph shape.

## Installing

Clone the repository anywhere. The path below installs it as a [Claude Code](https://claude.com/claude-code) skill; for any other tool — or none — see [which AI coding tool to install into](#which-ai-coding-tool-to-install-into) below.

```bash
# Personal install: available in every project
git clone https://github.com/XZ-LI/ai-webnovel-pipeline.git ~/.claude/skills/novel-pipeline

# Or scoped to one project
git clone https://github.com/XZ-LI/ai-webnovel-pipeline.git <your-project>/.claude/skills/novel-pipeline
```

Start a new Claude Code session and it appears in the available skills — say "I want to write a novel" to confirm.

To check the install is intact:

```bash
python3 ~/.claude/skills/novel-pipeline/scripts/selftest.py
```

132 checks, no API calls, runs in seconds.

### Which AI coding tool to install into

**Short answer**: the pipeline is command-line scripts; the agent only reads commands out for you, so it is **not tied to any one vendor**. The authoritative guide is `AGENTS.md` at the repository root, and every tool's entry file points back at it.

If you are **in mainland China**, order the choice by whether the whole chain is reachable — the agent and the model are two separate links, and one broken link is enough:

| Tool | The agent link | With DeepSeek configured |
|---|---|---|
| **Qoder** (Alibaba) / **Trae** (ByteDance) | Mainland products, directly reachable | Reachable; Qoder reads `AGENTS.md` natively, so it is the least work to set up |
| **Cline / Roo Code** | Open-source VS Code extension, bring-your-own-key, no vendor server in the path | **Whole chain reachable** |
| **Aider** | Open-source CLI, likewise BYOK | **Whole chain reachable** |
| Tongyi Lingma / CodeBuddy / Comate | Mainland products, directly reachable | Reachable, but usually bound to their own models |
| Claude Code / Cursor / Codex CLI / Copilot | Need an overseas connection | The agent link breaks first |

**Cline and Roo Code are local extensions rather than cloud services** — they hand model calls to the key you configure, so with DeepSeek the entire chain stays inside the mainland. Generate an entry file:

```bash
python3 ~/.claude/skills/novel-pipeline/scripts/install_for_agent.py --list
python3 ~/.claude/skills/novel-pipeline/scripts/install_for_agent.py --agent cline --dest ~/my-novel
```

| Tool | Entry file |
|---|---|
| **Qoder** (Alibaba) | `.qoder/rules/novel-pipeline.md`; it also reads `AGENTS.md` natively |
| **Trae** (ByteDance) | `.trae/rules/project_rules.md` |
| Codex CLI and others reading the common convention | `AGENTS.md` |
| Cline (Roo Code reads this too) | `.clinerules` |
| Cursor | `.cursor/rules/novel-pipeline.mdc` |
| GitHub Copilot | `.github/copilot-instructions.md` |
| Windsurf | `.windsurfrules` |
| Aider | `CONVENTIONS.md` |
| Claude Code | `SKILL.md` (put the repo under `~/.claude/skills/`) |

Each entry file is a thin pointer back to the same `AGENTS.md`, so the content never forks. For a tool not listed, most can be pointed at `AGENTS.md` — follow that tool's own documentation.

**No agent at all also works**: every step below is one command. What an agent adds is judging which step comes next, reading the output back to you, and stopping to ask at the forks.

> Tested end to end only on Claude Code (the 132 self-checks, the 21-query trigger suite, the A/B and the blind evaluation all ran there). Entry files for the other tools follow each vendor's published convention and the scripts themselves are agent-agnostic, but I have not verified each one, and these formats change — check the tool's current docs.

## Setup

**Not sure what you have or how to configure it? Run this first — it lists exactly what is missing:**

```bash
python3 ~/.claude/skills/novel-pipeline/scripts/doctor.py
```

It checks the Python version, the `openai` package, your API key, whether the endpoint answers, and your working directory, and gives a concrete next step for anything that fails. Add `--fix` to let it install the missing package itself.

**If you would rather not deal with it**: paste the whole doctor output to your agent and say "set this up for me". Only signing up and topping up have to be done by you; installing, creating directories and writing the config it can all do on your behalf.

By hand there are only two things:

```bash
python3 --version          # 3.9 or newer
pip install openai
```

> The package name is misleading: **`openai` here is just a generic HTTP client**. DeepSeek, Qwen, Kimi and GLM all speak the same OpenAI-compatible protocol and use this same package. Installing it does not contact OpenAI's servers and does not mean you will be using OpenAI — this project defaults to DeepSeek. (In mainland China, add `-i https://pypi.tuna.tsinghua.edu.cn/simple` if PyPI is slow.)

**Make a dedicated working directory**, say `~/my-novel`, and always open your agent there. Everything is generated relative to the current directory.

### Configuring your own API

Model calls are billed to you, so which provider and which model is your decision. One command sets it and verifies the connection:

```bash
python3 ~/.claude/skills/novel-pipeline/scripts/setup_api.py list     # see supported providers
python3 ~/.claude/skills/novel-pipeline/scripts/setup_api.py set --provider deepseek --key sk-...
```

Or just tell your agent "set up my API, I'm using DeepSeek, the key is …" and it will run the command.

Built-in provider presets (all OpenAI-compatible; model names per each vendor's docs):

| id | Provider | Note |
|---|---|---|
| `openai` | OpenAI | Default; fine for Chinese webnovels but pricey |
| `deepseek` | DeepSeek | Good value for long-form Chinese, a common choice for serials |
| `dashscope` | Alibaba Bailian (Qwen) | Strong Chinese register |
| `moonshot` | Moonshot (Kimi) | Long-context friendly |
| `zhipu` | Zhipu GLM | |
| `ollama` | Local Ollama | Free, but 3,000 characters a chapter is a hard ask of a small local model |

For a provider not listed, an internal endpoint or a proxy, just give the address:

```bash
python3 .../setup_api.py set --base-url https://your-endpoint/v1 --key sk-... --model model-name
```

`set` sends a minimal request to verify, and on failure tells you which thing to fix (model name not valid for this provider / bad key / cannot connect / out of quota each get a different hint). Settings live in `novel.config.json` in the working directory (mode 600, **do not commit it**); `--global` writes to `~/.novel-pipeline/config.json` to share across projects.

Inspect and re-test:

```bash
python3 .../setup_api.py show      # effective config, key masked
python3 .../setup_api.py test      # test only, changes nothing
```

**Precedence**: command-line `--model` > environment (`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `NOVEL_MODEL`) > config file > built-in default. So you can skip the config file entirely and just `export OPENAI_API_KEY=sk-...` for a one-off; to give the reviewer or the genre analysis a stronger model, use `--review-model` / `--analyze-model`.

Only the final "publish to platform" step needs extra packages (`patchright loguru pyyaml`); skip it if you are not publishing.

## How a session goes

Below is the actual flow. You talk normally; your agent runs the commands.

### Step 0 — First time: configure the API

See above. If it is not configured, the first write tells you the three ways to set it rather than failing obscurely.

### Step 1 — You open your mouth

```
You: I want to write a novel
```

It asks three things before writing anything:

```
Agent: Right. Three things first —
       1. Which genre? There are 37. Male-channel examples: western fantasy,
          modern martial arts, eastern cultivation. Female-channel: quick
          transmigration, palace intrigue, CEO romance. If you can't name one,
          describe the story you want and I'll match it.
       2. How much do you already have?
          A — nothing, you decide the plot
          B — a loose idea (one concept, one scene, one character counts)
          C — existing material (character sheets, worldbuilding, outline fragments)
       3. Writing for male-channel or female-channel readers?
```

Question 2 is the important one. All three are supported; the difference is only whether your material is treated as immovable:

- **A — from nothing**: it proposes everything.
- **B — a loose idea**: your exact words are passed through verbatim. "Reborn as the villain's butler, trying to keep the young lady alive" is enough.
- **C — existing material**: hand it the file; the characters, rules and prohibitions you have already fixed will not be altered, it only fills in what you left open.

### Step 2 — It hands you several options

This stage **always produces multiple versions**:

```
Agent: Three premises along the "reborn as the butler" line. Their core
       advantage and central conflict are completely different:

Option 1 — "Shadow Servant"
  Hook: he can hear how she dies three days from now, but each use ages him a year
  Advantage: foresight paid for in lifespan   Conflict: saving her versus destroying himself

Option 2 — "The Second Service"
  Hook: last life he handed her the poisoned cup himself; this time he has to stop
        himself before he does it again
  Advantage: memory of the previous life   Conflict: fighting his own past self

Option 3 — "Article Seven"
  Hook: the butler's code says a servant may not die before his master — that is
        not a rule, it is a binding
  Advantage: an undeath bound by curse   Conflict: the backlash and the fear of not dying

Which one? You can also say "the advantage from #2 with the opening from #1",
or ask for three more.
```

You can pick one, mix them, or say "none of these, give me three more" (it automatically avoids the directions already offered).

**Why summaries first rather than full drafts**: writing three complete "outline plus chapter one" packages means you throw two away. Summaries are cheap; expansion is what costs.

### Step 3 — Expand, set up, check the foundation

Once you pick, it writes the full outline (core premise, character dossier with appearance and growth arc, romance lines, three environment shifts, antagonist, first ten chapters) plus chapter one, sets up the project, and **reads the bible back to you**:

```
Agent: Project created → novels/The Second Service/

       First, the lines that must never be crossed — these are the ones to
       check yourself:
       - Rebirth happens once; dead is dead, no third life
       - Lu Jiu cannot reveal the previous life aloud; speaking it triggers the backlash
       - Shen Yanqiu must not learn he was reborn before chapter 40

       Then characters and world rules:
       - Lu Jiu, 28, missing the tip of his left little finger (she bit it off
         last life when he handed her the cup)
       - Shen Yanqiu appears pliant but knows everything — the biggest departure
         from your original note
       Anything to change?
```

Do not skip this, especially those first lines. The set it picks may not be your book's real red lines — if your protagonist has a physical limit (cannot speak, cannot see, missing a leg) or a power that must stay in the late arcs, add it now and it takes effect from the next chapter.

### Step 4 — Three chapters, then you read them

```
You: write three and let me look
Agent: (runs write_novel.py write --count 3 --genre western-fantasy)
       Three chapters done: 3,100 / 2,980 / 3,050 characters.
       Chapter 3 ends on her realising he knows about the cup.
       Read them: is the protagonist's register right? Is the pace rushed or slack?
```

If something is off, the cost of fixing rises in this order: the arc plan (the next 30 chapters of plot) → the story bible (characters and world rules) → the genre's writing rules. Tell your agent what bothers you and it knows which layer to touch.

**Whenever you change model, change rules, cross an arc boundary or refresh the bible, write one chapter and read it before scaling up.** `--count 20` going wrong is twenty chapters going wrong together.

### Step 4.5 — It diagnoses itself; you decide whether to revise

```
You: take a look at chapter three yourself
Agent: (runs review_chapter.py diagnose)
       Mechanical: 2,900 characters, in band; no craft labels; title format correct.
       Scored against the genre's criteria: hook 8 · suspense 8 · imagery 8 ·
       dialogue 5 · chapter differentiation 6.
       Three findings —

       1. [Hard constraint] The protagonist cannot speak, but he does here
          Quote: "Who are you?" he rasped.
          Fix: make it inner voice or gesture, or have someone else relay it
       2. [Quality] Pacing 6: high-intensity visions stacked with no buffer
       3. [Quality] Differentiation 6: same location and oppressive register as
          the previous two chapters

       Which do you want fixed? None is a valid answer.
You: fix the first one
Agent: (runs revise --fix 1) Done, 2,900 → 2,972 characters.
       His lines are now inner voice and gesture; the events of the chapter are unchanged.
       Original backed up to chapters/.bak/003.txt; this chapter's summary rewritten in step.
```

Two deliberate design points: **diagnosis and revision are separate commands** with your decision in between — auto-revising every finding tends to replace the passages that were working; and **revision rewrites that chapter's summary in step**, because the summary is the factual basis for the hundreds of chapters after it, and a stale summary propagates the error forever.

If you do not like the revision, `restore` puts the original back.

### Step 5 — Keep going

```
You: write ten more
You: where are we?
You: that fight in chapter 44 runs too long, redo it
```

It handles these on its own: refreshing the bible from summaries every 10 chapters, compressing the finished arc into a digest and generating the next arc plan every 30, logging what each chapter plants and ticking off what it pays off, rotating chapter type through combat → character → romance → revelation → breather → reversal (so ten consecutive fights never happen), switching to a secondary viewpoint every 5 chapters, and checking the opening technique of the last two chapters to avoid repeating it.

Two design points worth knowing:

- **The slow chapter types carry prohibitions.** A character chapter explicitly may not contain combat, pursuit or flight; a romance chapter may not advance feeling through escape; a breather chapter may not introduce new pursuers — and the chapter type is declared to **outrank** generic instructions like "every chapter needs conflict". Without that, slow chapters get pushed into action (measured: both the character and romance chapters came back as wounded-and-fleeing).
- **Chapter length is a band, not a floor**: 2,600–3,600 characters. Readers follow a serial chapter by chapter and wild swings break the rhythm; a floor alone drifts (measured: one chapter came in at 4,917).

You can intervene in the ledger: `open_threads` in `bible.json` is the book of debts — add one you want planted, delete one you do not intend to pay off. It only guarantees those debts stay in context; whether to settle them is your call.

**To redo a chapter, the summary has to go back with it** — deleting the prose but leaving the summary means that broken plot gets inherited as established fact. Just say "redo chapter 44" and it knows there are three things to roll back.

### Step 6 — At every 30-chapter fork, you can choose again

Arc boundaries (chapters 31, 61, 91 …) are forks in the plot, and you can ask for multiple versions there too:

```
You: give me three directions for the next arc
Agent: (runs new-arc --variants 3)
       Three plans that differ materially in events and turns:
       v1 returns to the family-revenge line, exposing the patriarch at chapter 45;
       v2 puts her in power and moves the protagonist behind the scenes;
       v3 introduces a third party from the previous life, turning "who handed over
          the cup" into a Rashomon.
       (Nothing touched yet — current plan and progress unchanged. Pick one and I'll apply it.)
```

### Step 7 (optional) — Publish

Mainland Chinese platforms only, and you need your own account with the work already approved in the platform's backend. Fanqie is the best-tested; Qimao works.

```
You: publish what's written to Fanqie, two chapters a day
```

The first run opens a real browser window for you to log in by QR code; then watch one chapter go up to confirm before enabling the daily job. 17K and Tadu are not bundled.

## What gets created in your working directory

```
~/my-novel/
├── premises/<genre>_<timestamp>/   premise options (pitches.md for you, variant_N_premise.txt for the scripts)
├── novels/<title>/
│   ├── bible.json             story bible ← the file worth reading yourself
│   ├── arc_plan.txt           current 30-chapter plan ← edit this to change plot direction
│   ├── summaries.jsonl        per-chapter summaries (only the last 10 enter the context)
│   ├── arc_digests.jsonl      one paragraph per arc — how the distant past is remembered
│   ├── state.json             which chapter you are on
│   ├── chapters/
│   │   ├── 001.txt …          prose; first line is the chapter title
│   │   └── .bak/              pre-revision originals, used by restore
│   ├── arc_candidates/        candidate arc plans (nothing is affected until you pick)
│   └── reviews/               diagnosis reports (ch003_review.md for you, .json for revision)
├── cookies/ state/            publishing sessions and progress (only if you publish)
├── novel.config.json          your API settings (mode 600, do not commit)
```

Chapter files have a fixed shape: **first line is the title, prose starts after a blank line**. The publishing end relies on it.

## What it costs

The built-in default is `deepseek-chat` on DeepSeek's endpoint — chosen because this writes long-form Chinese and most of its users are in mainland China, where `api.openai.com` is unreachable; a default that cannot connect strands a new user on their first run. At 2,600–3,600 characters a chapter with the full memory context (about 20k input tokens, and it does not grow with chapter count). To estimate, have your agent check status:

```
You: roughly what does 100 chapters cost?
Agent: (runs status and reads the measured input tokens) About 5,600 input plus
       4,000 output per chapter; here is the arithmetic for 100.
```

Summaries grow linearly with chapter count (about 75 tokens each), so chapter 200 is marginally dearer than chapter 10, but it does not run away. For cheaper runs, switch provider: `setup_api.py set --provider deepseek --key ...` and every script follows, with no need to pass a model name on each command.

## Things it will not do for you

- **It will not decide the direction of your story.** Character fates and endings belong to the author; its job is to write each chapter well inside the frame you set, and to tell you when the frame starts contradicting itself.
- **It will not revise your draft unsupervised.** Diagnose freely, but which findings get acted on is your call; the original is backed up and `restore` puts it back.
- **It will not create the work on a platform.** You register and clear the title yourself; it only posts chapters.
- **It will not scrape anyone's work for you.** Deriving rules from your own chosen exemplars is an advanced path (see `references/corpus.md`); sourcing and rights are your responsibility.

## Going deeper

Your agent reads these as needed; you can also just open them:

| File | Contents |
|---|---|
| `references/from-scratch.md` | Full walkthrough from nothing to a running serial |
| `references/genres.md` | How the 37 genre templates work, the shared craft layer, and how to add your own rules |
| `references/method.md` | The design of the memory layers, the foreshadowing ledger and arc digests, how to roll back a bad chapter, symptom → which layer to fix |
| `references/reviewing.md` | Chapter diagnosis and revision: what is checked, how to pick findings, where revision stops |
| `references/setup.md` | Environment, switching models, cost estimation, common startup problems |
| `references/corpus.md` | Advanced: build your own reference corpus and iterate a genre's rules |
| `references/publishing.md` | Platform configuration and the per-site pitfalls |
| `references/testing.md` | How to test this skill: regression self-check, trigger testing, behavioural checklist, quality A/B |
| `assets/templates/INDEX.md` | All 37 genres with maturity grades |

To add your own rules to a genre (tropes you do not want, platform red lines, a pacing pattern you have worked out), write them into `assets/prompts/<male|female>/<genre>.author.txt` — that file is never overwritten by a rebuild.

# ai-webnovel-pipeline

***English** · [简体中文](./INTRO.zh-CN.md)*

**Write a full-length Chinese webnovel with AI, and keep updating it.**

Cline, Roo Code, Aider, Cursor, Codex CLI, Copilot and Claude Code all work, and so does no agent at all, straight from the command line — **the pipeline itself is eleven Python scripts and 37 genre templates, with zero agent dependency**. In mainland China the whole chain stays reachable with Cline or Roo Code on DeepSeek, which is also the built-in default.

## The problem it solves

Writing one good chapter is easy. The hard part is that **chapter 200 still remembers what chapter 3 planted**. Six hundred thousand characters do not fit in any model's context window, so if you just keep telling an AI to "write the next chapter", by chapter twenty the protagonist's voice has drifted, the world rules contradict each other, and nothing you planted is ever paid off.

That is the problem this pipeline exists for.

## How it works

A layered memory. Each chapter is written from a few of these, not from the whole book:

| Layer | What it holds | Refresh rate |
|---|---|---|
| Hard constraints | Lines that void a chapter if broken (the protagonist is mute; the dead stay dead; this power belongs to the late arcs) | Set by the author, rarely changes |
| Story bible | World rules, character dossiers, overall arc | Auto-refreshed every 10 chapters |
| Foreshadowing ledger | What has been planted and not yet paid off, and in which chapter | Logged automatically each chapter |
| Arc digests | One paragraph per 30-chapter arc — how the distant past is remembered | Compressed when an arc completes |
| Arc plan | What the current 30 chapters must deliver | Replaced when an arc completes |
| Summaries + recent prose | Last 10 chapter summaries + last 2 full chapters | Appended each chapter |

Measured with tiktoken: **input stops growing after chapter 10** — about 20k tokens whether you are writing chapter 50 or chapter 1,000. There is no arithmetic ceiling on length; the limits are your budget and your patience.

## The genre rules are reverse-engineered from a corpus, not hand-written

This is the substantive difference between this project and "a really good prompt".

The foundation is a **1,110-novel corpus** (570 male-channel, 540 female-channel, stratified by platform rating). Three things were run on top of it:

1. **Per-genre criteria, reverse-engineered.** For each of 37 genres, the top-rated works in that category were used to derive what the category is actually good at — producing **370 weighted criteria**, each with explicit anchors for what scores 9–10 and what scores below 5. Not ten commandments someone sat down and wrote; dimensions pulled out of works readers actually rated highly.
2. **Writing rules grown by self-iteration.** The loop: generate an outline plus three chapters under the current rules → a reviewer model scores them against those derived criteria, dimension by dimension → an editor model rewrites the rules from the low-scoring dimensions → repeat. Seven genres ran 10–15 rounds; the best round was selected by review score.
3. **Genre-independent craft extracted.** Auditing one genre's ten rounds of accumulated rules line by line showed **81% of them had nothing to do with the genre** — how to break paragraphs, how to mix sentence lengths, how to interrupt dialogue, how to meter out information. Those 31 rules were promoted to a shared layer, levelling rule density across all 37 genres.

One useful causal finding came out of that process: **when the seed prompt carries imitable excerpts from real works, the loop accumulates 85 characters of articulated rules per round; strip the excerpts out and force it to state what it observed, and it accumulates 744 — a factor of 8.7.** Rules are the part that can ship with a project (excerpts are someone else's work), so that became a `--no-excerpts` switch anyone can use to thicken any genre.

**So the user needs no corpus of their own** — the analysis of those 1,110 novels is already baked into the 37 templates in this repository.

### A limit worth stating plainly

A separate study of mine ([llm-rubric-validity](https://github.com/XZ-LI/llm-rubric-validity)) measured the validity of exactly this kind of rubric: test–retest reliability is high (ICC = .87), but they explain **under 2% of reader-behaviour variance**, and changing the judge reverses the conclusion. So the value of these rules is that they get prose **reliably up to the craft floor of the industry** — consistent paragraph shape, dialogue that differentiates by station, metered information release, chapter endings that hold the reader — not "it scores high, therefore it is good".

This project's own A/B says the same. The score gap between with-templates and without was +0.22 under the model that also wrote the chapters, and +0.06 under an independent judge — too small to claim, and the docs say so. What did hold up was paragraph-shape consistency across chapters: with templates, mean paragraph length across three chapters was 19.7–20.0 characters; without, it swung between 10.4 and 33.2.

## What else is in the box

**Multiple options at every outline stage.** Project premises and each 30-chapter arc plan are generated as several distinct options for the author to choose from — one option only lets them say yes or no; three side by side lets them say "the ability from #2 with the opening from #1", which is the information you actually need.

**Diagnosis and revision.** After a chapter is written it can audit itself: first a free mechanical pass (length out of band, craft labels leaking into prose, over-long paragraphs, opening technique repeating the previous chapter), then a scored pass against the genre's own criteria plus a check of every hard constraint — every finding must quote the offending line. Whether to revise, and which findings to act on, is the author's call. Revision backs up the original and rewrites that chapter's summary in step.

**Optional publishing.** Browser automation for two Chinese platforms (Fanqie, Qimao), with daily scheduling on Beijing time.

## Engineering choices

- **Agent-agnostic.** The authoritative guide is a single `AGENTS.md` (the emerging cross-agent convention); the `SKILL.md` that Claude Code reads is the same content plus a frontmatter block. `scripts/install_for_agent.py` generates entry files in each tool's own format for Cursor / Cline / Copilot / Windsurf / Aider, all pointing back at that one guide, so the content never forks. Across eleven scripts there is not one Claude or Anthropic dependency — standard library plus `openai`.
- **Model-agnostic.** Six built-in provider presets (OpenAI, DeepSeek, Qwen, Kimi, GLM, local Ollama) and any OpenAI-compatible `--base-url`. One command sets it and verifies the connection on the spot.
- **Human in the loop.** Diagnosis and revision are two separate commands with the author's decision in between. It does not revise on its own, and it does not decide the direction of the story.
- **132 regression checks**, no API calls, runs in seconds — prompt assembly, prose cleaning, the foreshadowing ledger, genre resolution, config precedence, and whether the two guides have drifted apart.
- **The testing method is documented too**: trigger testing (does the skill even get picked up), a behavioural checklist, quality A/B and blind evaluation — including what each class of test can and cannot tell you.

## One honest boundary

All 132 checks green, an 8-out-of-10 diagnosis and a clean behavioural checklist still permit a novel nobody wants to read. What this guarantees is the absence of technical accidents — no contradicted world rules, no mute protagonist suddenly speaking, no runaway chapter lengths, no forgotten setups. It does not guarantee the book is good. That part comes from the premise the author sets, the option they pick, and what they say after reading.

For usage, see [README.md](README.md).

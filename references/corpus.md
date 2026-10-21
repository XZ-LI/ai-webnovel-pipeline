# 进阶：自建参考语料库，把某个题材从 B 级升到 A 级

**先说清楚：绝大多数情况不需要做这一步。** 自带的 37 个题材模版已经把语料库学到的东西固化进来了（见 `genres.md`），直接 `--genre` 就能写。

需要走这条路的只有三种情况：

1. 用户要写的题材不在那 37 个里（冷门品类、海外平台、非网文体裁）；
2. 用户想按**自己选的标杆作品**反推规则，而不是用内置的；
3. 用户要把某个 B 级题材升到 A 级（让规则真正经过"生成→评审→改写"的迭代）。

`$SK` = 本 skill 的 `scripts/` 目录。

## 语料库长什么样

```
<工作目录>/corpus/男频/<题材>/*.txt      # 每本一个 txt
<工作目录>/corpus/女频/<题材>/*.txt
```

这也是脚本的默认路径（`corpus/男频`，女频题材自动切到 `corpus/女频`），`--source-dir` / `--style-dir` 可以指到别处。男女频分开放是必要的——悬疑脑洞、科幻末世、游戏体育这三个题材名两边都有。

每个 txt 的**前 500 字内**要有一行 `评分：9.5` 这样的标记——脚本按这个分数排序取高分书，没有标记的按 0 分处理，基本不会被选中。章节标题写成 `第1章 标题`（行首、数字、空格），风格采样靠它定位到对应章号。

来源由用户自己负责：他自己写的作品、公开可获取的文本、或他有权使用的素材。**不要替用户去抓取受版权保护的商业作品**；要不要、怎么拿是他的决定和他的责任。

## 用法一：风格参考（最轻，不额外花钱）

写作时让模型看同题材高分作品的邻近章节，只学节奏笔法：

```bash
python3 $SK/write_novel.py write --project "<书名>" --genre 西方奇幻 \
    --style-dir corpus/男频
```

命中条件有两个，缺一个就静默没有风格样本（不报错）：
- `corpus/男频/<分类>/`（或你用 `--style-dir` 指定的目录下的 `<分类>/`）存在；
- 这个 `<分类>` 和 `novels/<书名>/bible.json` 里的 `category` 字段**完全一致**。

bible 的 `category` 常常是 `穿书·修仙·女强·系统` 这种组合标签，和目录名对不上——想用风格参考就把它改成和目录同名。单章风格样本上限 3000 字符，再多会挤掉近期原文。

## 用法二：反推题材套路（中等成本）

```bash
python3 $SK/generate_novel.py --category <题材> --analyze     # 先反推套路存报告，再生成开篇
python3 $SK/generate_novel.py --category <题材> --learn       # 读全部简介 + Top-10 开篇 + 结尾钩子
```

`--analyze` 会留下 `generated/<题材>/genre_report.txt`，这份报告本身就能当立项参考读。

## 用法三：review_loop 迭代出写作细则（最贵，也是 A 级的来路）

**先加 `--no-excerpts`。** 这个开关决定了跑完能不能拿到可分发的规则，实测差 8.7 倍：

| 跑法 | 种子 | 每轮攒下的规则 | 最高分 |
|---|---|---|---|
| 种子带原著节选 | 5655 字 | **85 字/轮** | 7.6（10 轮） |
| 种子不带节选 | 703 字 | **744 字/轮** | 7.5（2 轮） |

原因是：种子里塞了高分作品的正文节选，模型有东西可以直接模仿，就不必把"这个品类好在哪里"写成明文规则；把节选撤掉，它被迫把观察写成规则。而**规则才是能随 skill 分发的东西**——节选是别人的作品，打包前必须剥掉，一剥就什么都不剩（这正是 B 级题材只有评分维度、没有写作细则的由来）。

按 744 字/轮算，任一题材跑 6 轮能攒出约 4400 字规则，和内置的西方奇幻同量级。

```bash
python3 $SK/review_loop.py --category <题材> --source-dir corpus/男频 --no-excerpts
python3 $SK/review_loop.py --category <题材> --max-iter 3 --target 8.5
python3 $SK/review_loop.py --category <题材> --resume          # 接着上次跑
python3 $SK/review_loop.py --category <题材> --learn-only      # 只推评分细则，不生成
```

每轮做四件事：从高分源书反推评分细则（首轮后缓存）→ 用当前提示词生成大纲 + 3 章 → 评审模型逐项打分 → 编辑模型据低分项改写提示词。默认目标 9.0 分、写手和评审都用 `o3`。

产出在 `review_loop/<题材>/`：`rubrics.json`、`iter_N/{system_prompt.txt,novel.txt,evaluation.json}`、`summary.json`（含 `best_iteration` / `best_score`）。

**挑最优轮，不是最后一轮。** 分数会上下波动（内置的 7 个题材里，最优轮分别落在第 3、5、6、7、8 轮）。`summary.json` 的 `best_iteration` 就是答案。

用迭代出来的提示词：

```bash
python3 $SK/write_novel.py write --project "<书名>" \
    --system-prompt review_loop/<题材>/iter_<best>/system_prompt.txt
```

注意 `--system-prompt` 是**整套替换**默认写作规范，而 review_loop 产出的提示词里本来就带了一份通用规则，所以不要同时再 `--genre` 同一个题材（会重复啰嗦）。

## 固化回 skill

跑出满意的结果后，把它变成随 skill 分发的模版：

```bash
python3 $SK/build_templates.py --repo <你的工作目录>
```

工作目录里要有 `review_loop/`（以及可选的 `generate_novel*.py`，没有就用 skill 自带的）。该题材会自动升级标注成"已迭代 N 轮，采用第 M 轮（评审分 X）"。

**要分发给别人就保留默认的 `--quotes strip`**：评分细则里常会引用真实商业作品的原文片段做示例，默认会剥掉引文只留判分标准。自己本机想看引文用 `--quotes keep`，但别把那样生成的 assets 发出去。

固化回 skill 时注意：`build_templates.py` 会自动跳过已经提升到通用手艺层的那 31 条规则（见 `genres.md`），所以题材层里留下的是真正题材专属的部分，不会和共享层重复。

如果只是想给某个题材补几条自己的规则，不必跑 review_loop——直接写 `assets/prompts/<性别>/<题材>.author.txt`，见 `genres.md`。

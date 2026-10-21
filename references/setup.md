# 环境与成本

## 依赖

```bash
python3 --version        # 3.9 或更新
pip install openai
```

没别的依赖了——脚本只用标准库 + `openai`。（发布到平台那条可选支线需要浏览器自动化，见 `publishing.md`。）

## 配 API（第一次必做）

模型调用花的是用户自己的钱，服务商和模型由他定。**用户还没配时不要替他 export 一个 key，也不要猜模型名**——跑这个：

```bash
python3 $SK/setup_api.py list                                   # 支持哪些服务商
python3 $SK/setup_api.py set --provider deepseek --key sk-...    # 设置并当场测通
python3 $SK/setup_api.py set --base-url https://.../v1 --key sk-... --model 模型名   # 自定义接口
python3 $SK/setup_api.py show                                   # 当前生效（key 打码）
python3 $SK/setup_api.py test                                   # 只测不改
```

内置预设：`openai` / `deepseek` / `dashscope`（通义千问）/ `moonshot`（Kimi）/ `zhipu`（GLM）/ `ollama`（本地）。模型名以各家文档为准，错了接口会直接报 model not found。

`set` 会发一个最小请求验证，并按失败原因给提示（模型名对不上 / key 不对 / 连不上 / 余额不足）。配置写在工作目录的 `novel.config.json`（权限 600），`--global` 则写 `~/.novel-pipeline/config.json`。

**配置文件里有 key**：不要把它打印给用户看、不要提交到 git、不要复制到别处。要确认配置就用 `show`，它只给打码后的。

## 取值优先级

```
命令行 --model  >  环境变量  >  配置文件  >  内置默认（o3）
```

环境变量有三组：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`NOVEL_MODEL`（以及 `NOVEL_REVIEW_MODEL` / `NOVEL_ANALYZE_MODEL`）。所以临时试别的模型不用改配置，`NOVEL_MODEL=xxx python3 ...` 就行。

配好之后**所有脚本的 `--model` 默认值都跟着变**，不用在每条命令上手动带。评审和题材分析想用更强的模型，就在 `set` 时加 `--review-model` / `--analyze-model`。

## 工作目录

所有脚本都用**相对路径**，在哪个目录跑就在哪个目录建文件：

```
<工作目录>/
├── novels/<书名>/        ← write_novel.py 的项目（圣经、弧线、摘要、正文）
├── generated/<分类>/     ← generate_novel.py 的开篇产出
├── generated_female/     ← generate_novel_female.py 的开篇产出
├── premises/<题材>_<时间>/ ← gen_premise.py 的立项方案
├── review_loop/<分类>/   ← review_loop.py 的迭代产出（可选）
└── novel.config.json     ← API 设置（含明文 key，权限 600，别进 git）
```

给用户建一个专用目录（比如 `~/my-novel`）并提醒他固定在那里跑，否则项目会散落在各处，`--project` 自动识别也会失效。

## 换模型 / 换服务商

长期换就改配置（一次设置，全局生效）：

```bash
python3 $SK/setup_api.py set --provider deepseek --key sk-... --model deepseek-chat
```

单次换就带参数：`--model <模型名>`；临时试就 `NOVEL_MODEL=<模型名> python3 ...`。

换服务商时**模型名必须跟着换**成那家的名字，否则报 model not found。写中文网文用国产模型通常便宜一个数量级，长篇连载值得先试。

不要去改脚本里的模型常量——脚本是本 skill 自带的副本，改了以后别人和你会读到不一致的行为，而且下次更新 skill 就丢了。

## 估成本

```bash
python3 $SK/write_novel.py status --project "<书名>"
```

它会**实际构造一次提示词再量**（有 tiktoken 就用它，没装就按 0.9 token/字），分两项报：写作规范（通用 + 题材层，约 5,300 token）和正文提示（圣经 + 伏笔台账 + 弧线提要 + 弧线计划 + 摘要 + 近期原文）。按"每章 ≈ 输入 + 4k 输出"估算，乘章数再乘单价。

**要带 `--genre`**，不然算不进题材规则层那 5,300 token，报出来的数会偏小三成。

用户说"先写 100 章"的时候，先把这个数算给他看。好消息是**这个数不随章数增长**：第 10 章之后持平在 2 万 token 左右，第 1000 章也是这个数（摘要只展开最近 10 章，更早的靠圣经、伏笔台账、弧线提要三层承载，见 `method.md`）。所以 100 章的账就是单章的 100 倍，不用担心后期越写越贵。

## 常见启动问题

| 现象 | 原因 |
|---|---|
| `[ERROR] 没有 API key` | 还没配。跑 `setup_api.py set`，或 export 环境变量 |
| `model not found` / 404 | 服务商和模型名对不上。`setup_api.py list` 看这家常见的模型名 |
| 401 / unauthorized | key 不对、不完整，或不是这个服务商的 key |
| 连接超时 / 无法解析 | `base_url` 写错（通常以 `/v1` 结尾）或网络到不了 |
| `[warn] ...无小说文件，退回标题模式` | 没有参考语料库。**正常**，不影响跑通，见 `corpus.md` |
| 风格参考完全没生效 | `--style-dir` 目录名或 `bible.json` 的 `category` 对不上，见 `method.md` 末节 |
| 项目找不到 / `--project` 识别失败 | 不在建项目时的那个工作目录里跑 |
| 速率限制 / TPM 超限 | 降低 `--count` 分批跑，或换上限更高的模型 |

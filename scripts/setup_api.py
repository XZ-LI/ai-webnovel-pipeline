#!/usr/bin/env python3
"""
setup_api.py — 设置你自己的 API：服务商、key、默认模型，并当场测通。

写小说这件事要连着调几百次接口，所以值得先把这一步弄对：模型名和服务商对不上，
会在第一次真正写章节时才报 model not found，白等。

用法：
  python3 setup_api.py list                                  # 支持哪些服务商
  python3 setup_api.py set --provider deepseek --key sk-...  # 设置并自动测通
  python3 setup_api.py set --provider dashscope --key sk-... --model qwen-max
  python3 setup_api.py set --base-url https://xxx/v1 --key sk-... --model mymodel   # 自定义接口
  python3 setup_api.py show                                  # 当前生效的配置（key 打码）
  python3 setup_api.py test [--model ...]                    # 只测不改

配置默认写在当前目录的 novel.config.json（跟着这本书走）；
加 --global 写到 ~/.novel-pipeline/config.json（所有项目共用）。
文件里有 key，权限会设成仅本人可读；**不要把它提交到 git**。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import novel_config as nc  # noqa: E402


def cmd_list() -> int:
    print("OpenAI 兼容的服务商（模型名以各家文档为准）：\n")
    for pid, p in nc.PROVIDERS.items():
        print(f"  {pid:<10} {p['name']}")
        print(f"             接口 {p['base_url']}")
        print(f"             常见模型 {', '.join(p['models'])}")
        if p["note"]:
            print(f"             {p['note']}")
        print()
    print("设置：python3 setup_api.py set --provider <上面的 id> --key <你的key> [--model <模型>]")
    print("自定义接口（上面没有的服务商、公司内网、代理）：")
    print("  python3 setup_api.py set --base-url https://.../v1 --key <key> --model <模型>")
    return 0


def cmd_show() -> int:
    print(f"当前生效：{nc.describe()}")
    p = nc.config_path()
    print(f"配置文件：{p}" + ("" if p.exists() else "（还没有，正在用环境变量或默认值）"))
    cfg = nc.load()
    if cfg:
        shown = {k: ("…" if k == "api_key" else v) for k, v in cfg.items()}
        print(f"文件内容：{shown}")
    for role in ("review", "analyze"):
        if nc.model(role) != nc.model():
            print(f"{role} 模型：{nc.model(role)}")
    return 0


def run_test(model_name: str) -> bool:
    print(f"测试接口（模型 {model_name}）...", end=" ", flush=True)
    try:
        client = nc.make_client()
        r = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "回答两个字：收到"}],
        )
        print(f"通了。返回：{(r.choices[0].message.content or '').strip()[:20]}")
        return True
    except SystemExit as e:
        print(f"\n{e}")
        return False
    except Exception as e:
        msg = str(e)
        print("失败。")
        print(f"  {msg[:300]}")
        low = msg.lower()
        if "model" in low and ("not found" in low or "does not exist" in low or "无效" in msg):
            print("  → 模型名和这个服务商对不上。用 `setup_api.py list` 看这家常见的模型名。")
        elif any(k in low for k in ("auth", "api key", "401", "invalid_api_key", "unauthorized")):
            print("  → key 不对或没权限。确认复制完整、且是这个服务商的 key。")
        elif any(k in low for k in ("connect", "timeout", "resolve", "ssl")):
            print("  → 连不上这个地址。确认 --base-url 写对（通常以 /v1 结尾），以及网络能到。")
        elif "quota" in low or "balance" in low or "余额" in msg:
            print("  → 账户额度或余额不足。")
        return False


def cmd_set(args) -> int:
    cfg = nc.load() if (args.keep or nc.config_path().exists()) else {}

    if args.provider:
        if args.provider not in nc.PROVIDERS:
            print(f"[ERROR] 不认识的服务商 '{args.provider}'。可用：{', '.join(nc.PROVIDERS)}")
            print("自定义接口请用 --base-url。")
            return 1
        p = nc.PROVIDERS[args.provider]
        cfg["provider"] = args.provider
        cfg["base_url"] = p["base_url"]
        cfg.setdefault("model", p["models"][0])
    if args.base_url:
        cfg["base_url"] = args.base_url
        cfg.pop("provider", None)
    if args.key:
        cfg["api_key"] = args.key
    if args.model:
        cfg["model"] = args.model
    if args.review_model:
        cfg["review_model"] = args.review_model
    if args.analyze_model:
        cfg["analyze_model"] = args.analyze_model

    if not cfg.get("api_key"):
        print("[ERROR] 还没有 key。加上 --key <你的key>。")
        return 1
    if cfg.get("base_url", "").startswith("https://api.openai.com") and cfg.get("model") is None:
        cfg["model"] = nc.BUILTIN_MODEL

    path = nc.save(cfg, scope="global" if getattr(args, "global_scope", False) else "local")
    print(f"已写入 {path}（权限 600，别提交到 git）")
    print(f"生效配置：{nc.describe()}")

    if args.no_test:
        print("跳过联通测试（--no-test）。第一次真正写章节时才会知道对不对。")
        return 0
    ok = run_test(cfg.get("model") or nc.BUILTIN_MODEL)
    if ok:
        print("\n可以开始了。接下来：让 Claude 帮你选题材、出几个开局方案。")
        return 0
    print("\n配置已经写下了，但没测通。改一处再试：")
    print(f"  python3 setup_api.py set --model <别的模型名>   # 只改模型")
    print(f"  python3 setup_api.py set --key <重新粘贴的key>  # 只改 key")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出支持的服务商与常见模型名")
    sub.add_parser("show", help="显示当前生效的配置（key 打码）")

    s = sub.add_parser("set", help="设置服务商/key/模型，并测通")
    s.add_argument("--provider", help="服务商 id，见 list")
    s.add_argument("--base-url", help="自定义 OpenAI 兼容接口地址（通常以 /v1 结尾）")
    s.add_argument("--key", help="你的 API key")
    s.add_argument("--model", help="默认模型")
    s.add_argument("--review-model", help="评审/打分用的模型（默认同上）")
    s.add_argument("--analyze-model", help="题材分析用的模型（默认同上）")
    s.add_argument("--global", dest="global_scope", action="store_true",
                   help="写到 ~/.novel-pipeline/config.json，所有项目共用")
    s.add_argument("--keep", action="store_true", help="保留原有字段，只改这次给的")
    s.add_argument("--no-test", action="store_true", help="不做联通测试")

    t = sub.add_parser("test", help="只测当前配置，不修改")
    t.add_argument("--model", help="临时指定模型")

    args = ap.parse_args()
    if args.command == "list":
        return cmd_list()
    if args.command == "show":
        return cmd_show()
    if args.command == "set":
        return cmd_set(args)
    return 0 if run_test(args.model or nc.model()) else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
novel_config.py — 一处设置，所有脚本生效：API key、服务商地址、默认模型。

优先级（前面的覆盖后面的）：
  1. 命令行参数          --model ...
  2. 环境变量            OPENAI_API_KEY / OPENAI_BASE_URL / NOVEL_MODEL / NOVEL_REVIEW_MODEL
  3. 配置文件            ./novel.config.json  →  ~/.novel-pipeline/config.json
  4. 内置默认            o3（OpenAI 官方接口）

这样三种用法都成立：临时试一下用环境变量，长期用配置文件，单次覆盖用命令行。
配置文件用 `setup_api.py` 写，别手填（它会设成仅本人可读并先测通）。
"""

import json
import os
from pathlib import Path

BUILTIN_MODEL = "o3"
CONFIG_NAME = "novel.config.json"
HOME_CONFIG = Path.home() / ".novel-pipeline" / "config.json"

# OpenAI 兼容的常见服务商。模型名是写这份文件时各家常见的几个，
# 以服务商自己的文档为准——名字错了接口会直接报 model not found，不会静默跑错。
PROVIDERS = {
    "openai": {
        "name": "OpenAI 官方",
        "base_url": "https://api.openai.com/v1",
        "models": ["o3", "gpt-4o", "gpt-4o-mini"],
        "note": "中文网文够用但偏贵。",
    },
    "deepseek": {
        "name": "DeepSeek 深度求索",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "note": "写中文长篇性价比高，长篇连载的常见选择。",
    },
    "dashscope": {
        "name": "阿里云百炼（通义千问，OpenAI 兼容模式）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo"],
        "note": "中文语感好，qwen-max 更稳、qwen-plus 更便宜。",
    },
    "moonshot": {
        "name": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-32k", "moonshot-v1-128k"],
        "note": "长上下文友好。",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4"],
        "note": "",
    },
    "ollama": {
        "name": "本地 Ollama",
        "base_url": "http://localhost:11434/v1",
        "models": ["qwen2.5:14b", "qwen2.5:32b"],
        "note": "不花钱，但 3000 字一章对本地小模型是硬仗，质量要自己掂量。key 随便填。",
    },
}


def config_path() -> Path:
    """项目配置优先，没有就用家目录那份。"""
    local = Path.cwd() / CONFIG_NAME
    if local.exists():
        return local
    return HOME_CONFIG


def load() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] 配置文件读不出来（{p}）：{e}。按环境变量和默认值继续。")
        return {}


def save(data: dict, scope: str = "local") -> Path:
    """写配置。文件里有 key，所以权限设成仅本人可读。"""
    p = (Path.cwd() / CONFIG_NAME) if scope == "local" else HOME_CONFIG
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


def api_key() -> str:
    return os.environ.get("OPENAI_API_KEY") or load().get("api_key") or ""


def base_url() -> str:
    return os.environ.get("OPENAI_BASE_URL") or load().get("base_url") or ""


def model(role: str = "") -> str:
    """role: "" 普通生成 / "review" 评审 / "analyze" 题材分析。缺省逐级回落。"""
    env = {"review": "NOVEL_REVIEW_MODEL", "analyze": "NOVEL_ANALYZE_MODEL"}.get(role)
    if env and os.environ.get(env):
        return os.environ[env]
    if os.environ.get("NOVEL_MODEL"):
        return os.environ["NOVEL_MODEL"]
    cfg = load()
    if role and cfg.get(f"{role}_model"):
        return cfg[f"{role}_model"]
    return cfg.get("model") or BUILTIN_MODEL


def describe() -> str:
    """当前生效的配置，key 打码。用于脚本启动时让人确认钱花在哪。"""
    k = api_key()
    masked = f"{k[:6]}…{k[-4:]}" if len(k) > 12 else ("（已设置）" if k else "（未设置）")
    src = "环境变量" if os.environ.get("OPENAI_API_KEY") else (
        f"配置文件 {config_path()}" if load().get("api_key") else "无")
    url = base_url() or "https://api.openai.com/v1（默认）"
    return f"key {masked}（来自{src}）· 接口 {url} · 模型 {model()}"


def make_client():
    """所有脚本共用的客户端构造。缺 key 时给出可照着做的下一步。"""
    from openai import OpenAI  # 延迟导入，让 --help 在没装 openai 时也能跑

    key = api_key()
    if not key:
        raise SystemExit(
            "[ERROR] 没有 API key。三种设法任选一种：\n"
            "  1) 跑一次向导：python3 scripts/setup_api.py set --provider deepseek --key sk-...\n"
            "  2) 临时用环境变量：export OPENAI_API_KEY=sk-...\n"
            "  3) 看支持哪些服务商：python3 scripts/setup_api.py list"
        )
    kwargs = {"api_key": key}
    if base_url():
        kwargs["base_url"] = base_url()
    return OpenAI(**kwargs)

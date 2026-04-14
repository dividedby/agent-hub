#!/usr/bin/env python3
"""
agent-hub router (forked) — free-tier AI routing for Claude Code Pro users.

Goal: keep all Claude usage within your existing Claude Code Pro subscription.
Every task is routed to the best *free* external provider; Claude acts only
as the orchestrator (which is covered by your Claude Code Pro plan).

Providers (all free-tier, no CC required):
  groq-fast     Groq  / Llama 3.1 8B Instant  — 30 RPM, 14 400 RPD, 500K TPD
  groq-smart    Groq  / Llama 4 Scout 17B      — 30 RPM,  1 000 RPD, 500K TPD
  groq-creative Groq  / Kimi-K2                — 60 RPM,  1 000 RPD, 300K TPD
  cerebras      Cerebras / Llama 3.1 8B        — 30 RPM, 14 400 RPD,   1M TPD
  gemini-flash  Google / Gemini 2.5 Flash      — 10 RPM,    250 RPD, 250K TPM
  gemini-lite   Google / Gemini 2.5 Flash-Lite — 15 RPM,  1 000 RPD, 250K TPM
  mistral-code  Mistral / Codestral            —  2 RPM, ~4M tokens/month
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests
from dotenv import load_dotenv

# -- Paths
SKILL_DIR = Path(__file__).parent
DATA_DIR = Path.home() / ".claude" / "agent-hub"
ENV_FILE = DATA_DIR / ".env"
USAGE_FILE = DATA_DIR / "usage.json"

# -- Provider config (all FREE TIER limits, April 2026)
PROVIDERS: Dict = {
    "groq-fast": {
        "label":       "Groq/Llama3.1-8B",
        "model":       "llama-3.1-8b-instant",
        "base_url":    "https://api.groq.com/openai/v1/chat/completions",
        "env_key":     "GROQ_API_KEY",
        "used_metric": "requests",
        "limit":       14400,
        "window":      "daily",
        "fallback":    "cerebras",
        "api_style":   "openai",
    },
    "groq-smart": {
        "label":       "Groq/Llama4-Scout",
        "model":       "meta-llama/llama-4-scout-17b-16e-instruct",
        "base_url":    "https://api.groq.com/openai/v1/chat/completions",
        "env_key":     "GROQ_API_KEY",
        "used_metric": "requests",
        "limit":       1000,
        "window":      "daily",
        "fallback":    "gemini-flash",
        "api_style":   "openai",
    },
    "groq-creative": {
        "label":       "Groq/Kimi-K2",
        "model":       "moonshotai/kimi-k2-instruct",
        "base_url":    "https://api.groq.com/openai/v1/chat/completions",
        "env_key":     "GROQ_API_KEY",
        "used_metric": "requests",
        "limit":       1000,
        "window":      "daily",
        "fallback":    "gemini-lite",
        "api_style":   "openai",
    },
    "cerebras": {
        "label":       "Cerebras/Llama3.1-8B",
        "model":       "llama3.1-8b",
        "base_url":    "https://api.cerebras.ai/v1/chat/completions",
        "env_key":     "CEREBRAS_API_KEY",
        "used_metric": "tokens",
        "limit":       1000000,
        "window":      "daily",
        "fallback":    "groq-fast",
        "api_style":   "openai",
    },
    "gemini-flash": {
        "label":       "Gemini/2.5-Flash",
        "model":       "gemini-2.5-flash",
        "base_url":    None,
        "env_key":     "GEMINI_API_KEY",
        "used_metric": "requests",
        "limit":       250,
        "window":      "daily",
        "fallback":    "gemini-lite",
        "api_style":   "gemini",
    },
    "gemini-lite": {
        "label":       "Gemini/2.5-Flash-Lite",
        "model":       "gemini-2.5-flash-lite",
        "base_url":    None,
        "env_key":     "GEMINI_API_KEY",
        "used_metric": "requests",
        "limit":       1000,
        "window":      "daily",
        "fallback":    "groq-fast",
        "api_style":   "gemini",
    },
    "mistral-code": {
        "label":       "Mistral/Codestral",
        "model":       "codestral-latest",
        "base_url":    "https://codestral.mistral.ai/v1/chat/completions",
        "env_key":     "MISTRAL_API_KEY",
        "used_metric": "tokens",
        "limit":       4000000,
        "window":      "monthly",
        "fallback":    "groq-smart",
        "api_style":   "openai",
    },
}

TASK_TO_PROVIDER: Dict[str, str] = {
    "code":     "mistral-code",
    "complex":  "groq-smart",
    "research": "gemini-flash",
    "bulk":     "gemini-lite",
    "creative": "groq-creative",
    "fast":     "cerebras",
    "general":  "gemini-lite",
}

REQUIRED_ENV_KEYS = ["GROQ_API_KEY", "CEREBRAS_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY"]
FALLBACK_THRESHOLD = 0.10


# -- Usage state

def _window_start(window: str) -> datetime:
    now = datetime.now(timezone.utc)
    if window == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _init_usage() -> Dict:
    result = {}
    for name, cfg in PROVIDERS.items():
        ws = _window_start(cfg["window"]).isoformat()
        m = cfg["used_metric"]
        result[name] = {f"{m}_used": 0, f"{m}_limit": cfg["limit"],
                        "reset_window": cfg["window"], "last_reset": ws}
    return result


def _save_usage(data: Dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = USAGE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(USAGE_FILE)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _auto_reset(data: Dict) -> Dict:
    changed = False
    for name, cfg in PROVIDERS.items():
        if name not in data:
            data[name] = _init_usage()[name]; changed = True; continue
        raw = data[name].get("last_reset", "1970-01-01T00:00:00+00:00")
        last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        ws = _window_start(cfg["window"])
        if last < ws:
            m = cfg["used_metric"]
            data[name][f"{m}_used"] = 0
            data[name]["last_reset"] = ws.isoformat()
            changed = True
    if changed:
        _save_usage(data)
    return data


def load_usage() -> Dict:
    try:
        raw = USAGE_FILE.read_text()
        data = json.loads(raw)
        for name in PROVIDERS:
            if name not in data:
                raise ValueError(f"missing: {name}")
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        data = _init_usage(); _save_usage(data)
    return _auto_reset(data)


def increment_usage(data: Dict, provider: str, amount: int) -> Dict:
    cfg = PROVIDERS[provider]
    key = f"{cfg['used_metric']}_used"
    data[provider][key] += amount
    _save_usage(data)
    return data


# -- Classification

def classify(task: str) -> str:
    t = task.lower()
    complex_signals = ["architect", "design system", "refactor entire", "design pattern",
        "system design", "explain the codebase", "multi-step", "step by step",
        "walk me through", "performance optimization", "security audit"]
    code_signals = ["code", "function", "class", "debug", "refactor", "bug", "syntax",
        "implement", "def ", "import ", "return ", "error:", "traceback",
        "write a function", "fix this", "what's wrong with", "unit test",
        "dockerfile", "bash script", "shell script"]
    research_signals = ["explain", "summarize", "what is", "how does", "compare", "research",
        "document", "overview", "describe", "analyze", "what are", "why does",
        "pros and cons", "difference between"]
    bulk_signals = ["for each", "batch", "list of", "all of these", "process each",
        "every item", "in bulk", "multiple items", "iterate over"]
    creative_signals = ["story", "creative", "dialogue", "character", "write a story",
        "narrative", "poem", "fiction", "roleplay", "marketing copy", "blog post"]
    fast_signals = ["yes or no", "how many", "when was", "who is", "define ",
        "quick", "one word", "true or false", "convert ", "what time"]

    for s in complex_signals:
        if s in t: return "complex"
    for s in code_signals:
        if s in t: return "code"
    for s in bulk_signals:
        if s in t: return "bulk"
    for s in creative_signals:
        if s in t: return "creative"
    for s in research_signals:
        if s in t: return "research"
    for s in fast_signals:
        if s in t: return "fast"
    return "general"


# -- Provider selection

def _remaining_pct(data: Dict, provider: str) -> float:
    cfg = PROVIDERS[provider]
    used = data[provider][f"{cfg['used_metric']}_used"]
    limit = data[provider][f"{cfg['used_metric']}_limit"]
    return max(0.0, (limit - used) / limit) if limit else 0.0


def _is_available(data: Dict, provider: str) -> bool:
    return _remaining_pct(data, provider) > 0


def _needs_fallback(data: Dict, provider: str) -> bool:
    return _remaining_pct(data, provider) <= FALLBACK_THRESHOLD


def select_provider(data: Dict, task_type: str) -> Tuple[str, Optional[str]]:
    primary = TASK_TO_PROVIDER[task_type]
    fallback = PROVIDERS[primary]["fallback"]

    if _is_available(data, primary) and not _needs_fallback(data, primary):
        return primary, None

    lp = PROVIDERS[primary]["label"]
    lf = PROVIDERS[fallback]["label"]

    if not _is_available(data, primary):
        if _is_available(data, fallback):
            return fallback, f"warning: {lp} exhausted -> {lf}"
        sys.exit(f"[agent-hub] Hard stop: {primary} and {fallback} both exhausted.\n"
                 f"Reset: python3 router.py reset {primary} && python3 router.py reset {fallback}")

    pct = int(_remaining_pct(data, primary) * 100)
    if _is_available(data, fallback):
        return fallback, f"warning: {lp} at {pct}% -> {lf}"
    return primary, f"warning: {lp} at {pct}%, fallback {lf} unavailable"


# -- Status bar

def _fmt(n: int) -> str:
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000: return f"{n//1_000}K"
    return str(n)


def _indicator(data: Dict, provider: str) -> str:
    return "●" if _remaining_pct(data, provider) > 0 else "○"


def format_count(data: Dict, provider: str) -> str:
    cfg = PROVIDERS[provider]
    used = data[provider][f"{cfg['used_metric']}_used"]
    limit = data[provider][f"{cfg['used_metric']}_limit"]
    lstr = f"{limit//1_000_000}M" if limit >= 1_000_000 and limit % 1_000_000 == 0 else str(limit)
    return f"{_fmt(used)}/{lstr}"


def build_status_bar(data: Dict, active_provider: str, warning: Optional[str] = None) -> str:
    ind = _indicator(data, active_provider)
    label = PROVIDERS[active_provider]["label"]
    prefix = f"[{label} {ind}]"
    if warning:
        return f"{prefix} {warning} | Used: {format_count(data, active_provider)}"
    order = ["groq-fast","groq-smart","groq-creative","cerebras","gemini-flash","gemini-lite","mistral-code"]
    short = {"groq-fast":"G/fast","groq-smart":"G/smart","groq-creative":"G/kimi",
             "cerebras":"Cbr","gemini-flash":"Gem/flash","gemini-lite":"Gem/lite","mistral-code":"Mis/code"}
    parts = [f"{short[p]}{_indicator(data,p)}:{format_count(data,p)}" for p in order]
    return f"{prefix}  " + "  ".join(parts)


# -- API calls

def _ensure_env() -> None:
    load_dotenv(ENV_FILE)


def _call_openai_style(provider: str, task: str) -> Tuple[str, int]:
    _ensure_env()
    cfg = PROVIDERS[provider]
    key = os.environ.get(cfg["env_key"], "")
    if not key:
        raise ValueError(f"{cfg['env_key']} not set")
    resp = requests.post(
        cfg["base_url"],
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": cfg["model"], "messages": [{"role": "user", "content": task}]},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    if PROVIDERS[provider]["used_metric"] == "tokens":
        tokens = data.get("usage", {}).get("total_tokens", 0)
        return text, tokens
    return text, 1


def _call_gemini(provider: str, task: str) -> Tuple[str, int]:
    _ensure_env()
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ValueError("GEMINI_API_KEY not set")
    model = PROVIDERS[provider]["model"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    resp = requests.post(url, json={"contents": [{"parts": [{"text": task}]}]}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return text, 1


def _dispatch(provider: str, task: str) -> Tuple[str, int]:
    if PROVIDERS[provider]["api_style"] == "gemini":
        return _call_gemini(provider, task)
    return _call_openai_style(provider, task)


def call_with_retry(provider: str, task: str) -> Tuple[str, int]:
    try:
        return _dispatch(provider, task)
    except requests.exceptions.RequestException:
        time.sleep(2)
        return _dispatch(provider, task)


# -- CLI

def cmd_route(args: argparse.Namespace) -> None:
    task = args.task
    task_type = args.type if args.type else classify(task)
    data = load_usage()
    provider, warning = select_provider(data, task_type)
    try:
        response, increment = call_with_retry(provider, task)
    except ValueError as e:
        fb = PROVIDERS[provider]["fallback"]
        print(f"[agent-hub] Key error ({provider}): {e}. Trying {fb}...", file=sys.stderr)
        try:
            response, increment = call_with_retry(fb, task)
            warning = f"warning: {PROVIDERS[provider]['label']} key missing -> {PROVIDERS[fb]['label']}"
            provider = fb
        except Exception as e2:
            print(f"[agent-hub] Both failed: {e2}", file=sys.stderr); sys.exit(1)
    except requests.exceptions.RequestException as e:
        fb = PROVIDERS[provider]["fallback"]
        print(f"[agent-hub] Network error ({provider}): {e}. Trying {fb}...", file=sys.stderr)
        try:
            response, increment = call_with_retry(fb, task)
            warning = f"warning: {PROVIDERS[provider]['label']} network error -> {PROVIDERS[fb]['label']}"
            provider = fb
        except Exception as e2:
            print(f"[agent-hub] Both failed: {e2}", file=sys.stderr); sys.exit(1)
    data = increment_usage(data, provider, increment)
    bar = build_status_bar(data, provider, warning)
    print(bar, file=sys.stderr)
    print(response)


def cmd_status(args: argparse.Namespace) -> None:
    data = load_usage()
    print("[agent-hub] Free-Tier Usage  (Claude Code Pro covers orchestration; all below is free)")
    print()
    groups = [
        ("Groq (no CC)",     ["groq-fast", "groq-smart", "groq-creative"]),
        ("Cerebras (no CC)", ["cerebras"]),
        ("Google (no CC)",   ["gemini-flash", "gemini-lite"]),
        ("Mistral (no CC)",  ["mistral-code"]),
    ]
    for gname, providers in groups:
        print(f"  {gname}")
        for p in providers:
            cfg = PROVIDERS[p]
            ind = _indicator(data, p)
            pct = int(_remaining_pct(data, p) * 100)
            print(f"    {cfg['label']:<30} {format_count(data, p):>14}  {ind} {pct}% remaining")
        print()


def cmd_reset(args: argparse.Namespace) -> None:
    p = args.provider
    if p not in PROVIDERS:
        print(f"[agent-hub] Unknown: {p}. Options: {', '.join(PROVIDERS)}", file=sys.stderr); sys.exit(1)
    data = load_usage()
    cfg = PROVIDERS[p]; m = cfg["used_metric"]
    data[p][f"{m}_used"] = 0
    data[p]["last_reset"] = _window_start(cfg["window"]).isoformat()
    _save_usage(data)
    print(f"[agent-hub] Reset {p}.")


def cmd_set_key(args: argparse.Namespace) -> None:
    pmap = {"groq": "GROQ_API_KEY", "cerebras": "CEREBRAS_API_KEY",
            "gemini": "GEMINI_API_KEY", "mistral": "MISTRAL_API_KEY"}
    if args.provider not in pmap:
        print(f"[agent-hub] Unknown: {args.provider}. Options: {', '.join(pmap)}", file=sys.stderr); sys.exit(1)
    env_key = pmap[args.provider]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [l for l in (ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else [])
             if not l.startswith(f"{env_key}=")]
    lines.append(f"{env_key}={args.value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)
    print(f"[agent-hub] Set {env_key}.")


def cmd_classify(args: argparse.Namespace) -> None:
    t = classify(args.task)
    p = TASK_TO_PROVIDER[t]
    print(f"type={t}  provider={p}  model={PROVIDERS[p]['model']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="router.py",
                                     description="agent-hub: free-tier AI router for Claude Code Pro")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("route"); p.add_argument("task")
    p.add_argument("--type", choices=list(TASK_TO_PROVIDER), default=None)
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("status"); p.set_defaults(func=cmd_status)

    p = sub.add_parser("reset"); p.add_argument("provider", choices=list(PROVIDERS))
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("set-key")
    p.add_argument("provider", choices=["groq","cerebras","gemini","mistral"])
    p.add_argument("value"); p.set_defaults(func=cmd_set_key)

    p = sub.add_parser("classify"); p.add_argument("task"); p.set_defaults(func=cmd_classify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

# agent-hub (forked) — Free-Tier AI Router for Claude Code Pro

> **Goal**: Keep all Claude usage within your Claude Code Pro subscription.
> Every task routes to the best _free_ external AI provider.
> Claude acts only as the orchestrator — no separate Anthropic API costs.

## What changed from the original

| Area           | Original                                        | This Fork                                                                            |
| -------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------ |
| Providers      | Groq, OpenAI (gpt-4o-mini), Gemini 1.5, MiniMax | Groq, Cerebras, Gemini 2.5, Mistral Codestral                                        |
| Gemini model   | `gemini-1.5-flash` (legacy)                     | `gemini-2.5-flash` + `gemini-2.5-flash-lite`                                         |
| Groq models    | `llama-3.3-70b-versatile` (1K RPD)              | `llama-3.1-8b-instant` (14.4K RPD) + `llama-4-scout` (1K RPD) + `kimi-k2` (creative) |
| Code provider  | OpenAI gpt-4o-mini                              | **Mistral Codestral** (purpose-built for code, free)                                 |
| Fast provider  | Groq                                            | **Cerebras** (~2000 tokens/sec, 1M TPD free)                                         |
| OpenAI         | Required, monthly cap                           | Removed (free tier too limited)                                                      |
| MiniMax        | Required, hard to access                        | Removed (replaced with Cerebras + Kimi-K2)                                           |
| Task types     | 5 (code/research/creative/fast/general)         | 7 (+ `complex` + `bulk`)                                                             |
| Claude routing | Not present                                     | Not needed — Claude Code Pro covers orchestration                                    |
| Hooks          | None                                            | SessionStart (auto-inject status) + PostToolUse (logging)                            |

## Free-Tier Limits (April 2026)

| Provider | Model                | Limit                 | Window  | Notes                                  |
| -------- | -------------------- | --------------------- | ------- | -------------------------------------- |
| Groq     | llama-3.1-8b-instant | 14,400 req/day        | Daily   | Fastest Groq model                     |
| Groq     | llama-4-scout-17b    | 1,000 req/day         | Daily   | Best reasoning on Groq free            |
| Groq     | kimi-k2-instruct     | 1,000 req/day, 60 RPM | Daily   | Strong creative                        |
| Cerebras | llama3.1-8b          | 1M tokens/day         | Daily   | ~2000 t/s, no CC required              |
| Gemini   | 2.5 Flash            | 250 req/day           | Daily   | 1M context window                      |
| Gemini   | 2.5 Flash-Lite       | 1,000 req/day         | Daily   | Best free volume                       |
| Mistral  | Codestral            | ~4M tokens/month      | Monthly | Code-specialized, free experiment tier |

_All providers: no credit card required for the free tier._

## Install

```bash
SKILL_DIR="$HOME/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.5/skills/agent-hub"
mkdir -p "$SKILL_DIR/hooks" "$SKILL_DIR/.claude"

curl -o "$SKILL_DIR/router.py"  https://raw.githubusercontent.com/YOUR_FORK/agent-hub/main/router.py
curl -o "$SKILL_DIR/SKILL.md"   https://raw.githubusercontent.com/YOUR_FORK/agent-hub/main/SKILL.md
curl -o "$SKILL_DIR/hooks/session-start-router.sh" https://raw.githubusercontent.com/YOUR_FORK/agent-hub/main/hooks/session-start-router.sh
curl -o "$SKILL_DIR/hooks/post-tool-log.sh"        https://raw.githubusercontent.com/YOUR_FORK/agent-hub/main/hooks/post-tool-log.sh
curl -o "$SKILL_DIR/.claude/settings.json"         https://raw.githubusercontent.com/YOUR_FORK/agent-hub/main/.claude/settings.json

chmod +x "$SKILL_DIR/hooks/"*.sh
```

## Configure API keys (all free — no CC)

```bash
ROUTER="$HOME/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.5/skills/agent-hub/router.py"

# 1. Groq — https://console.groq.com
python3 $ROUTER set-key groq gsk_...

# 2. Cerebras — https://cloud.cerebras.ai
python3 $ROUTER set-key cerebras csk-...

# 3. Google Gemini — https://aistudio.google.com/apikey
python3 $ROUTER set-key gemini AIza...

# 4. Mistral — https://console.mistral.ai (Experiment tier)
python3 $ROUTER set-key mistral ...
```

Keys are stored in `~/.claude/agent-hub/.env` with `chmod 600` — never in code.

## Task → Provider Routing

| Task Type  | Signals                                    | Provider                | Why                     |
| ---------- | ------------------------------------------ | ----------------------- | ----------------------- |
| `code`     | write/fix/debug/test/Dockerfile            | Mistral Codestral       | Purpose-built for code  |
| `complex`  | architect, system design, explain codebase | Groq / Llama 4 Scout    | Best free reasoning     |
| `research` | explain, summarize, compare, analyze       | Gemini 2.5 Flash        | 1M context, strong      |
| `bulk`     | for each, batch, list of, iterate          | Gemini 2.5 Flash-Lite   | 1000 RPD, high volume   |
| `creative` | story, blog post, marketing copy           | Groq / Kimi-K2          | Strong creative, 60 RPM |
| `fast`     | yes/no, define, convert, lookup            | Cerebras / Llama 3.1 8B | ~2000 t/s, 1M TPD       |
| `general`  | everything else                            | Gemini 2.5 Flash-Lite   | Best free volume        |

## Usage

```bash
ROUTER="$HOME/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.5/skills/agent-hub/router.py"

# Route a task (Claude classifies automatically)
python3 $ROUTER route "write a function to parse JWT tokens"

# Override classification
python3 $ROUTER route "write a function to parse JWT tokens" --type code

# Check how a task would be classified (dry run)
python3 $ROUTER classify "explain how React hooks work"

# View all provider usage
python3 $ROUTER status

# Reset a provider's counter (e.g. after the window resets)
python3 $ROUTER reset gemini-flash
```

## Hooks (Claude Code integration)

The `hooks/` directory contains two Claude Code lifecycle hooks:

- **`session-start-router.sh`** — fires at `SessionStart`; injects current usage
  status into Claude's context automatically so the status bar always shows.
- **`post-tool-log.sh`** — fires at `PostToolUse` for Bash calls; logs every
  `router.py route` call to `~/.claude/agent-hub/route-log.jsonl` for auditing.

Register the hooks by copying `.claude/settings.json` to your project's
`.claude/settings.json` (or merge the `hooks` section into an existing file).

## Dependencies

```
requests
python-dotenv
```

```bash
pip install requests python-dotenv
```

## License

MIT — forked from [LakshmiSravyaVedantham/agent-hub](https://github.com/LakshmiSravyaVedantham/agent-hub)

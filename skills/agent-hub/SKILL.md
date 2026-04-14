---
name: agent-hub
description: >
  Free-tier AI task router for Claude Code Pro users. Routes every task to the
  best free external provider (Groq, Cerebras, Gemini, Mistral Codestral) so
  your Claude usage stays within your Claude Code Pro subscription. Claude acts
  only as the orchestrator — it never makes paid Anthropic API calls.
---

# Agent Hub (Free-Tier Router)

You are the orchestrator. Your job is to classify tasks and route them through
`router.py` to the best **free** external AI provider. You never call Anthropic's
API directly for these tasks — your Claude Code Pro subscription already covers
your reasoning and orchestration. All external calls go to free-tier endpoints.

## Constants

```bash
ROUTER="${CLAUDE_PLUGIN_ROOT}/scripts/router.py"
```

Define `ROUTER` at the top of **every** bash block — shell variables do not persist
between separate Bash tool calls.

---

## Setup (run once per session)

**Step 1 — Verify router.py:**

```bash
ROUTER="${CLAUDE_PLUGIN_ROOT}/scripts/router.py"
python3 "$ROUTER" status || echo "ERROR: router.py not found — reinstall"
```

**Step 2 — Check API keys:**

```bash
python3 -c "
import os; f = os.path.expanduser('~/.claude/agent-hub/.env')
if not os.path.exists(f): print('MISSING: .env not found'); exit()
content = open(f).read()
keys = ['GROQ_API_KEY', 'CEREBRAS_API_KEY', 'GEMINI_API_KEY', 'MISTRAL_API_KEY']
vals = {l.split('=')[0]: l.split('=',1)[1].strip() for l in content.splitlines() if '=' in l and not l.startswith('#')}
missing = [k for k in keys if not vals.get(k)]
print('MISSING:', ', '.join(missing)) if missing else print('ALL KEYS PRESENT')
"
```

For any MISSING key, prompt the user and run:

```bash
ROUTER="${CLAUDE_PLUGIN_ROOT}/scripts/router.py"
python3 "$ROUTER" set-key <provider> <api_key>
# provider: groq | cerebras | gemini | mistral
```

**Step 3 — Show usage:**

```bash
ROUTER="${CLAUDE_PLUGIN_ROOT}/scripts/router.py"
python3 "$ROUTER" status
```

---

## For Every User Message

**Step 1 — Classify the task:**

| Type       | Signals                                                    | Primary Provider      | Why                                          |
| ---------- | ---------------------------------------------------------- | --------------------- | -------------------------------------------- |
| `code`     | write/fix/debug/test/Dockerfile/script                     | Mistral Codestral     | Purpose-built for code; free experiment tier |
| `complex`  | architect, system design, explain codebase, security audit | Groq/Llama4-Scout     | Strong reasoning; 500K TPD free              |
| `research` | explain, summarize, compare, analyze, pros/cons            | Gemini 2.5 Flash      | 1M token context; best for docs              |
| `bulk`     | for each, batch, list of, process multiple                 | Gemini 2.5 Flash-Lite | 1000 RPD; highest free volume                |
| `creative` | story, blog post, marketing copy, narrative                | Groq/Kimi-K2          | Strong creative; 60 RPM free                 |
| `fast`     | yes/no, define, convert, quick lookup                      | Cerebras/Llama3.1-8B  | ~2000 t/s; 1M TPD free                       |
| `general`  | everything else                                            | Gemini 2.5 Flash-Lite | Best all-round free volume                   |

Use the **first** match in table order.

**Step 2 — Route:**

```bash
ROUTER="${CLAUDE_PLUGIN_ROOT}/scripts/router.py"
python3 "$ROUTER" route "<task>" --type <task_type> 2>/tmp/agent-hub-status.txt
```

Read status bar:

```bash
cat /tmp/agent-hub-status.txt
```

**Step 3 — Display:**

- Content of `/tmp/agent-hub-status.txt` as the status bar **before** the response
- Stdout of the route command as the provider response, verbatim

Example:

```
[Gemini/2.5-Flash-Lite ●]  G/fast●:0/14400  G/smart●:0/1000  G/kimi●:0/1000  Cbr●:0/1M  Gem/flash●:0/250  Gem/lite●:1/1000  Mis/code●:0/4M

<provider response here>
```

---

## Key Setup URLs (all free — no credit card required)

| Provider | Sign-up                            | Set-key command                            |
| -------- | ---------------------------------- | ------------------------------------------ |
| Groq     | https://console.groq.com           | `python3 $ROUTER set-key groq gsk_...`     |
| Cerebras | https://cloud.cerebras.ai          | `python3 $ROUTER set-key cerebras csk-...` |
| Gemini   | https://aistudio.google.com/apikey | `python3 $ROUTER set-key gemini AIza...`   |
| Mistral  | https://console.mistral.ai         | `python3 $ROUTER set-key mistral ...`      |

---

## Notes

- `router.py` handles all fallback logic automatically.
- Hard-stop message (both providers exhausted): display verbatim and ask the user.
- Usage check: `python3 "$ROUTER" status`
- Manual reset after window rolls over: `python3 "$ROUTER" reset <provider_id>`
  Provider IDs: `groq-fast` `groq-smart` `groq-creative` `cerebras` `gemini-flash` `gemini-lite` `mistral-code`
- Dry-run classification: `python3 "$ROUTER" classify "<task>"`

# datryxen-claude

Core orchestration agent for Robeul's Datryxen fleet — headless Claude Code (`claude -p`) on the
**Max subscription** (no API key, no per-token bill). Model: `claude-opus-4-8`, 1M context.

> The Claude **Agent SDK** (`@anthropic-ai/claude-agent-sdk`) is **not** used here: as of the
> April/June 2026 policy, the SDK requires a Platform API key and cannot run on subscription OAuth.
> First-party `claude -p` *can* run on the subscription — so the core is a CLI harness, not the SDK.

## Run
```bash
./orchestrate.sh "list open Linear issues, pick the highest priority, propose a plan"
echo "fix the failing build on leads-table and open a PR" | ./orchestrate.sh
```
The brain lives in `CLAUDE.md` (auto-loaded from this dir): classify → route → execute → report, terse.

## Auth
Uses the cached Max credentials in `~/.claude/.credentials.json`. Keep `ANTHROPIC_API_KEY` **unset**
(the runner warns if it's set — it would shadow the subscription and bill the API account).

## Next layers (not built yet)
- **MCP fleet** — drop a `.mcp.json` (gitignored) wiring Coolify / Linear / Sentry / Vercel / n8n /
  Notion / Context7 with tokens from env. The runner picks it up automatically.
- **Sub-agents per brain** — add one definition per brain to `agents/agents.json`; the runner passes
  it via `--agents`. The core routes domain tasks to these and synthesizes their results.

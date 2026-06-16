# Behavioral model — how Robeul actually works with Claude

This folder replaces prose profiles (`ROBEUL-PROFILE.md`, `WINS-AND-FAILS.md`) with a
**typed, evidence-backed model** mined from the real corpus. Every parameter carries a
count and/or a real (redacted) quote — no abstract guessing. That was the whole point:
prior "brain modeling" produced prose nothing was wired to. This is data.

## Files
| File | What | Committed? |
|---|---|---|
| `behavioral-model.json` | **The model.** Voice decode, ranked failure modes (each with evidence + the rule), domain-ownership map, tool profile, do/don't contract. | yes |
| `behavioral-model.v0.json` | Raw session-level signal counts the model is built from. | yes |
| `correction-taxonomy.v0.json` | 95 real corrections bucketed by failure mode, one redacted example each. | yes |
| `mine_corpus.py` | Phase-0 miner: session-level signals over all transcripts + vault chats. | yes |
| `extract_pairs.py` | Phase-1 miner: trigger→correction→recovery triples from strong/anger turns. | yes |
| `~/brain-corpus/model/conversations.jsonl` | Per-session records. | no (raw) |
| `~/brain-corpus/model/correction_pairs.jsonl` | Full redacted correction triples. | no (raw) |

## How it was built (deterministic, no LLM, no cost)
1. Walk `~/.claude/projects/**`, `~/brain-corpus/fleet/**` (jsonl) + `~/vault/chats/**` (md).
2. Drop sub-agent (Claude-to-Claude) sessions, compaction summaries, hook injections, command stubs.
3. Extract signals via regex: corrections (coarse + strong), anger (English + romanized Bengali profanity), false-done proxy, option-menu dumps, overbuild pushback, tool usage, domain (from cwd).
4. Pull real trigger→correction→recovery triples for strong/anger turns; bucket into a failure-mode taxonomy.
5. Synthesize `behavioral-model.json`. Secrets scrubbed at every stage.

## Headline findings (corpus: 1,359 Robeul sessions + 490 vault chats)
- **False-done is the #1 problem** — 472 sessions (34.7%) show a "done" claim followed by a correction/failure.
- **Short ask, long run** — avg 2.4 user turns vs 31.5 tool calls/session. He hands a terse goal and expects autonomous execution; scope drift, not effort, is the failure.
- **Option menus enrage him** — 504 sessions had assistant option-dumps; "What do you think by this kind of options every time? The fucking shit."
- **Overstepping** — 585 sessions had overbuild/scope pushback. "stop your over doing over stepping behaviour."
- **Anger is rare (1.6%) but sharp** — almost always after false-done / overstep / time-waste. Romanized Bengali profanity = trust broken = kill, admit, fix.
- **Heaviest domain by far: Outboundrix** (~586 sessions), then fleet ops (~231), brain (~150), Trusted-Leads (~125).

## Regenerate
```bash
python3 model/mine_corpus.py     # -> behavioral-model.v0.json + ~/brain-corpus/model/conversations.jsonl
python3 model/extract_pairs.py   # -> correction-taxonomy.v0.json + ~/brain-corpus/model/correction_pairs.jsonl
# then hand-refresh behavioral-model.json from the two outputs above
```

## Known gaps
- tl-scraper + robeul-server are decommissioned (2026-06-16) — their transcripts will never be pulled; the corpus is complete for the live fleet.
- The taxonomy is high-precision (95 turns) over high-recall: it favors real corrections over coverage. Session-level counts in `behavioral-model.v0.json` give the wider view.

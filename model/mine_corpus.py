#!/usr/bin/env python3
"""
Phase 0 corpus miner — deterministic, no LLM, no cost.

Pulls every Claude transcript + vault chat Robeul has ever produced and extracts
evidence-backed behavioral signals (corrections, anger, false-done, tool usage,
domains, session shape). Emits:

  ~/brain-corpus/model/conversations.jsonl   per-session records (raw-ish, stays OUT of git)
  <repo>/model/behavioral-model.v0.json      aggregate typed parameters (commit-safe; no raw chat)

Anti-waste: this runs free. The LLM classification pass (Phase 1) is gated on what
this surfaces — we only spend tokens on what deterministic mining cannot decide.

Secrets: tool_result bodies are NOT stored. User/assistant text is scanned, never
dumped wholesale; only short redacted snippets (<=160 chars, secret-scrubbed) are kept
as evidence, and only in the local conversations.jsonl (gitignored), never the aggregate.
"""
import os, re, json, glob, sys
from collections import Counter, defaultdict

HOME = os.path.expanduser("~")
SOURCES = [
    (os.path.join(HOME, ".claude", "projects"), "rentamac"),   # local transcripts
    (os.path.join(HOME, "brain-corpus", "fleet"), None),       # fleet pull (host in path)
]
VAULT = os.path.join(HOME, "vault", "chats")
OUT_LOCAL = os.path.join(HOME, "brain-corpus", "model", "conversations.jsonl")
OUT_AGG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "behavioral-model.v0.json")

# ---- signal lexicons (deterministic) ----------------------------------------
# coarse: any pushback-ish token (noisy — includes bare "no"/"don't")
CORRECTION = re.compile(r"\b(no|nope|wrong|stop|don'?t|again|redo|re-?do|undo|revert|"
                        r"not what|that'?s not|thats not|why did you|i said|i told you|"
                        r"useless|waste|broke|broken|still (?:not|broken|failing)|"
                        r"fix it|same (?:error|issue|problem))\b", re.I)
# strong: unambiguous "you got it wrong / undo it" — the real correction signal
STRONG_CORRECTION = re.compile(r"\b(wrong|stop|undo|revert|redo|re-?do|useless|waste|"
                               r"not what i|that'?s not what|thats not what|why did you|"
                               r"i (?:said|told you|asked)|you (?:broke|ruined|messed)|"
                               r"don'?t (?:do|add|change|touch|rolling)|same (?:error|issue|problem) (?:again|still))\b", re.I)
# romanized + native Bengali profanity / anger markers Robeul actually uses
PROFANITY = re.compile(r"\b(fuck|fucking|shit|wtf|damn|bullshit|crap|"
                       r"bsdk|bkl|bal|chod|choda|magi|kutta|kuttar|harami|"
                       r"shuorer|gadha|baine|bain|madarchod|maderchod|chutiya|chutiya)\b", re.I)
BENGALI = re.compile(r"[ঀ-৿]")  # any Bengali script char => emotional/native switch
# assistant over-claims completion
FALSE_DONE = re.compile(r"\b(done|completed|all set|works now|working now|fixed|"
                        r"production[- ]?(?:ready|grade)|deployed|good to go|"
                        r"successfully|ready to use)\b", re.I)
# failure / rework markers (either party)
FAILURE = re.compile(r"\b(error|failed|failing|traceback|exception|broke|broken|"
                     r"crash|revert|rollback|doesn'?t work|not working|build failed)\b", re.I)
# scope / over-engineering pushback
OVERBUILD = re.compile(r"\b(over[- ]?(?:engineer|complicat|build)|too (?:complex|much)|"
                       r"simplify|just (?:the|do|make)|only (?:the|do)|remove|delete|"
                       r"don'?t add|keep it simple|minimal)\b", re.I)
# menu / option-dumping by assistant (a known anti-pattern Robeul hates)
MENU = re.compile(r"(option\s*[a-c1-3]\b|^\s*[1-3]\.\s|would you like me to|"
                  r"want me to (?:also|now)|do you want me to|here are (?:a few|some|the) options)", re.I | re.M)

SECRET = re.compile(r"(?i)(sk-[a-z0-9-]{12,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
                    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?:\.[A-Za-z0-9_-]+)?|AKIA[0-9A-Z]{12,}|"
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|postgres(?:ql)?://[^\s\"']+|"
                    r"password[^\n]{0,15}?[:=]?\s*[A-Za-z0-9@#$%!._-]*\d[A-Za-z0-9@#$%!._-]*|"
                    r"api[_-]?key\s*[=:]\s*\S+|bearer\s+[A-Za-z0-9._-]{12,})")
SECRET_TOKEN = re.compile(r"\b(?=\S*\d)(?=\S*[@#$%!&*])[A-Za-z0-9@#$%!&*._-]{6,40}\b")
SKIP_FILE = re.compile(r"(\.bak$|pgpass|credential|\.pem$|\.key$|secret)", re.I)

# turns that are NOT Robeul talking (compaction, sub-agent prompts, hooks, command stubs)
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
NOISE_PREFIX = re.compile(r"^\s*(this session is being continued|caveat: the messages below|"
                          r"base directory for this skill:|you are the dedicated|you are a |"
                          r"you are an |stop hook feedback:|<command-name>|<command-message>|"
                          r"<local-command|the user opened the file|\[request interrupted)", re.I)


def strip_noise(txt):
    return REMINDER.sub(" ", txt or "").strip()


def is_real_user(txt):
    t = strip_noise(txt)
    if len(t) < 2:
        return False
    if NOISE_PREFIX.search(t):
        return False
    if "command-name" in t[:60] or "tool_use_error" in t[:60]:
        return False
    return True


def is_subagent_session(user_txts):
    """First real user turn is an agent role definition => Claude-to-Claude, not Robeul."""
    for u in user_txts[:1]:
        if re.match(r"\s*you are (the|a|an) ", u, re.I) and ("dedicated" in u.lower() or "agent" in u.lower() or "developer for" in u.lower()):
            return True
    return False


def scrub(s):
    return SECRET_TOKEN.sub("[REDACTED]", SECRET.sub("[REDACTED]", s or ""))


def snippet(s, n=160):
    s = scrub(re.sub(r"\s+", " ", (s or "")).strip())
    return s[:n]


def text_of(content):
    """content is str (user) or list of blocks (assistant/user-with-tool-result)."""
    if isinstance(content, str):
        return content, [], 0
    txt, tools, tool_errs = [], [], 0
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                txt.append(b.get("text", ""))
            elif t == "tool_use":
                tools.append(b.get("name", "?"))
            elif t == "tool_result":
                if b.get("is_error"):
                    tool_errs += 1
                # body intentionally NOT collected (size + secrets)
    return "\n".join(txt), tools, tool_errs


def host_for(path, default):
    if default:
        return default
    m = re.search(r"/brain-corpus/fleet/([^/]+)/", path)
    return m.group(1) if m else "unknown"


def mine_jsonl(path, host):
    """Return one session record or None."""
    user_txt, asst_txt = [], []
    tool_counter = Counter()
    tool_errs = 0
    cwd = gitbranch = version = None
    first_ts = last_ts = None
    turns = 0
    perm_modes = set()
    raw_first_user = None
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                ttype = d.get("type")
                if d.get("cwd") and not cwd:
                    cwd = d["cwd"]
                if d.get("gitBranch") and not gitbranch:
                    gitbranch = d["gitBranch"]
                if d.get("version") and not version:
                    version = d["version"]
                if d.get("permissionMode"):
                    perm_modes.add(d["permissionMode"])
                ts = d.get("timestamp")
                if ts:
                    first_ts = first_ts or ts
                    last_ts = ts
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                txt, tools, terr = text_of(msg.get("content"))
                tool_errs += terr
                for t in tools:
                    tool_counter[t] += 1
                if role == "user" and txt.strip():
                    if raw_first_user is None:
                        raw_first_user = txt
                    if is_real_user(txt):
                        user_txt.append(strip_noise(txt))
                        turns += 1
                elif role == "assistant" and (txt.strip() or tools):
                    asst_txt.append(txt)
    except Exception:
        return None

    user_blob = "\n".join(user_txt)
    asst_blob = "\n".join(asst_txt)
    if not user_blob and not asst_blob:
        return None

    # deterministic signals
    corrections = CORRECTION.findall(user_blob)
    strong_corr = STRONG_CORRECTION.findall(user_blob)
    anger_prof = PROFANITY.findall(user_blob)
    anger_bn = len(BENGALI.findall(user_blob))
    overbuild = OVERBUILD.findall(user_blob)
    asst_done = FALSE_DONE.findall(asst_blob)
    user_failmark = len(FAILURE.findall(user_blob))
    menu_hits = len(MENU.findall(asst_blob))

    # false-done proxy: assistant claimed done AND user later corrected / flagged failure
    false_done_proxy = bool(asst_done) and (len(corrections) > 0 or user_failmark > 0)

    # domain from cwd / gitbranch
    domain = None
    if cwd:
        domain = os.path.basename(cwd.rstrip("/")) or cwd
    rec = {
        "src": "jsonl",
        "path": path.replace(HOME, "~"),
        "host": host,
        "is_subagent": is_subagent_session([raw_first_user] if raw_first_user else []),
        "cwd": cwd,
        "domain": domain,
        "gitBranch": gitbranch,
        "version": version,
        "perm_modes": sorted(perm_modes),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "user_turns": turns,
        "asst_turns": len(asst_txt),
        "tool_calls": sum(tool_counter.values()),
        "top_tools": tool_counter.most_common(8),
        "tool_errors": tool_errs,
        "n_corrections": len(corrections),
        "n_strong_corrections": len(strong_corr),
        "n_anger_profanity": len(anger_prof),
        "n_bengali_chars": anger_bn,
        "n_overbuild_pushback": len(overbuild),
        "n_asst_done_claims": len(asst_done),
        "n_user_failure_marks": user_failmark,
        "n_menu_dumps": menu_hits,
        "false_done_proxy": false_done_proxy,
        # evidence (local-only, redacted)
        "ev_correction": snippet(next((u for u in user_txt if CORRECTION.search(u)), "")),
        "ev_anger": snippet(next((u for u in user_txt if PROFANITY.search(u) or BENGALI.search(u)), "")),
        "first_user": snippet(user_txt[0] if user_txt else ""),
    }
    return rec


def mine_md(path):
    try:
        with open(path, "r", errors="replace") as f:
            raw = f.read()
    except Exception:
        return None
    # split frontmatter
    meta = {}
    tags = []
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            in_tags = False
            for ln in parts[1].splitlines():
                if re.match(r"\s*-\s+\S", ln) and in_tags:
                    tags.append(ln.strip()[1:].strip())
                    continue
                in_tags = False
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    k, v = k.strip(), v.strip()
                    if k == "tags" and not v:
                        in_tags = True
                    meta[k] = v
            body = parts[2]
    corrections = CORRECTION.findall(body)
    anger_prof = PROFANITY.findall(body)
    anger_bn = len(BENGALI.findall(body))
    overbuild = OVERBUILD.findall(body)
    return {
        "src": "md",
        "path": path.replace(HOME, "~"),
        "host": "rentamac",
        "domain": meta.get("origin") or meta.get("source"),
        "meta_source": meta.get("source"),
        "meta_origin": meta.get("origin"),
        "tags": tags,
        "n_corrections": len(corrections),
        "n_anger_profanity": len(anger_prof),
        "n_bengali_chars": anger_bn,
        "n_overbuild_pushback": len(overbuild),
        "chars": len(body),
    }


def main():
    records = []
    files_jsonl = 0
    for base, default_host in SOURCES:
        for path in glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True):
            if SKIP_FILE.search(path):
                continue
            files_jsonl += 1
            host = host_for(path, default_host)
            rec = mine_jsonl(path, host)
            if rec:
                records.append(rec)

    md_records = []
    if os.path.isdir(VAULT):
        for path in glob.glob(os.path.join(VAULT, "**", "*.md"), recursive=True):
            if SKIP_FILE.search(path):
                continue
            r = mine_md(path)
            if r:
                md_records.append(r)

    # write local per-session (gitignored)
    os.makedirs(os.path.dirname(OUT_LOCAL), exist_ok=True)
    with open(OUT_LOCAL, "w") as f:
        for r in records + md_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ---- aggregate typed parameters (commit-safe; counts + IDs only) --------
    def agg_int(recs, key):
        return sum(r.get(key, 0) or 0 for r in recs)

    all_jsonl = records
    sub_sessions = [r for r in all_jsonl if r.get("is_subagent")]
    sessions = [r for r in all_jsonl if not r.get("is_subagent")]  # Robeul-driven only
    n_sessions = len(sessions)
    by_host = Counter(r["host"] for r in sessions)
    by_domain = Counter(r.get("domain") or "?" for r in sessions)
    tool_total = Counter()
    for r in sessions:
        for name, c in r.get("top_tools", []):
            tool_total[name] += c
    version_dist = Counter(r.get("version") or "?" for r in sessions)
    perm_dist = Counter(pm for r in sessions for pm in r.get("perm_modes", []))

    anger_sessions = [r for r in sessions if r["n_anger_profanity"] or r["n_bengali_chars"]]
    correction_sessions = [r for r in sessions if r["n_corrections"]]
    strong_corr_sessions = [r for r in sessions if r.get("n_strong_corrections")]
    false_done_sessions = [r for r in sessions if r["false_done_proxy"]]
    menu_sessions = [r for r in sessions if r["n_menu_dumps"]]
    overbuild_sessions = [r for r in sessions if r["n_overbuild_pushback"]]
    failure_sessions = [r for r in sessions if r["tool_errors"] or r["n_user_failure_marks"]]

    def ex_ids(recs, n=8):
        return [r["path"] for r in sorted(recs, key=lambda x: -(x.get("n_corrections", 0) + x.get("n_anger_profanity", 0)))[:n]]

    agg = {
        "schema": "behavioral-model/v0-deterministic",
        "note": "Phase 0 deterministic mine (no LLM). Counts are evidence; LLM pass (Phase 1) refines.",
        "corpus": {
            "jsonl_files_scanned": files_jsonl,
            "robeul_sessions": n_sessions,
            "subagent_sessions_excluded": len(sub_sessions),
            "vault_md_chats": len(md_records),
            "by_host": dict(by_host),
            "claude_code_versions": dict(version_dist.most_common(20)),
            "permission_modes": dict(perm_dist),
            "vault_origin": dict(Counter(r.get("meta_origin") or "?" for r in md_records)),
            "vault_top_tags": dict(Counter(t for r in md_records for t in r.get("tags", [])).most_common(30)),
        },
        "domains_worked": dict(by_domain.most_common(40)),
        "tool_usage": dict(tool_total.most_common(25)),
        "behavioral_signals": {
            "correction_rate": {
                "_note": "coarse = any pushback token incl bare 'no'/'don't' (noisy); strong = unambiguous wrong/undo/redo/'i said'",
                "coarse_sessions": len(correction_sessions),
                "coarse_pct": round(100 * len(correction_sessions) / n_sessions, 1) if n_sessions else 0,
                "strong_sessions": len(strong_corr_sessions),
                "strong_pct": round(100 * len(strong_corr_sessions) / n_sessions, 1) if n_sessions else 0,
                "total_correction_markers": agg_int(sessions, "n_corrections"),
                "total_strong_markers": agg_int(sessions, "n_strong_corrections"),
                "example_sessions": ex_ids(strong_corr_sessions),
            },
            "anger_events": {
                "sessions_with_anger": len(anger_sessions),
                "pct_of_sessions": round(100 * len(anger_sessions) / n_sessions, 1) if n_sessions else 0,
                "profanity_markers": agg_int(sessions, "n_anger_profanity"),
                "bengali_char_total": agg_int(sessions, "n_bengali_chars"),
                "example_sessions": ex_ids(anger_sessions),
            },
            "false_done_proxy": {
                "sessions": len(false_done_sessions),
                "pct_of_sessions": round(100 * len(false_done_sessions) / n_sessions, 1) if n_sessions else 0,
                "total_asst_done_claims": agg_int(sessions, "n_asst_done_claims"),
                "example_sessions": ex_ids(false_done_sessions),
            },
            "menu_option_dumps_by_assistant": {
                "sessions": len(menu_sessions),
                "total": agg_int(sessions, "n_menu_dumps"),
                "example_sessions": ex_ids(menu_sessions),
            },
            "overbuild_pushback": {
                "sessions": len(overbuild_sessions),
                "total": agg_int(sessions, "n_overbuild_pushback"),
                "example_sessions": ex_ids(overbuild_sessions),
            },
            "failure_rework": {
                "sessions": len(failure_sessions),
                "tool_errors_total": agg_int(sessions, "tool_errors"),
                "user_failure_marks_total": agg_int(sessions, "n_user_failure_marks"),
            },
        },
        "session_shape": {
            "avg_user_turns": round(sum(r["user_turns"] for r in sessions) / n_sessions, 1) if n_sessions else 0,
            "avg_tool_calls": round(sum(r["tool_calls"] for r in sessions) / n_sessions, 1) if n_sessions else 0,
            "total_tool_calls": sum(r["tool_calls"] for r in sessions),
        },
    }

    with open(OUT_AGG, "w") as f:
        json.dump(agg, f, indent=2, ensure_ascii=False)

    # console summary
    print(f"scanned jsonl files : {files_jsonl}")
    print(f"robeul sessions     : {n_sessions}  (subagent excluded: {len(sub_sessions)})")
    print(f"vault md chats      : {len(md_records)}")
    print(f"hosts               : {dict(by_host)}")
    print(f"correction (coarse) : {len(correction_sessions)} ({agg['behavioral_signals']['correction_rate']['coarse_pct']}%)")
    print(f"correction (strong) : {len(strong_corr_sessions)} ({agg['behavioral_signals']['correction_rate']['strong_pct']}%)")
    print(f"anger sessions      : {len(anger_sessions)} ({agg['behavioral_signals']['anger_events']['pct_of_sessions']}%)")
    print(f"false-done sessions : {len(false_done_sessions)} ({agg['behavioral_signals']['false_done_proxy']['pct_of_sessions']}%)")
    print(f"menu-dump sessions  : {len(menu_sessions)}")
    print(f"overbuild pushback  : {len(overbuild_sessions)}")
    print(f"top tools           : {tool_total.most_common(8)}")
    print(f"local records -> {OUT_LOCAL}")
    print(f"aggregate     -> {OUT_AGG}")


if __name__ == "__main__":
    main()

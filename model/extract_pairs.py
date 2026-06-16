#!/usr/bin/env python3
"""
Phase 1 (deterministic) — trigger -> correction -> recovery triples.

For every session that the miner flagged with a STRONG correction or anger, walk the
turns and capture: what the assistant had just done (the trigger), the user's actual
correcting words, and how the assistant recovered. Then bucket each correction into a
behavioral failure-mode taxonomy with real (redacted) example quotes + counts.

No LLM. The point is evidence: real words, not prose. Triples go to a gitignored local
file; only counts + one redacted example per bucket land in the committed model.
"""
import os, re, json, glob
from collections import Counter, defaultdict

HOME = os.path.expanduser("~")
SOURCES = [(os.path.join(HOME, ".claude", "projects"), "rentamac"),
           (os.path.join(HOME, "brain-corpus", "fleet"), None)]
OUT_PAIRS = os.path.join(HOME, "brain-corpus", "model", "correction_pairs.jsonl")
OUT_TAX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "correction-taxonomy.v0.json")

STRONG = re.compile(r"\b(wrong|stop|undo|revert|redo|re-?do|useless|waste|"
                    r"not what i|that'?s not what|thats not what|why did you|"
                    r"i (?:said|told you|asked)|you (?:broke|ruined|messed)|"
                    r"don'?t (?:do|add|change|touch|rolling)|same (?:error|issue|problem) (?:again|still))\b", re.I)
PROFANITY = re.compile(r"\b(fuck|fucking|shit|wtf|damn|bullshit|crap|bsdk|bkl|bal|chod|choda|"
                       r"magi|kutta|kuttar|harami|shuorer|gadha|madarchod|maderchod|chutiya)\b", re.I)
BENGALI = re.compile(r"[ঀ-৿]")
SECRET = re.compile(r"(?i)(sk-[a-z0-9-]{12,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
                    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?:\.[A-Za-z0-9_-]+)?|AKIA[0-9A-Z]{12,}|"
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|postgres(?:ql)?://[^\s\"']+|"
                    r"password[^\n]{0,15}?[:=]?\s*[A-Za-z0-9@#$%!._-]*\d[A-Za-z0-9@#$%!._-]*|"
                    r"api[_-]?key\s*[=:]\s*\S+|bearer\s+[A-Za-z0-9._-]{12,})")
# generic credential-shaped token: has a digit AND a special char, 6-40 chars (e.g. Xx0##0xX)
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
    """True only if this looks like Robeul actually typing (not agent/compaction/hook noise)."""
    t = strip_noise(txt)
    if len(t) < 2:
        return False
    if NOISE_PREFIX.search(t):
        return False
    if "command-name" in t[:60] or "tool_use_error" in t[:60]:
        return False
    return True

# failure-mode taxonomy — keyword -> bucket. First match wins; specific modes BEFORE broad ones.
BUCKETS = [
    ("wasting_time",       re.compile(r"wast(e|ing) (?:my |up my |of my )?times?|time wasting|suffering me|"
                                      r"hours? (lagash|lagse|laglo|wasted)|how many hours|7 hours|stop wasting|"
                                      r"still (?:not done|broken) after", re.I)),
    ("overstepping",       re.compile(r"over[- ]?(doing|stepping|step)|gone? ahead|went ahead|"
                                      r"doing what you want|did what you want|without (asking|telling|permission)|"
                                      r"who (gave you|told you to)|don'?t (do|add) (more|extra|unnecessary)|stop your over", re.I)),
    ("stale_or_wrong_memory", re.compile(r"old memory|stale|fuck your brain|your brain|real capabilit|actual (config|state|credential)|"
                                      r"check (live|actual|real)|memory takhte|paroah|access(ed)? it.*real|not using the actual", re.I)),
    ("false_done",         re.compile(r"not (done|working|fixed)|didn'?t (work|run)|still (broken|failing|not (?:done|working))|"
                                      r"you said.*(done|work)|where.*proof|it'?s not (done|working)|fake (execution|build)|"
                                      r"said done|same (?:error|issue|problem)|wrong (built|build)", re.I)),
    ("wrong_target",       re.compile(r"wrong (file|app|server|repo|project|place|one|sheet|source|db|database|email|mailbox|path|idea|built|build)|"
                                      r"not (that|this) (one|file|app|sheet)|i meant|different (app|file|sheet)|that'?s not the|"
                                      r"which (sheet|app|one)|drop wrong|totally wrong", re.I)),
    ("options_menu",       re.compile(r"kind of options|options? every time|don'?t (give|ask).*(option|menu)|stop asking|"
                                      r"bargain|\boption [a-c1-3]\b|menu|too (long|much text)|shorter|terse|stop (talking|explaining)|just (answer|tell)", re.I)),
    ("guessing_assuming",  re.compile(r"\bguess|\bassum|made up|hallucin|you don'?t know|check first|"
                                      r"did you (even )?(read|look|verify|check)|where did you get|who gave you (permission|the right)", re.I)),
    ("ignored_instruction",re.compile(r"i (said|told you|asked)|why did you|didn'?t i (say|tell)|i already|listen|read (what|the)|"
                                      r"don'?t (do|change|touch|rolling|go ahead|use)|flagging your|be (very )?disciplin|get lost", re.I)),
    ("broke_something",    re.compile(r"you broke|ruined|messed (up|it)|revert|undo|rollback|now it'?s broken|that broke", re.I)),
    ("over_engineering",   re.compile(r"over[- ]?(engineer|complicat|build)|too (complex|much)|simplif|just (the|do|make)|"
                                      r"only (the|do|need|csv)|don'?t add|keep it simple|minimal|too many|unnecessary|remove|delete|extra", re.I)),
    ("redo_again",         re.compile(r"\b(redo|re-?do|again|start over|from scratch)\b", re.I)),
]


def scrub(s):
    return SECRET_TOKEN.sub("[REDACTED]", SECRET.sub("[REDACTED]", s or ""))


def clean(s, n=300):
    return scrub(re.sub(r"\s+", " ", (s or "")).strip())[:n]


def text_and_tools(content):
    if isinstance(content, str):
        return content, []
    txt, tools = [], []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    txt.append(b.get("text", ""))
                elif b.get("type") == "tool_use":
                    tools.append(b.get("name", "?"))
    return "\n".join(txt), tools


def turns_of(path):
    out = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                if role not in ("user", "assistant"):
                    continue
                txt, tools = text_and_tools(msg.get("content"))
                # skip pure tool_result / command-stub user turns
                if role == "user" and (not txt.strip() or "command-name" in txt[:40]):
                    continue
                out.append((role, txt, tools))
    except Exception:
        pass
    return out


def bucket_of(text):
    for name, rx in BUCKETS:
        if rx.search(text):
            return name
    return "other"


# a session is a sub-agent (Claude-to-Claude) if its first user turn is a role/task brief
SUBAGENT_FIRST = re.compile(r"^\s*(you are\b|repo \(|here'?s the|here are the|your task\b|"
                            r"outboundrix\s*=|task:|context:|app pages live)", re.I)
BRIEF_MARKERS = re.compile(r"app router|monorepo at|live-verified|design lead|full-stack developer for|"
                           r"dedicated (developer|full-stack)", re.I)


def is_subagent_file(turns):
    for role, txt, _ in turns:
        if role == "user":
            t = strip_noise(txt)
            if len(t) < 2:
                continue
            return bool(SUBAGENT_FIRST.search(t) or BRIEF_MARKERS.search(t[:400]))
    return False


def main():
    pairs = []
    for base, default_host in SOURCES:
        for path in glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True):
            if SKIP_FILE.search(path):
                continue
            turns = turns_of(path)
            if is_subagent_file(turns):
                continue  # Claude-to-Claude session, not Robeul
            for i, (role, txt, tools) in enumerate(turns):
                if role != "user" or not txt:
                    continue
                if not is_real_user(txt):
                    continue
                txt = strip_noise(txt)
                # a correction must follow an assistant action — skip opening task briefs
                trig_txt, trig_tools = "", []
                for j in range(i - 1, -1, -1):
                    if turns[j][0] == "assistant":
                        trig_txt, trig_tools = turns[j][1], turns[j][2]
                        break
                if not trig_txt and not trig_tools:
                    continue  # first turn / no preceding action = a brief, not a correction
                # very long structured turns are briefs, not corrections
                if len(txt) > 2000:
                    continue
                kind = None
                if PROFANITY.search(txt) or BENGALI.search(txt):
                    kind = "anger"
                elif STRONG.search(txt):
                    kind = "strong"
                if not kind:
                    continue
                # recovery = nearest following assistant turn
                rec_txt = ""
                for j in range(i + 1, len(turns)):
                    if turns[j][0] == "assistant":
                        rec_txt = turns[j][1]
                        break
                pairs.append({
                    "path": path.replace(HOME, "~"),
                    "kind": kind,
                    "bucket": bucket_of(txt),
                    "trigger_action": clean(trig_txt, 220),
                    "trigger_tools": trig_tools[:6],
                    "correction": clean(txt, 300),
                    "recovery": clean(rec_txt, 220),
                })

    os.makedirs(os.path.dirname(OUT_PAIRS), exist_ok=True)
    with open(OUT_PAIRS, "w") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # aggregate taxonomy
    by_bucket = Counter(p["bucket"] for p in pairs)
    by_kind = Counter(p["kind"] for p in pairs)
    examples = {}
    for p in pairs:
        b = p["bucket"]
        # prefer a punchy, non-empty correction as the example
        if b not in examples and p["correction"]:
            examples[b] = {"correction": p["correction"][:200],
                           "trigger_tools": p["trigger_tools"],
                           "path": p["path"]}
    tax = {
        "schema": "correction-taxonomy/v0-deterministic",
        "note": "Real trigger->correction->recovery triples mined from strong-correction + anger turns. No LLM.",
        "total_correction_turns": len(pairs),
        "by_kind": dict(by_kind),
        "by_failure_mode": dict(by_bucket.most_common()),
        "example_per_mode": examples,
    }
    with open(OUT_TAX, "w") as f:
        json.dump(tax, f, indent=2, ensure_ascii=False)

    print(f"correction turns captured : {len(pairs)}")
    print(f"by kind                   : {dict(by_kind)}")
    print("by failure mode:")
    for b, c in by_bucket.most_common():
        print(f"  {b:22s} {c}")
    print(f"pairs (local) -> {OUT_PAIRS}")
    print(f"taxonomy      -> {OUT_TAX}")


if __name__ == "__main__":
    main()

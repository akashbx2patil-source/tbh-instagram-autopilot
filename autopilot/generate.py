"""AI post generation for TrustBrokerHub, with a second AI pass that reviews every post for compliance."""
import json, os, re
from pathlib import Path
from .render import validate, THEMES, FIELDS, LINKS

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
DATA = Path(__file__).resolve().parent.parent / "data"

# Facts that have been checked against official sources. The generator may only use
# regulatory numbers from this list; the reviewer rejects any other regulatory figure.
APPROVED_FACTS = """
- UK FSCS: eligible clients of a failed FCA-authorised investment firm may be compensated up to GBP 85,000 per person, per firm. Covers firm failure, never trading losses.
- Cyprus Investor Compensation Fund (CySEC firms): up to EUR 20,000 for eligible retail clients of a failed firm.
- ESMA (EU) and ASIC (Australia) retail leverage caps: 30:1 major FX pairs; 20:1 non-major FX, gold and major indices; 10:1 other commodities and non-major indices; 5:1 individual shares; 2:1 crypto.
- ESMA and ASIC retail rules: margin close-out when margin falls to 50% of required margin; negative balance protection for retail clients. The FCA (UK) applies equivalent retail rules.
- EU/UK CFD providers must display the percentage of their retail accounts that lose money.
- Japan FSA: retail FX leverage cap 25:1; crypto exchanges serving Japan must register with the FSA.
- US (CFTC/NFA): retail forex leverage 50:1 on major pairs, 20:1 on others; firms must be CFTC-registered and NFA members; check on NFA BASIC.
- Official lookups: FCA Financial Services Register and FCA Warning List; ASIC professional registers; CySEC regulated entities list; BaFin company database; NFA BASIC; FSA Japan registered-operator lists; MAS Financial Institutions Directory and MAS Investor Alert List; FSCA FSP search; IOSCO I-SCAN; RBI Alert List (India).
- Forex maths: a pip is 0.0001 for most pairs and 0.01 for JPY pairs. Standard lot 100,000 units, mini 10,000, micro 1,000. On USD-quoted pairs such as EUR/USD one pip is about $10 per standard lot.
- TrustBrokerHub: tracks 38,000+ brokers and exchanges, 112 regulators, 140 countries; Trust Score uses five factors (regulation, reviews, transparency, footprint, complaints); formula and weights published on /methodology; no pay-to-rank.
"""

RULES = """
You write Instagram posts for TrustBrokerHub (trustbrokerhub.com), a forex broker and crypto exchange review site whose purpose is to stop retail traders losing money to brokers that were never going to pay them.

NON-NEGOTIABLE RULES
- Research and safety only. Never financial or investment advice: no "best broker", no returns, never tell anyone what or when to trade.
- Never invent statistics, quotes, reviews, testimonials, case studies, press mentions or regulator actions.
- The ONLY regulatory or numeric facts you may state are in the APPROVED FACTS list, plus simple arithmetic built from them. If you want another number, write the sentence without it.
- Never say or imply that a named broker or exchange is a scam. Describe behaviours and patterns.
- Never quote Trust Score numbers or "top-rated" lists.
- Global audience, no home country. Spread regulator examples across regions. Use USD for worked examples.
- Voice: second person, flat confident verdicts, short sentences, British spelling (licence, authorised, recognise). No exclamation marks, no emoji, no hype, no urgency, no rhetorical-question headlines. Never use: "let's dive in", "it's important to note", "double-edged sword", "when it comes to", "fast-paced", "navigate the complexities".
- Calibration example of the voice: "Leverage does not make you a better trader. It makes every mistake bigger, and faster."

THEMES: scam (red flags, scam patterns, recovery scams, clone firms, social-media scams) | reg (what regulators do, compensation schemes, leverage caps, registers and warning lists, entities, crypto registration) | basics (trading concepts framed around the mistake they cause) | verify (how to check a broker before depositing, plus TrustBrokerHub tools; never salesy).
"""

SCHEMA = """
OUTPUT FORMAT
Return ONLY a JSON array inside <posts></posts> tags. Each post is an object with exactly these keys:
- "id": string given to you
- "theme": one of "scam", "reg", "basics", "verify"
- "kind": one of "statement", "redflag", "term", "number", "mythfact", "checklist", "carousel"
- "link": one of %s
- "data": fields depend on kind (see examples). Length limits in characters: title 95, sub 190, quote 70, meaning 190, action 100, term 32, definition 150, example 170, number 9, label 55, body 170, foot 70, myth 85, fact 170, note 60, kicker 32. Checklist: 3-5 items of max 60 chars. Carousel: "slides" is a list of 4-8 [heading, body] pairs, heading max 60, body max 170.
- "ig": the Instagram caption body, 2-4 short paragraphs separated by blank lines, max 900 characters. Do NOT add hashtags, links or a disclaimer; they are added automatically.
- "x": a short version for X, max 230 characters, no link.
- "tags": list of 0-2 extra lowercase hashtags.
""" % sorted(LINKS)


def _client():
    import anthropic
    return anthropic.Anthropic()


def _ask(system, user, max_tokens=8000):
    msg = _client().messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                                    messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _extract(tag, text):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    if not m:
        raise ValueError(f"model reply had no <{tag}> block")
    return json.loads(m.group(1).strip())


def write_posts(ids, themes, used_titles, feedback=""):
    examples = (DATA / "examples.json").read_text(encoding="utf-8")
    system = RULES + "\nAPPROVED FACTS\n" + APPROVED_FACTS + "\n" + SCHEMA
    plan = "\n".join(f'- id "{i}": theme "{t}"' for i, t in zip(ids, themes))
    user = (f"EXAMPLES OF FINISHED POSTS (match this structure and voice exactly):\n{examples}\n\n"
            f"TOPICS ALREADY USED (do not repeat these headlines or points; a fresh angle on a subject is fine):\n"
            + "\n".join(f"- {t}" for t in used_titles[-200:]) +
            f"\n\nWrite {len(ids)} new posts with these ids and themes:\n{plan}\n"
            "Use a mix of kinds; at most one carousel per 4 posts. Each post must teach one concrete, useful thing.\n"
            + (f"\nA previous attempt was rejected for these reasons. Fix them:\n{feedback}\n" if feedback else ""))
    return _extract("posts", _ask(system, user))


def review_posts(posts):
    system = ("You are a strict compliance and fact-check reviewer for a finance (YMYL) brand's social posts. "
              "Reject a post if ANY of these is true: it gives financial or investment advice or implies returns; "
              "it states a regulatory figure, statistic or fact not in the APPROVED FACTS list (simple arithmetic from them is fine); "
              "it names a real broker or exchange as a scam or makes any claim about a specific named firm; "
              "it quotes Trust Score numbers or top-rated lists; it is factually wrong or misleading; "
              "it uses hype, urgency, exclamation marks or emoji; it assumes the reader lives in one country.\n\n"
              "APPROVED FACTS\n" + APPROVED_FACTS +
              '\nReturn ONLY JSON inside <review></review> tags: an object mapping each post id to {"ok": true|false, "issues": ["..."]}.')
    return _extract("review", _ask(system, json.dumps(posts, ensure_ascii=False, indent=1), max_tokens=4000))


def generate(n, start_index, used_ids, used_titles, attempts=4):
    """Return (n posts that passed the validator and the AI review, next free id index)."""
    order = ["scam", "basics", "reg", "verify", "scam", "basics", "reg"]
    good, feedback = [], ""
    k = start_index
    for _ in range(attempts):
        need = n - len(good)
        if need <= 0:
            break
        ids = [f"ai{k + i:04d}" for i in range(need)]
        themes = [order[(k + i) % len(order)] for i in range(need)]
        k += need  # never reuse an id, even for rejected posts
        try:
            batch = write_posts(ids, themes, used_titles + [title_of(p) for p in good], feedback)
        except Exception as e:  # bad JSON etc.
            feedback = f"- Output could not be parsed: {e}"
            continue
        problems, passed = [], []
        for p in batch:
            if p.get("kind") == "carousel":
                p["data"]["slides"] = [tuple(s) for s in p["data"].get("slides", [])]
            errs = validate([p], list(used_ids) + [g["id"] for g in good])
            if errs:
                problems += errs
            else:
                passed.append(p)
        if passed:
            verdict = review_posts(passed)
            for p in passed:
                v = verdict.get(p["id"], {"ok": False, "issues": ["not reviewed"]})
                if v.get("ok"):
                    good.append(p)
                else:
                    problems += [f'{p["id"]}: {i}' for i in v.get("issues", [])]
        feedback = "\n".join(f"- {x}" for x in problems)
        if feedback:
            print("Rejected this round:\n" + feedback)
    if len(good) < n:
        raise RuntimeError(f"Only {len(good)} of {n} posts passed checks after {attempts} attempts")
    return good[:n], k


def title_of(p):
    d = p["data"]
    return d.get("title") or d.get("term") or d.get("quote") or d.get("myth") or f'{d.get("number", "")} {d.get("label", "")}'

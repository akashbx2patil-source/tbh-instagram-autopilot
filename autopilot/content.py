"""What the AI writes and how every piece is checked.

Steps that call an AI:
  write_post      -> headline, subline, CTA and a picture idea for the Canva template
  check_text      -> a second AI reviews the words (compliance + facts)
  check_image     -> a second AI looks at the finished Canva image (typos, cut-off text, odd picture)
  write_caption   -> after you approve: Instagram caption + hashtags
  check_caption   -> a second AI reviews the caption
"""
import json, re
from . import llm

DISCLAIMER = "Research only, not financial advice."
THEMES = ["scam", "basics", "reg", "verify"]
THEME_NAMES = {"scam": "scam awareness: red flags, scam patterns, recovery scams, clone firms, social-media scams",
               "basics": "trading basics framed around the mistake they cause (leverage, spreads, swaps, margin, lot size)",
               "reg": "regulation: what regulators do, compensation schemes, leverage caps, registers and warning lists",
               "verify": "how to check a broker before depositing, and TrustBrokerHub's tools (never salesy)"}

# Only these numbers and regulatory facts may appear. Add a line only after checking it on the regulator's own website.
APPROVED_FACTS = """
- UK FSCS: eligible clients of a failed FCA-authorised investment firm may be compensated up to GBP 85,000 per person, per firm. Covers firm failure, never trading losses.
- Cyprus Investor Compensation Fund (CySEC firms): up to EUR 20,000 for eligible retail clients of a failed firm.
- ESMA (EU) and ASIC (Australia) retail leverage caps: 30:1 major FX pairs; 20:1 non-major FX, gold and major indices; 10:1 other commodities and non-major indices; 5:1 individual shares; 2:1 crypto.
- ESMA and ASIC retail rules: margin close-out at 50% of required margin; negative balance protection for retail clients. The FCA (UK) applies equivalent retail rules.
- EU/UK CFD providers must show the percentage of their retail accounts that lose money.
- Japan FSA: retail FX leverage cap 25:1.
- US (CFTC/NFA): retail forex leverage 50:1 on major pairs, 20:1 on others; firms must be CFTC-registered and NFA members; check on NFA BASIC.
- Official lookups: FCA Register and FCA Warning List; ASIC professional registers; CySEC regulated entities list; BaFin company database; NFA BASIC; MAS Financial Institutions Directory and MAS Investor Alert List; FSCA FSP search; IOSCO I-SCAN; RBI Alert List (India).
- Forex maths: a pip is 0.0001 for most pairs and 0.01 for JPY pairs. Standard lot 100,000 units, mini 10,000, micro 1,000. On EUR/USD one pip is about USD 10 per standard lot.
- TrustBrokerHub (trustbrokerhub.com): 38,000+ brokers and exchanges, 100+ regulators, 140 countries; every broker has a Trust Score built from five factors (regulation, reviews, transparency, footprint, complaints); formula published on the methodology page; no pay-to-rank; scam alerts, trader reviews and a compare tool.
"""

RULES = """
You create Instagram content for TrustBrokerHub (trustbrokerhub.com), a forex broker and crypto exchange research site. Its purpose: help retail traders avoid brokers that were never going to pay them.

NON-NEGOTIABLE
- Research and safety only. Never financial or investment advice. No "best broker", no profits or returns, never tell anyone what or when to trade.
- Never invent statistics, quotes, reviews, testimonials, press mentions or regulator actions.
- The ONLY numbers and regulatory facts you may use are in APPROVED FACTS (simple arithmetic from them is fine).
- Never name a real broker or exchange. Never call any firm a scam; describe behaviours and patterns.
- Never quote Trust Score numbers or "top-rated" lists.
- Global audience. Spread regulator examples across regions. Use USD for worked examples.
- Voice: second person, flat confident verdicts, short sentences, British spelling (licence, authorised, recognise). No exclamation marks, no emoji, no hype, no urgency.
- Never use: "let's dive in", "it's important to note", "double-edged sword", "when it comes to", "fast-paced", "navigate", "game-changer", "unlock".
- Voice example: "Leverage does not make you a better trader. It makes every mistake bigger, and faster."
"""

HERO_STYLE = ("Photoreal advertising photograph, 4:5 portrait. Dark navy studio (#071A3D), seamless backdrop, soft atmospheric "
              "haze, cool blue rim light (#1261D6). The whole scene sits in the LOWER HALF of the frame; the upper 45% is "
              "empty navy haze (space for a headline). Absolutely no text, letters, numbers, logos, screens with writing, "
              "or human faces. Subject: ")

BANNED = ["let's dive", "it's important to note", "double-edged sword", "when it comes to", "fast-paced", "navigate",
          "game-changer", "game changer", "unlock", "guaranteed", "risk-free", "risk free", "get rich", "best broker",
          "top-rated", "top rated", "100% safe", "financial freedom"]
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def _problems_in(text):
    low, out = text.lower(), []
    if "!" in text:
        out.append("uses an exclamation mark")
    if EMOJI.search(text):
        out.append("uses emoji")
    out += [f'uses the banned phrase "{b}"' for b in BANNED if b in low]
    return out


def validate_post(p):
    """Fast rule checks that need no AI. Returns a list of problems (empty = fine)."""
    errs = []
    for k in ("theme", "headline", "subline", "cta", "hero_prompt", "topic"):
        if not str(p.get(k, "")).strip():
            errs.append(f"missing {k}")
    if errs:
        return errs
    lines = p["headline"].split("\n")
    if len(lines) != 3:
        errs.append(f"headline must be exactly 3 lines separated by \\n (got {len(lines)})")
    for ln in lines:
        if len(ln) > 16:
            errs.append(f'headline line "{ln}" is {len(ln)} characters; max 16')
        if ln != ln.upper():
            errs.append(f'headline line "{ln}" must be UPPERCASE')
    if len(p["subline"]) > 110:
        errs.append(f"subline is {len(p['subline'])} characters; max 110")
    if len(p["cta"]) > 34:
        errs.append(f"cta is {len(p['cta'])} characters; max 34")
    if p["theme"] not in THEMES:
        errs.append(f"theme must be one of {THEMES}")
    for k in ("headline", "subline", "cta"):
        errs += [f"{k} {x}" for x in _problems_in(p[k])]
    return errs


def write_post(theme, used_topics, feedback="", topic_request="", extra=""):
    system = RULES + "\nAPPROVED FACTS\n" + APPROVED_FACTS + """
TASK: write ONE single-image Instagram post that teaches one concrete, useful thing.
It is placed into a fixed Canva design: a big 3-line headline at the top, one supporting line under it,
a short call-to-action line in the footer, and a photoreal picture filling the background.

Return JSON with exactly these keys:
- "theme": the theme you were given
- "topic": 5-10 words describing the subject (used to avoid repeats)
- "headline": EXACTLY 3 lines joined with "\\n", UPPERCASE, max 16 characters per line, a complete thought (e.g. "LEVERAGE DOES\\nNOT MAKE YOU\\nA BETTER TRADER.")
- "subline": one or two plain sentences, max 110 characters, that land the point
- "cta": footer line, max 34 characters, calm, no question, e.g. "Check the register, not the badge."
- "hero_prompt": one sentence describing a photoreal object scene (a metaphor for the idea) for the background picture. Objects only, no people's faces, no text or screens.
"""
    if extra.strip():
        system += "\nEXTRA INSTRUCTIONS FROM THE OWNER (follow them unless they break a non-negotiable rule):\n" + extra.strip() + "\n"
    if topic_request:
        theme_line = f"Write about this topic the owner asked for: {topic_request}\n(Use the closest theme of: {', '.join(THEMES)}.)"
    else:
        theme_line = f"Theme: {theme} ({THEME_NAMES[theme]})."
    user = (f"{theme_line}\n\nTopics already used (pick something different):\n"
            + "\n".join(f"- {t}" for t in used_topics[-150:])
            + (f"\n\nYour last attempt was rejected. Fix these problems:\n{feedback}" if feedback else ""))
    p = llm.ask_json("writer", system, user, temperature=0.9)
    if p.get("theme") not in THEMES or not topic_request:
        p["theme"] = theme
    return p


def check_text(p):
    system = ("You are a strict compliance and fact-check reviewer for a finance (YMYL) brand's Instagram posts.\n"
              "Reject the post if ANY of these is true: it gives financial or investment advice or implies returns; "
              "it states a number or regulatory fact that is not in APPROVED FACTS (simple arithmetic from them is fine); "
              "it names a real broker or exchange or calls any firm a scam; it quotes Trust Score numbers or rankings; "
              "it is factually wrong or misleading; it uses hype, urgency, exclamation marks or emoji; it has a spelling "
              "or grammar mistake (British spelling is correct); the headline does not read as a complete thought.\n\n"
              "APPROVED FACTS\n" + APPROVED_FACTS +
              '\nReturn JSON: {"ok": true or false, "issues": ["short, specific problem", ...]}')
    shown = {k: p[k] for k in ("headline", "subline", "cta")}
    return llm.ask_json("checker", system, json.dumps(shown, ensure_ascii=False, indent=1), temperature=0)


def check_image(png, p):
    system = ("You check a finished Instagram post image before it is published. The intended text is given.\n"
              "Reject it if ANY of these is true: any text is misspelt or differs from the intended text; text is cut off, "
              "overlaps other text or the logo, or runs outside the picture; text is hard to read against the background; "
              "the picture contains stray letters, fake writing, a distorted object or anything disturbing; the picture "
              "has nothing to do with the message; it looks broken or unprofessional.\n"
              'Return JSON: {"ok": true or false, "issues": ["short, specific problem", ...]}')
    user = "Intended text:\n" + json.dumps({k: p[k] for k in ("headline", "subline", "cta")}, ensure_ascii=False, indent=1)
    return llm.ask_json("checker", system, user, image_png=png, temperature=0)


def write_caption(post_text, feedback=""):
    system = RULES + "\nAPPROVED FACTS\n" + APPROVED_FACTS + """
TASK: write the Instagram caption for a post that is already designed and approved.
Return JSON with exactly these keys:
- "caption": 2-4 short paragraphs separated by blank lines, max 900 characters. Expand on the post with one useful,
  concrete detail. End with a soft line pointing to the link in bio (e.g. "Search any broker before you deposit. Link in bio.").
  Do NOT include hashtags or the disclaimer; they are added automatically.
- "hashtags": 6 to 10 lowercase hashtags relevant to the post, each starting with "#". Mix broad (#forex, #forextrading,
  #trading) and specific (#brokerreview, #scamalert, #duediligence, #investorprotection, #riskmanagement ...).
  Never brand names or broker names.
"""
    user = "The post says:\n" + post_text + (f"\n\nYour last caption was rejected. Fix:\n{feedback}" if feedback else "")
    return llm.ask_json("writer", system, user, temperature=0.7)


def validate_caption(c):
    errs = []
    cap, tags = str(c.get("caption", "")).strip(), c.get("hashtags") or []
    if not cap:
        errs.append("caption is empty")
    if len(cap) > 900:
        errs.append(f"caption is {len(cap)} characters; max 900")
    if "#" in cap:
        errs.append("caption contains hashtags; put them only in the hashtags list")
    if not 6 <= len(tags) <= 10:
        errs.append(f"need 6-10 hashtags (got {len(tags)})")
    for t in tags:
        if not re.fullmatch(r"#[a-z0-9_]{2,40}", str(t)):
            errs.append(f'bad hashtag "{t}"')
    errs += [f"caption {x}" for x in _problems_in(cap)]
    return errs


def check_caption(post_text, c):
    system = ("You are a strict compliance and fact-check reviewer for a finance (YMYL) brand's Instagram captions.\n"
              "Reject the caption if ANY of these is true: financial or investment advice or implied returns; a number or "
              "regulatory fact not in APPROVED FACTS; a real broker or exchange named or any firm called a scam; Trust Score "
              "numbers or rankings; factually wrong or misleading; hype, urgency, exclamation marks or emoji; spelling or "
              "grammar mistakes (British spelling is correct); it does not match the post.\n\nAPPROVED FACTS\n" + APPROVED_FACTS +
              '\nReturn JSON: {"ok": true or false, "issues": ["short, specific problem", ...]}')
    user = f"POST:\n{post_text}\n\nCAPTION:\n{c['caption']}\n\nHASHTAGS: {' '.join(c['hashtags'])}"
    return llm.ask_json("checker", system, user, temperature=0)


def final_caption(c, always=()):
    tags = list(dict.fromkeys(str(t).strip().lower() for t in list(c["hashtags"]) + list(always or []) if str(t).strip()))
    return f"{c['caption'].strip()}\n\n{DISCLAIMER}\n\n{' '.join(tags)}"


def post_text(p):
    """Plain-text description of a post, given to the caption writer."""
    if p.get("text"):
        return p["text"]
    return f"Headline: {p['headline'].replace(chr(10), ' ')}\nSupporting line: {p['subline']}\nFooter: {p['cta']}"

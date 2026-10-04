"""Rendering + validation for TrustBrokerHub Instagram posts.

Usage:  python3 build.py --start 2026-10-05 --time 18:30
Outputs: out/posts/<Day-NN_date_id>/ (slide PNGs, captions.txt, linkedin.pdf for carousels)
         out/calendar.csv
"""
import argparse, csv, datetime as dt, html, json, re, shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image

SITE = "trustbrokerhub.com"
THEMES = {
    "scam":   dict(label="SCAM WATCH",     accent="#F5A524", li="#ScamAwareness",
                   tags=["#scamalert", "#forexscam", "#investmentscam", "#fraudprevention"]),
    "reg":    dict(label="REGULATION",     accent="#6EA8FF", li="#FinancialRegulation",
                   tags=["#financialregulation", "#investorprotection", "#regulatedbroker", "#compliance"]),
    "basics": dict(label="TRADING BASICS", accent="#2DD4BF", li="#TradingEducation",
                   tags=["#forexeducation", "#tradingeducation", "#learntotrade", "#riskmanagement"]),
    "verify": dict(label="VERIFY FIRST",   accent="#4ADE80", li="#DueDiligence",
                   tags=["#brokercheck", "#duediligence", "#investorprotection", "#tradingsafety"]),
}
COMMON = ["#forex", "#forextrading", "#trading", "#brokerreview"]
X_TAG = {"scam": "#ScamAlert", "reg": "#Forex", "basics": "#Forex", "verify": "#Forex"}
DISCLAIMER = "Research only, not financial advice."

e = html.escape

SHIELD = """<svg width="54" height="62" viewBox="0 0 54 62"><path d="M27 2 L51 11 V29 C51 45 40 55 27 60 C14 55 3 45 3 29 V11 Z" fill="none" stroke="var(--accent)" stroke-width="4"/><path d="M16 31 L24 39 L39 22" fill="none" stroke="var(--ink)" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>"""

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0A1930;--ink:#F3F6FB;--muted:#A6B4CB;--line:rgba(255,255,255,.10);--card:rgba(255,255,255,.045)}
html,body{width:1080px;height:1350px;background:var(--bg);font-family:'Inter',sans-serif;color:var(--ink);-webkit-font-smoothing:antialiased}
.page{position:relative;width:1080px;height:1350px;padding:72px 80px 60px;display:flex;flex-direction:column;overflow:hidden;
 background:radial-gradient(900px 700px at 105% -10%, color-mix(in srgb,var(--accent) 22%, transparent), transparent 60%),
            radial-gradient(700px 600px at -20% 115%, rgba(80,120,200,.16), transparent 60%), var(--bg)}
.page:before{content:"";position:absolute;inset:0;background-image:radial-gradient(rgba(255,255,255,.07) 1.4px,transparent 1.4px);background-size:36px 36px;
 -webkit-mask-image:linear-gradient(180deg,#000 0,transparent 45%);pointer-events:none}
header,footer,main{position:relative;z-index:1}
header{display:flex;align-items:center;justify-content:space-between}
.brand{display:flex;align-items:center;gap:16px;font-size:34px;letter-spacing:-.5px}
.brand b{font-weight:800}.brand span{font-weight:400;color:var(--muted)}
.chip{font-size:22px;font-weight:700;letter-spacing:3px;color:var(--accent);border:2px solid color-mix(in srgb,var(--accent) 55%,transparent);padding:12px 22px;border-radius:999px;background:color-mix(in srgb,var(--accent) 10%,transparent)}
main{flex:1;display:flex;flex-direction:column;justify-content:center;min-height:0;overflow:hidden;padding:40px 0}
footer{display:flex;justify-content:space-between;align-items:center;border-top:2px solid var(--line);padding-top:26px;font-size:24px;color:var(--muted)}
footer .url{color:var(--ink);font-weight:600}
.kicker{font-size:30px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--accent);margin-bottom:30px}
h1{font-weight:800;letter-spacing:-2.5px;line-height:1.06}
.sub{color:var(--muted);line-height:1.38;margin-top:36px}
.label{font-size:24px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:var(--muted);margin-bottom:14px}
.box{background:var(--card);border:2px solid var(--line);border-radius:28px;padding:36px 40px;margin-top:40px}
.box.accent{border-color:color-mix(in srgb,var(--accent) 60%,transparent);background:color-mix(in srgb,var(--accent) 9%,transparent)}
.box p{line-height:1.4}
.quote{font-weight:800;letter-spacing:-1.5px;line-height:1.12;position:relative}
.qmark{font-size:200px;line-height:.6;color:var(--accent);font-weight:800;height:90px;display:block}
.flag{display:inline-flex;align-items:center;gap:12px;font-size:26px;font-weight:800;letter-spacing:3px;color:#0A1930;background:var(--accent);padding:10px 20px;border-radius:10px;margin-bottom:34px;align-self:flex-start}
.big{font-weight:800;letter-spacing:-8px;line-height:.95;color:var(--accent)}
.items{list-style:none;margin-top:44px}
.items li{display:flex;gap:28px;align-items:flex-start;padding:24px 0;border-bottom:2px solid var(--line);line-height:1.3}
.items li:last-child{border-bottom:0}
.tick{flex:none;width:46px;height:46px;border-radius:12px;border:3px solid var(--accent);display:flex;align-items:center;justify-content:center;margin-top:2px}
.mf{border-radius:28px;padding:40px 44px;border:2px solid var(--line)}
.mf + .mf{margin-top:28px}
.mf.myth{background:rgba(248,113,113,.08);border-color:rgba(248,113,113,.45)}
.mf.fact{background:rgba(74,222,128,.08);border-color:rgba(74,222,128,.5)}
.mf .tag{font-size:26px;font-weight:800;letter-spacing:4px;margin-bottom:18px}
.mf.myth .tag{color:#F87171}.mf.fact .tag{color:#4ADE80}
.mf p{line-height:1.3}
.idx{font-weight:800;font-size:170px;line-height:.9;letter-spacing:-6px;color:transparent;-webkit-text-stroke:3px var(--accent);margin-bottom:40px}
.swipe{margin-top:56px;display:flex;align-items:center;gap:18px;font-size:30px;font-weight:700;color:var(--accent)}
.count{font-weight:700;color:var(--ink)}
"""

FIT_JS = """
() => {
  const main = document.querySelector('main');
  const fits = [...document.querySelectorAll('[data-fit]')];
  let guard = 0;
  while (main.scrollHeight > main.clientHeight + 1 && guard < 200) {
    fits.forEach(el => { const s = parseFloat(getComputedStyle(el).fontSize); el.style.fontSize = Math.max(s * 0.96, 18) + 'px'; });
    guard++;
  }
  return guard;
}
"""

def shell(theme, body, counter=""):
    t = THEMES[theme]
    mid = f'<span class="count">{counter}</span>' if counter else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet"><style>{CSS}</style></head>
<body style="--accent:{t['accent']}"><div class="page">
<header><div class="brand">{SHIELD}<div><b>Trust</b><span>BrokerHub</span></div></div><div class="chip">{t['label']}</div></header>
<main>{body}</main>
<footer><span>{DISCLAIMER}</span>{mid}<span class="url">{SITE}</span></footer>
</div></body></html>"""

TICK = '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M4 12.5 L10 18 L20 6" fill="none" stroke="var(--accent)" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'

def pages_for(p):
    d, th, k = p["data"], p["theme"], p["kind"]
    if k == "statement":
        return [shell(th, f'<div class="kicker">{e(d["kicker"])}</div><h1 data-fit style="font-size:88px">{e(d["title"])}</h1><p class="sub" data-fit style="font-size:38px">{e(d["sub"])}</p>')]
    if k == "redflag":
        return [shell(th, f'<div class="flag">RED FLAG</div><span class="qmark">&ldquo;</span><p class="quote" data-fit style="font-size:76px">{e(d["quote"])}</p>'
                          f'<div class="box"><div class="label">What it really means</div><p data-fit style="font-size:36px">{e(d["meaning"])}</p></div>'
                          f'<div class="box accent" style="margin-top:24px"><div class="label" style="color:var(--accent)">Do this instead</div><p data-fit style="font-size:36px;font-weight:600">{e(d["action"])}</p></div>')]
    if k == "term":
        return [shell(th, f'<div class="kicker">Glossary</div><h1 data-fit style="font-size:110px">{e(d["term"])}</h1><p class="sub" data-fit style="font-size:44px;color:var(--ink)">{e(d["definition"])}</p>'
                          f'<div class="box accent"><div class="label" style="color:var(--accent)">In practice</div><p data-fit style="font-size:36px">{e(d["example"])}</p></div>')]
    if k == "number":
        return [shell(th, f'<div class="big" data-fit style="font-size:{230 if len(d["number"])<6 else 190}px">{e(d["number"])}</div><h1 data-fit style="font-size:58px;margin-top:40px;letter-spacing:-1.5px">{e(d["label"])}</h1>'
                          f'<p class="sub" data-fit style="font-size:38px">{e(d["body"])}</p><div class="box accent"><p data-fit style="font-size:34px;font-weight:600">{e(d["foot"])}</p></div>')]
    if k == "mythfact":
        return [shell(th, f'<div class="kicker">Myth vs fact</div><div class="mf myth"><div class="tag">MYTH</div><p data-fit style="font-size:52px;font-weight:700">{e(d["myth"])}</p></div>'
                          f'<div class="mf fact"><div class="tag">FACT</div><p data-fit style="font-size:40px">{e(d["fact"])}</p></div>')]
    if k == "checklist":
        items = "".join(f'<li><span class="tick">{TICK}</span><span data-fit style="font-size:38px">{e(i)}</span></li>' for i in d["items"])
        return [shell(th, f'<div class="kicker">Checklist</div><h1 data-fit style="font-size:72px">{e(d["title"])}</h1><ul class="items">{items}</ul>'
                          f'<p class="sub" data-fit style="font-size:32px;margin-top:20px;font-weight:600;color:var(--accent)">{e(d["note"])}</p>')]
    if k == "carousel":
        n = len(d["slides"]) + 2
        out = [shell(th, f'<div class="kicker">{e(d["kicker"])}</div><h1 data-fit style="font-size:100px">{e(d["title"])}</h1>'
                         f'<div class="swipe">Swipe <svg width="60" height="24" viewBox="0 0 60 24"><path d="M2 12 H54 M44 3 L56 12 L44 21" fill="none" stroke="var(--accent)" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg></div>', f"1 / {n}")]
        for i, (h, b) in enumerate(d["slides"], 1):
            out.append(shell(th, f'<div class="idx">{i:02d}</div><h1 data-fit style="font-size:70px;letter-spacing:-1.8px">{e(h)}</h1><p class="sub" data-fit style="font-size:42px">{e(b)}</p>', f"{i+1} / {n}"))
        out.append(shell(th, f'<div class="kicker">Before you deposit</div><h1 data-fit style="font-size:80px">Check any broker\'s licence, warnings and reviews for free.</h1>'
                             f'<div class="box accent"><p data-fit style="font-size:40px;font-weight:700">{SITE}{e(p["link"])}</p></div>'
                             f'<p class="sub" data-fit style="font-size:34px">Save this post. Share it with someone about to open an account.</p>', f"{n} / {n}"))
        return out
    raise ValueError(k)

def captions(p):
    t = THEMES[p["theme"]]
    url = f"https://{SITE}{p['link']}"
    tags = list(dict.fromkeys(t["tags"] + COMMON + p.get("tags", [])))[:10]
    ig = f"{p['ig']}\n\nMore at {SITE}{p['link']} (link in bio)\n\n{DISCLAIMER}\n\n{' '.join(tags)}"
    li_body = re.sub(r"\s*—\s*link in bio\.", ".", p["ig"])
    li_body = re.sub(r"\n*Link in bio\.?", "", li_body).strip()
    li = f"{li_body}\n\nMore: {url}\n\n{DISCLAIMER}\n\n#Forex #InvestorProtection {t['li']}"
    x = f"{p['x']}\n\n{url}"
    xt = f"{x}\n\n{X_TAG[p['theme']]}"
    if xlen(xt) <= 280: x = xt
    return ig, x, li

def xlen(s):  # X counts every URL as 23 characters
    return len(re.sub(r"https?://\S+", "x" * 23, s))

LINKS = {"/brokers", "/rankings", "/compare", "/regulators", "/scam-alerts", "/reviews", "/methodology",
         "/regulation", "/glossary", "/tools", "/news", "/learn", "/cashback"}
FIELDS = {"statement": ["kicker", "title", "sub"], "redflag": ["quote", "meaning", "action"],
          "term": ["term", "definition", "example"], "number": ["number", "label", "body", "foot"],
          "mythfact": ["myth", "fact"], "checklist": ["title", "items", "note"], "carousel": ["kicker", "title", "slides"]}
BANNED = ["!", "let's dive", "it's important to note", "double-edged sword", "when it comes to", "fast-paced",
          "navigate the complexities", "best broker", "you should trade", "top-rated", "trust score of"]
# Rough on-image limits (characters) so text stays large and readable.
LIMITS = {"title": 95, "sub": 190, "quote": 70, "meaning": 190, "action": 100, "term": 32, "definition": 150,
          "example": 170, "number": 9, "label": 55, "body": 170, "foot": 70, "myth": 85, "fact": 170, "note": 60, "kicker": 32}

def validate(posts, used_ids=()):
    errs = []
    seen = set(used_ids)
    for p in posts:
        pid = p.get("id", "?")
        if pid in seen: errs.append(f"{pid}: duplicate id")
        seen.add(pid)
        if p.get("theme") not in THEMES: errs.append(f"{pid}: bad theme {p.get('theme')}")
        k = p.get("kind")
        if k not in FIELDS: errs.append(f"{pid}: bad kind {k}"); continue
        if p.get("link") not in LINKS: errs.append(f"{pid}: link must be one of {sorted(LINKS)}")
        d = p.get("data", {})
        for f in FIELDS[k]:
            if not d.get(f): errs.append(f"{pid}: missing data.{f}")
        for f, n in LIMITS.items():
            if isinstance(d.get(f), str) and len(d[f]) > n: errs.append(f"{pid}: data.{f} is {len(d[f])} chars (max {n})")
        if k == "checklist" and not (3 <= len(d.get("items", [])) <= 5): errs.append(f"{pid}: checklist needs 3-5 items")
        if k == "checklist" and any(len(i) > 60 for i in d.get("items", [])): errs.append(f"{pid}: checklist item over 60 chars")
        if k == "carousel":
            s = d.get("slides", [])
            if not (4 <= len(s) <= 8): errs.append(f"{pid}: carousel needs 4-8 slides")
            for h, b in s:
                if len(h) > 60 or len(b) > 170: errs.append(f"{pid}: slide too long: {h[:30]}")
        if not p.get("ig"): errs.append(f"{pid}: missing ig")
        if len(p.get("ig", "")) > 1500: errs.append(f"{pid}: ig caption too long")
        text = json.dumps(p, ensure_ascii=False).lower()
        for b in BANNED:
            if b in text: errs.append(f"{pid}: contains banned phrase '{b}'")
        if p.get("x") and xlen(p["x"] + "\n\nhttps://x.co/a") > 280: errs.append(f"{pid}: X post too long")
    return errs

def write_xlsx(rows, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook(); ws = wb.active; ws.title = "Calendar"
    cols = [("Posted?", 9, None), ("Day", 6, "day"), ("Date", 12, "date"), ("Day of week", 8, "weekday"), ("Time (IST)", 10, "time_ist"),
            ("Theme", 16, "theme"), ("Format", 13, "format"), ("Images", 8, "images"), ("Title", 44, "title"), ("Folder", 30, "folder"),
            ("Link", 36, "link"), ("Instagram caption", 60, "instagram_caption"), ("X post", 50, "x_post"), ("X chars", 8, "x_chars"),
            ("LinkedIn post", 60, "linkedin_post"), ("Alt text", 50, "alt_text")]
    fills = {"SCAM WATCH": "FDF1DC", "REGULATION": "E3EDFF", "TRADING BASICS": "DDF7F3", "VERIFY FIRST": "E0F8E9"}
    for i, (n, w, _) in enumerate(cols, 1):
        c = ws.cell(1, i, n); c.font = Font(name="Arial", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="0A1930"); c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[c.column_letter].width = w
    for r, row in enumerate(rows, 2):
        for i, (_, _, k) in enumerate(cols, 1):
            c = ws.cell(r, i, row[k] if k else ""); c.font = Font(name="Arial", size=10); c.alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(r, 6).fill = PatternFill("solid", fgColor=fills[row["theme"]]); ws.row_dimensions[r].height = 90
    ws.freeze_panes = "C2"; ws.auto_filter.ref = f"A1:P{len(rows)+1}"
    wb.save(path)

def alt(p):
    d = p["data"]; k = p["kind"]
    if k == "carousel": return f'{d["title"]}. ' + " ".join(f"{i}. {h}: {b}" for i, (h, b) in enumerate(d["slides"], 1))
    if k == "redflag": return f'Red flag: "{d["quote"]}" What it means: {d["meaning"]} Do this instead: {d["action"]}'
    if k == "term": return f'{d["term"]}: {d["definition"]} {d["example"]}'
    if k == "number": return f'{d["number"]}: {d["label"]}. {d["body"]} {d["foot"]}'
    if k == "mythfact": return f'Myth: {d["myth"]} Fact: {d["fact"]}'
    if k == "checklist": return f'{d["title"]}: ' + "; ".join(d["items"]) + f'. {d["note"]}'
    return f'{d["title"]} {d["sub"]}'



def instagram_caption(p):
    return captions(p)[0]

def render_post(p, folder):
    """Render every slide of a post to JPEG (Instagram's API accepts JPEG only). Returns the file paths."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    files = []
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
        for i, h in enumerate(pages_for(p), 1):
            pg.set_content(h, wait_until="networkidle")
            pg.evaluate("document.fonts.ready")
            pg.evaluate(FIT_JS)
            f = folder / f"slide-{i:02d}.jpg"
            pg.screenshot(path=str(f), type="jpeg", quality=92)
            files.append(f)
        br.close()
    return files

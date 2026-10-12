"""TrustBrokerHub Instagram autopilot.

  python -m autopilot.main create         # make today's post and send it to your phone for approval
  python -m autopilot.main check          # read Approve / Reject; publish approved posts at POST_AT
  python -m autopilot.main refresh-token  # renew the Instagram token
  python -m autopilot.main status         # print what is pending / queued
  python -m autopilot.main setup <step> [value]   # one-time setup helpers (see README)

Daily flow:
  1. create: the writer AI (Gemini or ChatGPT) writes headline + subline + CTA + picture idea
  2. rule check + checker AI review (rewritten until it passes)
  3. optional AI background picture, then Canva fills the brand template and exports a PNG
  4. the checker AI looks at the finished image
  5. Telegram sends it to your phone with Approve / Reject
  6. check: once approved, the writer AI writes caption + hashtags, the checker AI reviews them
  7. it is published to Instagram at POST_AT (default 18:30 India time)
"""
import datetime as dt, json, os, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

from . import content, telegram as tg

ROOT = Path(__file__).resolve().parent.parent
DATA, MEDIA = ROOT / "data", ROOT / "media"
PENDING, POSTED, STATE = DATA / "pending.json", DATA / "posted.json", DATA / "state.json"
READY, REJECTED, STATUS = DATA / "ready.json", DATA / "rejected.json", DATA / "status.json"
TZ = ZoneInfo(os.environ.get("TIMEZONE") or "Asia/Kolkata")
POST_AT = os.environ.get("POST_AT") or "18:30"
MAX_PER_DAY = int(os.environ.get("MAX_POSTS_CREATED_PER_DAY") or "4")
REASON_WAIT_MIN = 20
DRY_RUN = (os.environ.get("DRY_RUN") or "false").lower() == "true"


# ---------- small helpers ----------
def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, obj):
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def now():
    return dt.datetime.now(TZ)


def iso(t=None):
    return (t or now()).isoformat(timespec="seconds")


def raw_url(rel):
    repo = os.environ.get("GITHUB_REPOSITORY", "akashbx2patil-source/tbh-instagram-autopilot")
    branch = os.environ.get("GITHUB_REF_NAME") or "main"
    return f"{os.environ.get('MEDIA_BASE_URL') or f'https://raw.githubusercontent.com/{repo}/{branch}'}/{rel}"


def _vault_ig():
    try:
        from . import vault
        return vault.get("ig_access_token")
    except Exception:
        return None


def instagram_ready():
    return bool(os.environ.get("IG_USER_ID") and (os.environ.get("IG_ACCESS_TOKEN") or _vault_ig()))


def write_status():
    """data/status.json: one file the admin panel (or anyone) can read to see the autopilot's state."""
    p, posted = load(PENDING, None), load(POSTED, [])
    pend = None
    if p:
        pend = {k: p.get(k) for k in ("id", "source", "status", "theme", "headline", "subline", "cta", "text",
                                       "created_at", "approved_at", "warnings", "canva_edit_url", "kind")}
        pend["images"] = [raw_url(f) for f in p["files"]]
        pend["publishes_at"] = f"{POST_AT} {TZ.key}" if p["status"] == "approved" else None
    new = {"post_at": POST_AT, "timezone": TZ.key, "pending": pend, "ready_left": len(load(READY, [])),
           "recent": [{**r, "images": [raw_url(f) for f in r.get("files", [])]} for r in posted[-20:][::-1]]}
    old = load(STATUS, {})
    if {k: v for k, v in old.items() if k != "updated_at"} != new:  # only touch the file when something changed
        save(STATUS, {"updated_at": iso(), **new})


# ---------- create ----------
def _used_topics():
    return [p.get("topic") or p.get("title", "") for p in load(POSTED, [])] + \
           [r.get("topic", "") for r in load(REJECTED, [])]


def _feedback_from_rejections():
    lines = [f"- Akash rejected \"{r.get('topic', '')}\": {r['reason']}" for r in load(REJECTED, [])[-8:] if r.get("reason")]
    return ("Earlier posts Akash rejected, and why (avoid the same mistakes):\n" + "\n".join(lines)) if lines else ""


def _make_ai_post(state):
    from . import canva, llm
    k = state.get("next_ai_index", 1)
    state["next_ai_index"] = k + 1
    pid, theme = f"ai{k:04d}", content.THEMES[k % len(content.THEMES)]
    history, feedback = _feedback_from_rejections(), ""
    for attempt in range(5):
        p = content.write_post(theme, _used_topics(), "\n".join(x for x in (history, feedback) if x))
        errs = content.validate_post(p)
        if not errs:
            v = content.check_text(p)
            errs = [] if v.get("ok") else (v.get("issues") or ["rejected by the checker"])
        if not errs:
            break
        feedback = "\n".join(f"- {e}" for e in errs)
        print(f"Attempt {attempt + 1} rejected:\n{feedback}")
    else:
        raise RuntimeError(f"No post passed the checks after 5 attempts. Last problems:\n{feedback}")

    warnings, png, edit_url = [], None, ""
    for attempt in range(2):
        hero = llm.hero_image(content.HERO_STYLE + p["hero_prompt"])
        asset = canva.upload_image(hero, f"{pid}-hero.png") if hero else None
        design_id, edit_url = canva.fill_template(
            {"HEADLINE": p["headline"], "SUBLINE": p["subline"], "CTA": p["cta"]}, f"Autopilot {pid} {now():%Y-%m-%d}", asset)
        png = canva.export_jpg(design_id)
        v = content.check_image(png, p)
        warnings = [] if v.get("ok") else (v.get("issues") or ["image check failed"])
        if not warnings:
            break
        print(f"Image check problems: {warnings}")
    rel = f"media/{now():%Y-%m-%d}-{pid}/slide-01.jpg"
    (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / rel).write_bytes(png)
    return {"id": pid, "source": "ai", "kind": "single", "theme": theme, "topic": p["topic"], "headline": p["headline"],
            "subline": p["subline"], "cta": p["cta"], "hero_prompt": p["hero_prompt"], "files": [rel],
            "canva_edit_url": edit_url, "warnings": warnings}


def _take_ready():
    ready = load(READY, [])
    if not ready:
        return None
    r = ready.pop(0)
    save(READY, ready)
    folder = ROOT / r["folder"]
    files = sorted(str(f.relative_to(ROOT)) for f in folder.glob("slide-*.*"))
    if not files:
        raise RuntimeError(f"No slide images found in {r['folder']}")
    return {"id": r["id"], "source": "ready", "kind": "carousel" if len(files) > 1 else "single", "theme": r.get("theme", "verify"),
            "topic": r["title"], "headline": r["title"], "subline": "", "cta": "", "text": r["text"], "files": files, "warnings": [],
            "caption": r.get("caption"), "hashtags": r.get("hashtags")}


def create(force=False):
    state, pending = load(STATE, {}), load(PENDING, None)
    if pending:
        print(f"{pending['id']} is still {pending['status']}; not making a new post.")
        if pending["status"] == "awaiting_approval" and not force:
            tg.say(f"Reminder: post {pending['id']} is still waiting for your approval (buttons above).")
        return
    today = now().strftime("%Y-%m-%d")
    made = state.get("made", {})
    if made.get("date") != today:
        made = {"date": today, "count": 0}
    if made["count"] >= MAX_PER_DAY:
        tg.say(f"I've already made {MAX_PER_DAY} posts today, so I'll stop for now. Send /new tomorrow, or raise MAX_POSTS_CREATED_PER_DAY.")
        return
    made["count"] += 1
    state["made"] = made

    post = _take_ready() or _make_ai_post(state)
    post.update(status="awaiting_approval", created_at=iso())
    save(STATE, state)
    save(PENDING, post)

    lines = [f"New post for approval ({post['id']})", ""]
    if post["source"] == "ready":
        lines.append(post["topic"] + ("  [carousel, %d slides]" % len(post["files"]) if post["kind"] == "carousel" else ""))
    else:
        lines += [post["headline"].replace("\n", " "), post["subline"], "", "AI checks: words passed."]
        lines.append("Image check: passed." if not post["warnings"] else "Image check flagged: " + "; ".join(post["warnings"]))
    lines += ["", f"Approve to publish at {POST_AT} India time (or right away if that has passed). "
              "Reject to get a new one; you can then reply with what to change."]
    text = "\n".join(lines)
    if tg.ready():
        images = [(Path(f).name, (ROOT / f).read_bytes()) for f in post["files"]]
        if len(images) == 1:
            post["telegram_message_id"] = tg.send_for_approval(images[0][1], text, post["id"], images[0][0])
        else:
            post["telegram_message_id"] = tg.send_album(images, text, post["id"])
        save(PENDING, post)
    else:
        print("::warning::Telegram is not set up, so nobody was asked to approve.\n" + text)
    write_status()
    print(f"Created {post['id']} and asked for approval.")


# ---------- check (approvals + publishing) ----------
def _reject(pending, state, reason="", by="phone"):
    rej = load(REJECTED, [])
    rej.append({"id": pending["id"], "topic": pending.get("topic", ""), "headline": pending.get("headline", ""),
                "reason": reason, "at": iso(), "by": by})
    save(REJECTED, rej[-100:])
    if pending.get("telegram_message_id"):
        tg.close_buttons(pending["telegram_message_id"], f"Rejected {pending['id']}." + (
            "" if reason else " Reply with what to change (or 'skip') and I'll make a new one."))
    PENDING.unlink(missing_ok=True)
    state["replace_after"] = iso() if reason else iso(now() + dt.timedelta(minutes=REASON_WAIT_MIN))


def _approve(pending, by="phone"):
    if pending["status"] == "approved":
        return
    pending.update(status="approved", approved_at=iso(), approved_by=by)
    save(PENDING, pending)
    if pending.get("telegram_message_id"):
        when = "Posting it now." if _time_to_post() else f"It goes live at {POST_AT} India time (within about 15 minutes of it)."
        tg.close_buttons(pending["telegram_message_id"], f"Approved {pending['id']}. {when}")


def _time_to_post():
    h, m = map(int, POST_AT.split(":"))
    return now() >= now().replace(hour=h, minute=m, second=0, microsecond=0)


def publish(pending):
    post_text = content.post_text(pending)
    feedback, cap = "", None
    if pending.get("caption") and pending.get("hashtags"):  # ready-made post with an approved caption
        cap = content.final_caption({"caption": pending["caption"], "hashtags": pending["hashtags"]})
    for attempt in range(0 if cap else 4):
        c = content.write_caption(post_text, feedback)
        errs = content.validate_caption(c)
        if not errs:
            v = content.check_caption(post_text, c)
            errs = [] if v.get("ok") else (v.get("issues") or ["rejected by the checker"])
        if not errs:
            cap = content.final_caption(c)
            break
        feedback = "\n".join(f"- {e}" for e in errs)
        print(f"Caption attempt {attempt + 1} rejected:\n{feedback}")
    if not cap:
        tg.say(f"I couldn't write a caption for {pending['id']} that passed the checks. I'll try again on the next run.\n{feedback}")
        return
    urls = [raw_url(f) for f in pending["files"]]
    alt = (pending.get("headline") or pending.get("topic", "")).replace("\n", " ") + ". " + (pending.get("subline") or "")
    if DRY_RUN or not instagram_ready():
        why = "DRY RUN" if DRY_RUN else "Instagram is not connected yet (IG_USER_ID / IG_ACCESS_TOKEN)"
        print(f"{why}: would publish\n" + "\n".join(urls) + "\n\n" + cap)
        if DRY_RUN or not pending.get("ig_missing_told"):  # say it once, not every 15 minutes
            tg.say(f"{why}, so nothing was posted yet. It will post as soon as it can. Caption:\n\n{cap}")
            if not DRY_RUN:
                pending["ig_missing_told"] = True
                save(PENDING, pending)
        return
    from .instagram import publish as ig_publish
    for u in urls:  # Instagram downloads the images, so they must be public first
        for _ in range(30):
            r = requests.head(u, timeout=20, allow_redirects=True)
            if r.status_code == 200:
                break
            time.sleep(10)
        else:
            raise RuntimeError(f"Image not reachable: {u}. Is the repository public?")
    media_id = ig_publish(urls, cap, alt.strip())
    posted = load(POSTED, [])
    posted.append({"date": now().strftime("%Y-%m-%d"), "posted_at": iso(), "id": pending["id"], "source": pending["source"],
                   "theme": pending.get("theme"), "kind": pending.get("kind"), "topic": pending.get("topic"),
                   "title": (pending.get("headline") or pending.get("topic", "")).replace("\n", " "),
                   "caption": cap, "files": pending["files"], "instagram_media_id": media_id})
    save(POSTED, posted)
    PENDING.unlink(missing_ok=True)
    tg.say(f"Posted {pending['id']} to Instagram.\n\n{cap[:600]}")
    print(f"Published {pending['id']} (media id {media_id}).")


def _status_text():
    p = load(PENDING, None)
    if not p:
        return f"Nothing waiting. Ready-made posts left: {len(load(READY, []))}. Send /new to make one now."
    head = (p.get("headline") or p.get("topic", "")).replace("\n", " ")
    return f"{p['id']} is {p['status'].replace('_', ' ')}: {head}"


def check():
    state, pending = load(STATE, {}), load(PENDING, None)
    events = []
    decision = (os.environ.get("DECISION") or "").strip().lower()  # from the admin panel / workflow button
    if decision in ("approve", "reject"):
        events.append({"kind": decision, "post_id": os.environ.get("DECISION_POST_ID") or (pending or {}).get("id"),
                       "reason": os.environ.get("REASON", ""), "by": "admin"})
    if tg.ready():
        evs, state["tg_offset"] = tg.updates(state.get("tg_offset", 0))
        events += evs
    want_new = False
    for e in events:
        pending = load(PENDING, None)
        if e["kind"] in ("approve", "reject"):
            if not pending or e.get("post_id") != pending["id"]:
                tg.say(f"That button was for {e.get('post_id')}, which is no longer waiting. " + _status_text())
                continue
            if e["kind"] == "approve":
                _approve(pending, e.get("by", "phone"))
            else:
                _reject(pending, state, e.get("reason", ""), e.get("by", "phone"))
            continue
        text = e["text"]
        low = text.lower()
        if low.startswith("/start"):
            tg.say("Hi. I'll send each new TrustBrokerHub post here for approval. Commands: /status, /new, /approve, /reject <reason>.")
        elif low.startswith("/status"):
            tg.say(_status_text())
        elif low.startswith("/new"):
            want_new = True
        elif low.startswith("/approve") and pending:
            _approve(pending)
        elif low.startswith("/reject") and pending:
            _reject(pending, state, text[7:].strip())
        elif state.get("replace_after") and not pending:  # a reason after tapping Reject
            rej = load(REJECTED, [])
            if rej and low not in ("skip", "no", "none"):
                rej[-1]["reason"] = text
                save(REJECTED, rej)
            state["replace_after"] = iso()
            tg.say("Noted. Making a new post now.")
        else:
            tg.say("I only understand the buttons, /status, /new, /approve and /reject <reason>.")
    save(STATE, state)

    pending = load(PENDING, None)
    due = state.get("replace_after") and now() >= dt.datetime.fromisoformat(state["replace_after"])
    if not pending and (want_new or due):
        state.pop("replace_after", None)
        save(STATE, state)
        create(force=True)
    elif pending and want_new:
        tg.say(f"{pending['id']} is still {pending['status'].replace('_', ' ')}. Reject it first if you want a new one.")
    pending = load(PENDING, None)
    if pending and pending["status"] == "approved" and _time_to_post():
        publish(pending)
    write_status()


# ---------- setup helpers ----------
def setup(step, value=""):
    if step == "telegram":
        cid, name = tg.find_chat_id()
        if not cid:
            sys.exit("No message found. Open your bot in Telegram, press Start (or send 'hi'), then run this again.")
        state = load(STATE, {})
        state["telegram_chat_id"] = str(cid)
        save(STATE, state)
        tg.say(f"Connected. Hi {name}, approvals for TrustBrokerHub posts will come here.")
        print(f"Saved the Telegram chat for {name}.")
    elif step == "canva-link":
        from .canva import auth_link, REDIRECT
        from .canva import _env
        cid = _env("CANVA_CLIENT_ID")
        print(f"Client ID in use: {cid}  ({len(cid)} characters). It must match the Client ID on canva.com/developers exactly.")
        print("\n1. Open this link, log in to Canva and click Allow:\n\n" + auth_link() +
              f"\n\n2. The browser then tries to open {REDIRECT}?code=... and shows an error page. That is expected."
              "\n3. Copy the WHOLE address from the address bar and run this workflow again with step 'canva-code',"
              " pasting it into the 'value' box. Do it within a few minutes.\n")
    elif step == "canva-code":
        from .canva import connect
        connect(value)
        print("Canva connected. Login saved (encrypted) in data/vault.json.")
    elif step == "test":
        from . import llm
        for role in ("writer", "checker"):
            print(f"{role}: {llm.provider(role)} ->", llm.ask_json(role, "Reply in JSON.", 'Return {"ok": true}'))
        from . import canva, vault
        if os.environ.get("CANVA_REFRESH_TOKEN") or vault.get("canva_refresh_token"):
            canva.token()
            print("Canva: login works.")
        else:
            print("Canva: not connected yet (run canva-link and canva-code)")
        print("Telegram:", "ready" if tg.ready() else "not set up")
        tg.say("Test message from the TrustBrokerHub autopilot. Everything that printed OK is working.")
        print("Instagram:", "secrets present" if instagram_ready() else "not set up")
    else:
        sys.exit(f"Unknown setup step {step}")


def refresh_token():
    from .instagram import refresh_token as ig_refresh
    from . import vault
    token, expires = ig_refresh()
    vault.put("ig_access_token", token)
    print(f"Instagram token refreshed and saved; valid for about {int(expires or 0) // 86400} days.")


def status():
    print(_status_text())
    for i, r in enumerate(load(READY, [])):
        print(f"  ready {i + 1}. {r['id']}: {r['title']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "setup":
        setup(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    else:
        {"create": create, "check": check, "refresh-token": refresh_token, "status": status}[cmd]()

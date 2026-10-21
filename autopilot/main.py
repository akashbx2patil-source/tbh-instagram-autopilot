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

from . import settings as cfg
SETTINGS = cfg.apply()  # admin-panel settings win over GitHub variables
from . import content, telegram as tg

ROOT = Path(__file__).resolve().parent.parent
DATA, MEDIA = ROOT / "data", ROOT / "media"
PENDING, POSTED, STATE = DATA / "pending.json", DATA / "posted.json", DATA / "state.json"
READY, REJECTED, STATUS = DATA / "ready.json", DATA / "rejected.json", DATA / "status.json"
IDEAS, EDITS, ISSUES = DATA / "ideas.json", DATA / "admin_edits.json", DATA / "issues.json"
# Files the admin panel writes (the autopilot only reads them): settings.json, ready.json, ideas.json,
# admin_edits.json. Files the autopilot writes: state, pending, posted, rejected, issues, status, vault.
TZ = ZoneInfo(os.environ.get("TIMEZONE") or "Asia/Kolkata")
POST_AT = os.environ.get("POST_AT") or "18:30"
MAX_PER_DAY = int(os.environ.get("MAX_POSTS_CREATED_PER_DAY") or "4")
LISTEN_MIN = float(os.environ.get("LISTEN_MINUTES") or "0")
DRY_RUN = (os.environ.get("DRY_RUN") or "false").lower() == "true"
PUBLISH_NOW = (os.environ.get("PUBLISH_NOW") or "false").lower() == "true"


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
    """data/status.json: everything the admin panel shows, in one file."""
    p, posted, state = load(PENDING, None), load(POSTED, []), load(STATE, {})
    pend = None
    if p:
        pend = {k: p.get(k) for k in ("id", "source", "kind", "status", "theme", "topic", "headline", "subline", "cta", "text",
                                       "caption", "hashtags", "created_at", "approved_at", "approved_by", "warnings",
                                       "canva_edit_url", "edit_rev", "edit_error", "idea")}
        pend["images"] = [raw_url(f) for f in p["files"]]
        pend["publishes_at"] = POST_AT if p["status"] == "approved" else None
    ideas_done = set(state.get("ideas_done", []))
    new = {"post_at": POST_AT, "timezone": TZ.key, "paused": bool(SETTINGS.get("paused")),
           "writer": os.environ.get("WRITER") or "auto", "checker": os.environ.get("CHECKER") or "auto",
           "pending": pend,
           "queue": [{**r, "images": [raw_url(str(f.relative_to(ROOT))) for f in sorted((ROOT / r["folder"]).glob("slide-*.*"))]}
                     for r in _ready_left(state)],
           "ideas": [i for i in load(IDEAS, []) if i.get("id") not in ideas_done],
           "recent": [{**r, "images": [raw_url(f) for f in r.get("files", [])]} for r in posted[-60:][::-1]],
           "posted_count": len(posted),
           "rejected": load(REJECTED, [])[-20:][::-1],
           "issues": load(ISSUES, [])[-30:][::-1]}
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
    themes = [t for t in SETTINGS.get("themes") or [] if t in content.THEMES] or content.THEMES
    pid, theme = f"ai{k:04d}", themes[k % len(themes)]
    idea = _take_idea(state)
    history, feedback = _feedback_from_rejections(), ""
    for attempt in range(5):
        p = content.write_post(theme, _used_topics(), "\n".join(x for x in (history, feedback) if x),
                               topic_request=idea, extra=SETTINGS.get("extra_instructions", ""))
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

    warnings, png, edit_url, asset = [], None, "", None
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
            "canva_edit_url": edit_url, "warnings": warnings, "hero_asset_id": asset, "idea": idea}


def _ready_left(state=None):
    done = set((state or load(STATE, {})).get("ready_done", []))
    return [r for r in load(READY, []) if r["id"] not in done]


def _take_ready(state):
    left = _ready_left(state)
    if not left:
        return None
    r = left[0]
    state.setdefault("ready_done", []).append(r["id"])
    folder = ROOT / r["folder"]
    files = sorted(str(f.relative_to(ROOT)) for f in folder.glob("slide-*.*"))
    if not files:
        raise RuntimeError(f"No slide images found in {r['folder']}")
    return {"id": r["id"], "source": "ready", "kind": "carousel" if len(files) > 1 else "single", "theme": r.get("theme", "verify"),
            "topic": r["title"], "headline": r["title"], "subline": "", "cta": "", "text": r["text"], "files": files, "warnings": [],
            "caption": r.get("caption"), "hashtags": r.get("hashtags")}


def _take_idea(state):
    done = set(state.get("ideas_done", []))
    for i in load(IDEAS, []):
        if i.get("id") and i["id"] not in done and i.get("topic", "").strip():
            state.setdefault("ideas_done", []).append(i["id"])
            return i["topic"].strip()
    return ""


def create(force=False):
    if SETTINGS.get("paused"):
        print("Paused in the admin panel; not making a post.")
        return
    state, pending = load(STATE, {}), load(PENDING, None)
    if pending:
        print(f"{pending['id']} is still {pending['status']}; not making a new post.")
        if pending["status"] == "awaiting_approval" and not force:
            # Re-send the post itself with fresh buttons, so it is at the bottom of the chat, not days up.
            if pending.get("telegram_message_id"):
                tg.close_buttons(pending["telegram_message_id"], f"Reminder: {pending['id']} is still waiting. Here it is again:")
            days = (now() - dt.datetime.fromisoformat(pending["created_at"])).days if pending.get("created_at") else 0
            _send_for_approval(pending, f"Still waiting for your approval ({pending['id']}"
                                        + (f", {days} days" if days else "") + "). Nothing posts until you tap Approve.\n\n"
                                        + (pending.get("headline") or pending.get("topic", "")).replace("\n", " "))
            save(PENDING, pending)
        return
    today = now().strftime("%Y-%m-%d")
    made = state.get("made", {})
    if made.get("date") != today:
        made = {"date": today, "count": 0}
    if made["count"] >= MAX_PER_DAY:
        tg.say(f"I've already made {MAX_PER_DAY} posts today, so I'll stop for now. Send /new tomorrow, or raise the daily limit in the admin panel.")
        return
    made["count"] += 1
    state["made"] = made

    post = _take_ready(state) or _make_ai_post(state)
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
    _send_for_approval(post, text)
    save(PENDING, post)
    write_status()
    print(f"Created {post['id']} and asked for approval.")


# ---------- admin panel edits ----------
def _send_for_approval(post, text):
    if not tg.ready():
        print("::warning::Telegram is not set up, so nobody was asked to approve.\n" + text)
        return
    images = [(Path(f).name, (ROOT / f).read_bytes()) for f in post["files"]]
    if len(images) == 1:
        post["telegram_message_id"] = tg.send_for_approval(images[0][1], text, post["id"], images[0][0])
    else:
        post["telegram_message_id"] = tg.send_album(images, text, post["id"])


def _apply_admin_edits():
    """Apply edits made in the admin panel (data/admin_edits.json) to the post that is waiting."""
    p = load(PENDING, None)
    if not p:
        return
    e = load(EDITS, {}).get(p["id"])
    if not e or int(e.get("rev", 0)) <= int(p.get("edit_rev", 0)):
        return
    p["edit_rev"] = int(e["rev"])
    p.pop("edit_error", None)
    if "caption" in e:
        if str(e["caption"] or "").strip():
            p["caption"] = str(e["caption"]).strip()
        else:
            p.pop("caption", None)  # blank = let the AI write it
    if "hashtags" in e:
        tags = e["hashtags"] if isinstance(e["hashtags"], list) else str(e["hashtags"] or "").replace(",", " ").split()
        tags = ["#" + t.lstrip("#").lower() for t in tags if t.strip("# ")]
        if tags:
            p["hashtags"] = tags
        else:
            p.pop("hashtags", None)
    new = {k: str(e[k]).replace("\r", "") for k in ("headline", "subline", "cta") if e.get(k) not in (None, "")}
    if p["source"] == "ai" and any(new.get(k) != p.get(k) for k in new):
        cand = {**p, **new}
        errs = content.validate_post(cand)
        if errs:
            p["edit_error"] = errs
            save(PENDING, p)
            tg.say(f"Your edit to {p['id']} was not applied: " + "; ".join(errs))
            return
        from . import canva
        design_id, edit_url = canva.fill_template({"HEADLINE": cand["headline"], "SUBLINE": cand["subline"], "CTA": cand["cta"]},
                                                  f"Autopilot {p['id']} edit {p['edit_rev']}", p.get("hero_asset_id"))
        img = canva.export_jpg(design_id)
        rel = f"media/{now():%Y-%m-%d}-{p['id']}/slide-01-r{p['edit_rev']}.jpg"
        (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / rel).write_bytes(img)
        if p.get("telegram_message_id") and p["status"] == "awaiting_approval":
            tg.close_buttons(p["telegram_message_id"], f"{p['id']} was edited in the admin panel; new version below.")
        p.update(new, files=[rel], canva_edit_url=edit_url, status="awaiting_approval", warnings=[])
        p.pop("approved_at", None)
        _send_for_approval(p, f"Edited post for approval ({p['id']})\n\n{p['headline'].replace(chr(10), ' ')}\n{p['subline']}")
    save(PENDING, p)
    print(f"Applied admin edit {p['edit_rev']} to {p['id']}")


HINTS = [("503", "Gemini was overloaded. It retries by itself; nothing to do."),
         ("high demand", "Gemini was overloaded. It retries by itself; nothing to do."),
         ("no longer available", "The AI model was retired. Leave the model setting blank so it picks the newest."),
         ("Canva login failed", "Canva needs reconnecting: GitHub Actions -> Setup -> canva-link, then canva-code."),
         ("access token", "The Instagram token may have expired: generate a new one and update the IG_ACCESS_TOKEN secret."),
         ("Session has expired", "The Instagram token expired: generate a new one and update the IG_ACCESS_TOKEN secret."),
         ("API key", "An AI key is missing or wrong: check GEMINI_API_KEY / OPENAI_API_KEY in GitHub secrets."),
         ("No post passed the checks", "The AI could not write a post that passed the rules. Try again, or simplify the extra instructions.")]


def record_issue(step, err):
    msg = str(err).strip()
    hint = next((h for k, h in HINTS if k.lower() in msg.lower()), "")
    issues = load(ISSUES, [])
    issues.append({"at": iso(), "step": step, "message": msg[:1500], "hint": hint,
                   "run": f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"})
    save(ISSUES, issues[-50:])
    tg.say(f"Autopilot problem during {step}: {msg[:300]}" + (f"\nWhat to do: {hint}" if hint else ""))


# ---------- check (approvals + publishing) ----------
def _reject(pending, state, reason="", by="phone"):
    rej = load(REJECTED, [])
    rej.append({"id": pending["id"], "topic": pending.get("topic", ""), "headline": pending.get("headline", ""),
                "reason": reason, "at": iso(), "by": by})
    save(REJECTED, rej[-100:])
    if pending.get("telegram_message_id"):
        tg.close_buttons(pending["telegram_message_id"], f"Rejected {pending['id']}. Making a new one now (about a minute)." + (
            "" if reason else "\nTip: send /reject followed by what to change, e.g. '/reject too negative', and I'll use it."))
    PENDING.unlink(missing_ok=True)
    state["replace_after"] = iso()  # make the replacement straight away
    state["last_rejected"] = pending["id"]


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
    always = SETTINGS.get("always_hashtags") or []
    if pending.get("caption"):  # caption from a ready-made post or written in the admin panel
        cap = content.final_caption({"caption": pending["caption"], "hashtags": pending.get("hashtags") or []}, always)
    for attempt in range(0 if cap else 4):
        c = content.write_caption(post_text, feedback)
        errs = content.validate_caption(c)
        if not errs:
            v = content.check_caption(post_text, c)
            errs = [] if v.get("ok") else (v.get("issues") or ["rejected by the checker"])
        if not errs:
            cap = content.final_caption(c, always)
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
    try:
        from .instagram import permalink
        link = permalink(media_id)
    except Exception:
        link = ""
    posted = load(POSTED, [])
    posted.append({"date": now().strftime("%Y-%m-%d"), "posted_at": iso(), "id": pending["id"], "source": pending["source"],
                   "theme": pending.get("theme"), "kind": pending.get("kind"), "topic": pending.get("topic"),
                   "title": (pending.get("headline") or pending.get("topic", "")).replace("\n", " "),
                   "caption": cap, "files": pending["files"], "instagram_media_id": media_id, "permalink": link,
                   "approved_by": pending.get("approved_by", "")})
    save(POSTED, posted)
    PENDING.unlink(missing_ok=True)
    tg.say(f"Posted {pending['id']} to Instagram.\n\n{cap[:600]}")
    print(f"Published {pending['id']} (media id {media_id}).")


def _status_text():
    p = load(PENDING, None)
    if not p:
        return f"Nothing waiting. Ready-made posts left: {len(_ready_left())}. Send /new to make one now."
    head = (p.get("headline") or p.get("topic", "")).replace("\n", " ")
    return f"{p['id']} is {p['status'].replace('_', ' ')}: {head}"


def _pass(long_poll=0):
    """Read new decisions, act on them, make replacements, publish if due. Returns True if anything changed."""
    _apply_admin_edits()
    state, pending = load(STATE, {}), load(PENDING, None)
    before = json.dumps([state, pending, load(POSTED, [])], sort_keys=True)
    events = []
    decision = (os.environ.pop("DECISION", "") or "").strip().lower()  # from the admin panel / workflow button (once)
    if decision in ("approve", "reject"):
        events.append({"kind": decision, "post_id": os.environ.get("DECISION_POST_ID") or (pending or {}).get("id"),
                       "reason": os.environ.get("REASON", ""), "by": "admin"})
    if tg.ready():
        evs, state["tg_offset"] = tg.updates(state.get("tg_offset", 0), long_poll)
        events += evs
    save(STATE, state)
    want_new = False
    for e in events:
        state, pending = load(STATE, {}), load(PENDING, None)
        if e["kind"] in ("approve", "reject"):
            if not pending or e.get("post_id") != pending["id"]:
                tg.say(f"That button was for {e.get('post_id')}, which is no longer waiting. " + _status_text())
                continue
            if e["kind"] == "approve":
                _approve(pending, e.get("by", "phone"))
            else:
                _reject(pending, state, e.get("reason", ""), e.get("by", "phone"))
                save(STATE, state)
            continue
        text = e["text"]
        low = text.lower()
        if low.startswith("/start"):
            tg.say("Hi. I'll send each new TrustBrokerHub post here for approval. Commands: /status, /new, /approve, /reject <what to change>.")
        elif low.startswith("/status"):
            tg.say(_status_text())
        elif low.startswith("/new"):
            want_new = True
        elif low.startswith("/approve") and pending:
            _approve(pending)
        elif low.startswith("/reject") and pending:
            _reject(pending, state, text[7:].strip())
            save(STATE, state)
        elif state.get("last_rejected"):  # a note after tapping Reject: keep it so future posts learn from it
            rej = load(REJECTED, [])
            if rej and rej[-1]["id"] == state["last_rejected"] and low not in ("skip", "no", "none"):
                rej[-1]["reason"] = (rej[-1].get("reason", "") + " " + text).strip()
                save(REJECTED, rej)
                tg.say("Noted. I'll keep that in mind for the next posts.")
        else:
            tg.say("I understand the buttons, /status, /new, /approve and /reject <what to change>.")

    state, pending = load(STATE, {}), load(PENDING, None)
    due = state.get("replace_after") and now() >= dt.datetime.fromisoformat(state["replace_after"])
    if not pending and (want_new or due):
        state.pop("replace_after", None)
        save(STATE, state)
        create(force=True)
    elif pending and want_new:
        tg.say(f"{pending['id']} is still {pending['status'].replace('_', ' ')}. Reject it first if you want a new one.")
    pending = load(PENDING, None)
    if pending and pending["status"] == "approved" and (_time_to_post() or PUBLISH_NOW) and not SETTINGS.get("paused"):
        publish(pending)
    write_status()
    return json.dumps([load(STATE, {}), load(PENDING, None), load(POSTED, [])], sort_keys=True) != before


def _git_save(msg):
    """Inside GitHub Actions: commit and push data/ and media/ now, so nothing is lost mid-run."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
    run("config", "user.name", "instagram-autopilot")
    run("config", "user.email", "actions@users.noreply.github.com")
    run("add", "data", "media")
    if run("diff", "--cached", "--quiet").returncode != 0:
        run("commit", "-q", "-m", msg)
        run("pull", "-q", "--rebase")
        if run("push", "-q").returncode != 0:
            print("::warning::push failed; will retry at the end of the run")


def listen(minutes=None):
    """Stay awake for a few minutes so Approve / Reject taps are handled within seconds."""
    minutes = LISTEN_MIN if minutes is None else minutes
    end = time.time() + minutes * 60
    while time.time() < end and tg.ready():
        p, st = load(PENDING, None), load(STATE, {})
        waiting = (p and p["status"] == "awaiting_approval") or st.get("replace_after")
        h, m = map(int, POST_AT.split(":"))
        post_soon = p and p["status"] == "approved" and \
            now() + dt.timedelta(seconds=end - time.time()) >= now().replace(hour=h, minute=m, second=0)
        if not (waiting or post_soon):
            break
        if _pass(long_poll=25):
            _git_save(f"Autopilot {now():%Y-%m-%d %H:%M}")


def check():
    changed = _pass()
    _git_save(f"Autopilot {now():%Y-%m-%d %H:%M}")
    # Stay awake only when something just happened (a new post, a tap). Quiet checks end at once,
    # so frequent pings from an outside timer (cron-job.org) stay cheap.
    if changed or os.environ.get("ALWAYS_LISTEN") == "true":
        listen()


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
    for i, r in enumerate(_ready_left()):
        print(f"  ready {i + 1}. {r['id']}: {r['title']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    try:
        if cmd == "setup":
            setup(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
        elif cmd == "create":
            _apply_admin_edits()
            create()
            write_status()
            _git_save(f"Autopilot {now():%Y-%m-%d %H:%M}")
            listen()
        else:
            {"check": check, "refresh-token": refresh_token, "status": status}[cmd]()
    except Exception as e:
        if cmd != "setup":
            try:
                record_issue(cmd, e)
                write_status()
            except Exception as e2:
                print(f"could not record the issue: {e2}")
        raise

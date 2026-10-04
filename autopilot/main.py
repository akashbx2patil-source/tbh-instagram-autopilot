"""TrustBrokerHub Instagram autopilot.

python -m autopilot.main prepare        # pick today's post (AI-generate more if the queue is low) and render it
python -m autopilot.main publish        # publish today's rendered post to Instagram
python -m autopilot.main refresh-token  # renew the Instagram token (and update the GitHub secret if GH_PAT is set)
python -m autopilot.main status         # show what is queued
"""
import datetime as dt, json, os, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA, MEDIA = ROOT / "data", ROOT / "media"
QUEUE, POSTED, TODAY, STATE = DATA / "queue.json", DATA / "posted.json", DATA / "today.json", DATA / "state.json"
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Kolkata"))
LOW_WATER = int(os.environ.get("QUEUE_LOW_WATER", "7"))   # generate more when fewer than this remain
BATCH = int(os.environ.get("GENERATE_BATCH", "7"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, obj):
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def today():
    return dt.datetime.now(TZ).date().isoformat()


def prepare():
    from .render import render_post, instagram_caption, alt
    from .generate import generate, title_of
    date = today()
    posted = load(POSTED, [])
    if any(p["date"] == date for p in posted):
        print(f"Already posted for {date}. Nothing to do."); return
    current = load(TODAY, None)
    if current and current["date"] == date:
        print(f"Today's post already prepared: {current['post']['id']}"); return

    queue, state = load(QUEUE, []), load(STATE, {"next_ai_index": 1})
    if current:  # an earlier day's post never got published: put it back at the front
        print(f"Re-queueing unpublished post {current['post']['id']} from {current['date']}.")
        queue.insert(0, current["post"]); TODAY.unlink()
    if len(queue) < LOW_WATER:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            if not queue:
                sys.exit("Queue is empty and ANTHROPIC_API_KEY is not set, so no post can be made.")
            print(f"Queue is low ({len(queue)}) but ANTHROPIC_API_KEY is not set; skipping generation.")
        else:
            used_ids = [p["id"] for p in posted] + [p["id"] for p in queue]
            used_titles = [p["title"] for p in posted] + [title_of(p) for p in queue]
            print(f"Queue has {len(queue)} posts; asking Claude for {BATCH} more...")
            new, state["next_ai_index"] = generate(BATCH, state["next_ai_index"], used_ids, used_titles)
            for p in new:  # JSON-safe
                if p["kind"] == "carousel": p["data"]["slides"] = [list(s) for s in p["data"]["slides"]]
            queue += new
            save(QUEUE, queue); save(STATE, state)
            print("Added: " + ", ".join(f'{p["id"]} ({title_of(p)})' for p in new))

    post = queue.pop(0)
    if post["kind"] == "carousel":
        post["data"]["slides"] = [tuple(s) for s in post["data"]["slides"]]
    files = render_post(post, MEDIA / date)
    if post["kind"] == "carousel":
        post["data"]["slides"] = [list(s) for s in post["data"]["slides"]]
    save(QUEUE, queue)
    save(TODAY, {"date": date, "post": post, "files": [f.name for f in files],
                 "caption": instagram_caption(post), "alt": alt(post), "title": title_of(post)})
    print(f"Prepared {post['id']} for {date}: {len(files)} image(s). {len(queue)} left in queue.")


def media_url(date, name):
    base = os.environ.get("MEDIA_BASE_URL")
    if not base:
        repo, branch = os.environ["GITHUB_REPOSITORY"], os.environ.get("GITHUB_REF_NAME", "main")
        base = f"https://raw.githubusercontent.com/{repo}/{branch}/media"
    return f"{base.rstrip('/')}/{date}/{name}"


def wait_public(urls, timeout=300):
    end = time.time() + timeout
    for u in urls:
        while True:
            try:
                r = requests.head(u, timeout=20, allow_redirects=True)
                if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
                    break
            except requests.RequestException:
                pass
            if time.time() > end:
                sys.exit(f"Image not publicly reachable: {u}. Is the repository public?")
            time.sleep(10)


def publish():
    from .instagram import publish as ig_publish
    cur = load(TODAY, None)
    date = today()
    if not cur or cur["date"] != date:
        print("Nothing prepared for today."); return
    urls = [media_url(date, f) for f in cur["files"]]
    if DRY_RUN:
        print("DRY RUN — would publish:\n" + "\n".join(urls) + "\n\n" + cur["caption"]); return
    wait_public(urls)
    media_id = ig_publish(urls, cur["caption"], cur["alt"])
    posted = load(POSTED, [])
    posted.append({"date": date, "id": cur["post"]["id"], "theme": cur["post"]["theme"], "kind": cur["post"]["kind"],
                   "title": cur["title"], "instagram_media_id": media_id})
    save(POSTED, posted)
    TODAY.unlink()
    print(f"Published {cur['post']['id']} to Instagram (media id {media_id}).")


def refresh_token():
    from .instagram import refresh_token as ig_refresh
    token, expires = ig_refresh()
    print(f"Instagram token refreshed; valid for about {int(expires or 0) // 86400} days.")
    pat, repo = os.environ.get("GH_PAT"), os.environ.get("GITHUB_REPOSITORY")
    if not pat:
        print("::warning::GH_PAT is not set, so the new token could not be saved. Update the IG_ACCESS_TOKEN secret by hand before it expires.")
        return
    from base64 import b64encode
    from nacl import encoding, public
    h = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
    key = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=h, timeout=30).json()
    box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    enc = b64encode(box.encrypt(token.encode())).decode()
    r = requests.put(f"https://api.github.com/repos/{repo}/actions/secrets/IG_ACCESS_TOKEN", headers=h,
                     json={"encrypted_value": enc, "key_id": key["key_id"]}, timeout=30)
    if r.status_code not in (201, 204):
        sys.exit(f"Could not update the IG_ACCESS_TOKEN secret: {r.status_code} {r.text}")
    print("Saved the new token to the IG_ACCESS_TOKEN secret.")


def status():
    from .generate import title_of
    q, posted = load(QUEUE, []), load(POSTED, [])
    print(f"Posted so far: {len(posted)}. Queued: {len(q)}.")
    for i, p in enumerate(q[:10]):
        print(f"  {i+1:>2}. {p['id']:<8} {p['theme']:<7} {p['kind']:<10} {title_of(p)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"prepare": prepare, "publish": publish, "refresh-token": refresh_token, "status": status}[cmd]()

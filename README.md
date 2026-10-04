# TrustBrokerHub Instagram Autopilot

Posts to Instagram every day at **18:30 IST** with no manual work.

## What happens every day

1. GitHub wakes up the autopilot.
2. It takes the next post from the queue.
3. When fewer than 7 posts are left, **Claude writes 7 new ones** in the TrustBrokerHub voice.
4. Every AI post must pass two checks:
   - an automatic rule check: lengths, banned phrases, no exclamation marks;
   - a second AI pass that acts as a compliance reviewer and rejects any post that:
     - gives advice;
     - uses a number that is not on the approved facts list;
     - names a broker as a scam;
     - quotes Trust Scores.

   Rejected posts are rewritten automatically.
5. It renders the image or carousel in your brand style.
6. It publishes the post to Instagram through Meta's official API.
7. It records what was posted, so nothing is ever posted twice.

The queue starts with the 60 posts already written and checked for you. That covers the first 60 days. After that, every post is AI-generated.

> Do **not** also upload the earlier 60-post zip to Meta Business Suite. The autopilot posts those same 60, so you'd post everything twice.

---

## One-time setup (about 1–2 hours)

### Step 1 — Make your Instagram a professional account
In the Instagram app, go to **Profile → ☰ → Account type and tools → Switch to professional account**, and choose **Business**.

### Step 2 — Create a Meta app and get your Instagram token
1. Go to **developers.facebook.com** and log in. Then go to **My Apps → Create app**.
2. When asked for a use case, pick the Instagram one. It's labelled something like **"Manage messaging & content on Instagram"**.
3. In the app dashboard, open **Instagram → API setup with Instagram login**.
4. Under **Generate access tokens**, click **Add account**, log in to your TrustBrokerHub Instagram account, and allow the permissions.
5. Copy and keep safe:
   - the **Instagram account ID** (a long number shown next to your account);
   - the **access token** (a long string). This is a 60-day token, and the autopilot renews it for you.

   If Meta asks you to add the Instagram account under **App roles → Roles** as an Instagram tester, do that, then accept the invite inside Instagram.

   Meta changes this screen often, so the labels may differ slightly. The current guide is at developers.facebook.com/docs/instagram-platform.

### Step 3 — Get a Claude API key
1. Go to **console.anthropic.com**, then **API keys → Create key**, and copy it.
2. Add a small amount of credit under **Billing**.

Writing 7 posts a week costs very little.

### Step 4 — Put the autopilot on GitHub
1. Create a free account at **github.com** if you don't have one.
2. Click **New repository**:
   - Name it `tbh-instagram-autopilot`.
   - Set it to **Public**. Instagram downloads each image from a public link. Your keys stay hidden in GitHub Secrets and never appear in the files.
3. Upload every file and folder from this zip, including the `.github` folder. The easiest way is **GitHub Desktop**: clone the empty repo, copy the files in, then commit and push.
   If you use the website's **Add file → Upload files**, check that the `.github/workflows` folder actually appears afterwards.
4. In the repo, open **Settings → Actions → General → Workflow permissions** and select **Read and write permissions**, then click **Save**.

### Step 5 — Add your keys as secrets
Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Claude API key |
| `IG_USER_ID` | your Instagram account ID |
| `IG_ACCESS_TOKEN` | your Instagram access token |
| `GH_PAT` | a GitHub token so the autopilot can renew the Instagram token by itself (see below) |

To create `GH_PAT`:
1. In GitHub, go to **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Under **Repository access**, choose **Only select repositories** and pick `tbh-instagram-autopilot`.
3. Under **Permissions**, set **Secrets** to **Read and write**.
4. Generate the token and paste it in as the secret.

Without `GH_PAT`, posting still works. You'd just need to paste in a fresh Instagram token yourself every 60 days.

### Step 6 — Test, then go live
1. Open the **Actions** tab, select **Daily Instagram post**, and click **Run workflow**. Leave **Dry run** ticked.
   - It should finish green, and a new folder of images should appear under `media/`.
   - Open the log and expand **Publish to Instagram** to see the exact caption it would post.
2. Run it again with **Dry run unticked**. Your first post goes live on Instagram.
3. Done. From now on it runs every day at 18:30 IST by itself. GitHub sometimes starts scheduled jobs a few minutes late.

Also run **Refresh Instagram token** once by hand to check the renewal works. It needs the token to be at least 24 hours old, so do this the day after setup.

---

## Day-to-day controls

| You want to… | Do this |
|---|---|
| Pause posting | **Settings → Secrets and variables → Actions → Variables**, add `PAUSED` = `true`. Delete it to resume. |
| See what's coming next | Open `data/queue.json` (posts are in posting order). |
| Remove or edit a queued post | Edit `data/queue.json` on GitHub and commit. |
| See what's been posted | Open `data/posted.json`. |
| Change the posting time | Edit the `cron` line in `.github/workflows/daily-instagram-post.yml`. The time is in UTC: 13:00 UTC = 18:30 IST. |
| Use AI posts from day one | Replace the contents of `data/queue.json` with `[]`. Claude then writes the first batch on the next run. |
| Change the AI model | Add a variable `CLAUDE_MODEL`, for example `claude-opus-5-5`. |

If a run fails, GitHub emails you. Nothing is lost: an unpublished post goes back to the front of the queue the next day.

## Files

- `autopilot/generate.py` — Claude writes and reviews the posts. **The approved facts list lives here.** Add a fact only after checking it on an official regulator website.
- `autopilot/render.py` — the image design and the rule checker.
- `autopilot/instagram.py` — publishing to Instagram and token renewal.
- `autopilot/main.py` — the daily routine.
- `data/` — queue, posted log, examples the AI copies the style from.
- `media/` — the images of every post, by date.

## Good to know

- **Organic posts are fine.** Meta restricts paid ads about forex/CFDs, but these are not ads. Don't boost them as ads without checking Meta's financial services ad rules first.
- **Glance at your Instagram once a week.** The checks are strict, but finance is a trust business, so it's worth a quick look.
- **Instagram limits API accounts to 100 posts per 24 hours.** One a day is nowhere near that limit.

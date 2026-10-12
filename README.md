# TrustBrokerHub Instagram Autopilot

Makes one Instagram post a day, asks you on your phone, and posts it only after you approve it.

```
09:00  Gemini / ChatGPT writes the post (headline, supporting line, footer, picture idea)
       -> rule check + a second AI checks facts and compliance (rewritten until it passes)
       -> (optional) AI paints a background picture
       -> Canva fills your brand template and exports the image
       -> the second AI looks at the finished image (typos, cut-off text, odd picture)
       -> Telegram sends it to your phone with  [Approve]  [Reject]
You    -> Approve   (or Reject, then type what to change; a new one arrives in a few minutes)
18:30  -> the AI writes the caption + hashtags, the second AI checks them
       -> it is posted to Instagram, and Telegram confirms with the caption
```

The 9 ready-made Canva posts in `assets/ready/` go out first (with the captions you already approved), one a day. After that every post is written by AI. Nothing is ever posted without your approval.

---

## Setup checklist (one time, about 1 hour)

Everything goes into **GitHub → this repo → Settings → Secrets and variables → Actions**.
"Secret" = the **Secrets** tab, "variable" = the **Variables** tab. Never paste keys into a chat.

### ☐ 1. GitHub permissions (2 min)
- Settings → Actions → General → Workflow permissions → **Read and write permissions** → Save.
- The repo must stay **Public** (Instagram downloads the images from it; keys stay hidden in Secrets).

### ☐ 2. (No GitHub token needed)
The Canva login and Telegram chat are saved inside the repo by the Setup workflow (the Canva login encrypted in `data/vault.json`). You can delete any old `GH_PAT` secret.

### ☐ 3. AI keys (5 min)
A ChatGPT Plus or Gemini app subscription is **not** an API key. You need keys from the developer sites:
- **Gemini (free tier is enough for text):** aistudio.google.com → **Get API key** → Create. Secret **`GEMINI_API_KEY`**.
- **ChatGPT (optional, pay as you go):** platform.openai.com → API keys → Create. Add a little credit under Billing. Secret **`OPENAI_API_KEY`**.

With both keys, Gemini writes and ChatGPT checks (two different AIs = a better check). With one key, it does both jobs.

### ☐ 4. Telegram approvals (5 min)
1. In Telegram, open **@BotFather** → send `/newbot` → pick a name, e.g. *TBH Autopilot*, and a username ending in `bot`.
2. Copy the token it gives you → secret **`TELEGRAM_BOT_TOKEN`**.
3. Open your new bot in Telegram and press **Start**.
4. GitHub → **Actions → Setup → Run workflow** → step **telegram** → Run. Your bot says "Connected".

### ☐ 5. Canva (15 min)
The brand template is already made in your Canva: **TBH autopilot template — single post** (`EAHXu-PJ0RI`), with the fields HEADLINE, SUBLINE, CTA and HERO.
1. Go to **canva.com/developers** → Your integrations → **Create an integration**. Name it *TBH Autopilot*.
2. **Scopes:** tick read + write for *asset*, *design:content*; read for *design:meta*, *brandtemplate:meta*, *brandtemplate:content*.
3. **Authentication → Redirect URLs:** add `http://127.0.0.1:3001/callback`
4. **Client secret → Generate.** Save the Client ID as secret **`CANVA_CLIENT_ID`** and the secret as **`CANVA_CLIENT_SECRET`**.
5. Actions → Setup → Run workflow → step **canva-link**. Open the run's log, click the long link, log in to Canva, click **Allow**.
6. Your browser shows an error page at `127.0.0.1`. That's expected. Copy the **whole address** from the address bar.
7. Within a few minutes: Actions → Setup → step **canva-code**, paste the address into **value** → Run. Log says "Canva connected".

### ☐ 6. Instagram (if not done yet, 30 min)
1. Instagram app → Profile → ☰ → Account type and tools → **Switch to professional account → Business**.
2. developers.facebook.com → My Apps → **Create app** → use case "Manage messaging & content on Instagram".
3. Instagram → **API setup with Instagram login** → Generate access tokens → Add account → log in → allow.
4. Save the account ID as **`IG_USER_ID`** and the token as **`IG_ACCESS_TOKEN`**. The *Refresh Instagram token* workflow renews it every Monday and keeps the new one (encrypted) in `data/vault.json`.

### ☐ 7. Background pictures (optional, recommended)
Without this, every AI post uses the template's desk-and-lamp photo. To get a fresh picture each day, add the variable **`HERO_IMAGES`** = `gemini` (or `openai`).
Image generation is paid on both (a few US cents per picture; check Google's or OpenAI's pricing page), so your key needs billing turned on.

### ☐ 8. Test, then go live
1. Actions → Setup → step **test**. Every line should say OK and your phone gets a test message.
2. Actions → **Autopilot** → Run workflow → action **create**. Your phone gets today's post.
3. Tap **Approve**. Actions → Autopilot → Run workflow → action **check**, tick **Dry run**. Telegram shows the caption it would post.
4. Done. From now on it runs by itself every day.

---

## Day to day (from your phone)

| You want to… | Do this in Telegram |
|---|---|
| Post it | Tap **Approve**. It goes out at 18:30 India time, or straight away if that has passed. |
| Get a different one | Tap **Reject**, then type what to change (or `skip`). A new post arrives within about 20 minutes. |
| See what's waiting | Send `/status` |
| Get an extra post now | Send `/new` |

Other controls (GitHub → Settings → Secrets and variables → Actions → **Variables**):

| Variable | What it does | Default |
|---|---|---|
| `PAUSED` | `true` stops everything | not set |
| `POST_AT` | posting time, India time | `18:30` |
| `WRITER` / `CHECKER` | `gemini` or `openai` | Gemini writes, ChatGPT checks |
| `HERO_IMAGES` | `gemini`, `openai` or `off` | `off` |
| `GEMINI_MODEL` / `OPENAI_MODEL` | model names | newest Gemini Flash / `gpt-4.1-mini` |
| `CANVA_TEMPLATE_ID` | a different Canva brand template | `EAHXu-PJ0RI` |

Approval checks run every 15 minutes between 08:30 and 00:30 India time. GitHub sometimes starts them a few minutes late.

## For the admin panel

- Live state: `https://raw.githubusercontent.com/akashbx2patil-source/tbh-instagram-autopilot/main/data/status.json`
  (the post waiting for approval with its image links, plus the last 20 posts with captions).
- Approve or reject from a server (not the browser), with a GitHub token that has **Actions: Read and write** on this repo:
  `POST https://api.github.com/repos/akashbx2patil-source/tbh-instagram-autopilot/actions/workflows/autopilot.yml/dispatches`
  body `{"ref":"main","inputs":{"action":"approve","post_id":"ai0003"}}` (or `"reject"` with `"reason"`).

## Files

- `autopilot/content.py`: the writing rules, **the approved facts list** (add a fact only after checking it on the regulator's own website) and every check.
- `autopilot/llm.py`: Gemini / ChatGPT. `autopilot/canva.py`: Canva. `autopilot/telegram.py`: phone approvals. `autopilot/instagram.py`: posting.
- `data/ready.json`: ready-made posts still to go out, in order. `data/pending.json`: the post waiting for you.
- `data/posted.json`: everything posted. `data/rejected.json`: what you rejected and why (the AI learns from it).
- `media/`: every AI-made image, by date.

Organic posts are fine. Meta restricts paid ads about forex and CFDs, so don't boost these as ads without checking Meta's financial services ad rules first.

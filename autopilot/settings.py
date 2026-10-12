"""Settings the admin panel controls (data/settings.json). The autopilot only READS this file;
only the admin panel writes it, so the two never overwrite each other.
Anything left blank falls back to the GitHub variable / built-in default."""
import json, os
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "settings.json"
DEFAULTS = {
    "paused": False,             # true = make nothing new and publish nothing
    "post_at": "18:30",          # India time
    "writer": "",                # "", "gemini" or "openai"   ("" = automatic)
    "checker": "",               # "", "gemini" or "openai"
    "gemini_model": "",          # "" = newest Flash model
    "hero_images": "off",        # "off", "gemini" or "openai": AI background picture per post
    "canva_template_id": "",     # "" = EAHXu-PJ0RI
    "max_posts_per_day": 4,      # safety cap on new posts made per day (incl. replacements)
    "themes": ["scam", "basics", "reg", "verify"],
    "extra_instructions": "",    # free text added to every writing prompt
    "always_hashtags": [],       # hashtags added to every caption
}
ENV = {"post_at": "POST_AT", "writer": "WRITER", "checker": "CHECKER", "gemini_model": "GEMINI_MODEL",
       "hero_images": "HERO_IMAGES", "canva_template_id": "CANVA_TEMPLATE_ID",
       "max_posts_per_day": "MAX_POSTS_CREATED_PER_DAY"}


def load():
    s = dict(DEFAULTS)
    if PATH.exists():
        try:
            s.update({k: v for k, v in json.loads(PATH.read_text(encoding="utf-8")).items() if k in DEFAULTS})
        except ValueError:
            print("::warning::data/settings.json is not valid JSON; using defaults")
    return s


def apply():
    """Copy non-empty settings into the environment so every module sees them."""
    s = load()
    for key, env in ENV.items():
        v = s.get(key)
        if v not in (None, "", []):
            os.environ[env] = str(v)
    return s

"""Save a value as a GitHub Actions secret of this repository (needs the GH_PAT secret)."""
import os
from base64 import b64encode
import requests


def save_secret(name, value):
    pat, repo = os.environ.get("GH_PAT"), os.environ.get("GITHUB_REPOSITORY")
    if not pat or not repo:
        raise RuntimeError(f"GH_PAT is not set, so the {name} secret could not be saved")
    from nacl import encoding, public
    h = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
    r = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=h, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Could not read the repo's secret key ({r.status_code}). Does GH_PAT have Secrets: read and write?")
    key = r.json()
    box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    enc = b64encode(box.encrypt(value.encode())).decode()
    r = requests.put(f"https://api.github.com/repos/{repo}/actions/secrets/{name}", headers=h,
                     json={"encrypted_value": enc, "key_id": key["key_id"]}, timeout=30)
    if r.status_code not in (201, 204):
        raise RuntimeError(f"Could not save the {name} secret: {r.status_code} {r.text}")

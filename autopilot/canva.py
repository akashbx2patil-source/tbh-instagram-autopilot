"""Canva Connect API: fill the brand template, export it as a JPG.

Docs: https://www.canva.dev/docs/connect/
Env: CANVA_CLIENT_ID, CANVA_CLIENT_SECRET, CANVA_REFRESH_TOKEN, CANVA_TEMPLATE_ID
Canva refresh tokens work only once, so every run stores the new one, encrypted, in
data/vault.json (committed by the workflow) before doing anything else.
"""
import base64, hashlib, json, os, time, urllib.parse
import requests
from . import vault

API = "https://api.canva.com/rest/v1"
SCOPES = "asset:read asset:write brandtemplate:meta:read brandtemplate:content:read design:meta:read design:content:read design:content:write"
REDIRECT = os.environ.get("CANVA_REDIRECT_URI", "http://127.0.0.1:3001/callback")


class CanvaError(RuntimeError):
    pass


def _env(name):
    """Read a secret without stray spaces or line breaks (easy to paste by accident)."""
    v = (os.environ.get(name) or "").strip().strip('"').strip("'")
    if not v:
        raise CanvaError(f"The {name} secret is missing")
    return v


def _basic():
    raw = f"{_env('CANVA_CLIENT_ID')}:{_env('CANVA_CLIENT_SECRET')}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def _verifier():
    # Derived from the client secret, so the setup link can be printed without storing anything.
    return hashlib.sha256(("tbh-pkce:" + _env("CANVA_CLIENT_SECRET")).encode()).hexdigest()


def auth_link():
    challenge = base64.urlsafe_b64encode(hashlib.sha256(_verifier().encode()).digest()).decode().rstrip("=")
    q = {"code_challenge": challenge, "code_challenge_method": "S256", "scope": SCOPES, "response_type": "code",
         "client_id": _env("CANVA_CLIENT_ID"), "redirect_uri": REDIRECT, "state": "tbh"}
    return "https://www.canva.com/api/oauth/authorize?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def connect(code):
    """Swap the one-time code from the browser for a refresh token and save it as a secret."""
    code = code.strip()
    if "code=" in code:  # the whole address-bar URL was pasted
        code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query)["code"][0]
    r = requests.post(f"{API}/oauth/token", headers=_basic(), timeout=60, data={
        "grant_type": "authorization_code", "code": code, "code_verifier": _verifier(), "redirect_uri": REDIRECT})
    if r.status_code != 200:
        raise CanvaError(f"Canva did not accept the code ({r.status_code}): {r.text[:300]}. Codes expire after a few minutes; open the link again.")
    vault.put("canva_refresh_token", r.json()["refresh_token"])


_token = None


def token():
    global _token
    if _token:
        return _token
    r = requests.post(f"{API}/oauth/token", headers=_basic(), timeout=60, data={
        "grant_type": "refresh_token", "refresh_token": vault.get("canva_refresh_token") or _env("CANVA_REFRESH_TOKEN")})
    if r.status_code != 200:
        raise CanvaError(f"Canva login failed ({r.status_code}): {r.text[:300]}. Run Setup -> canva-link and canva-code again.")
    body = r.json()
    vault.put("canva_refresh_token", body["refresh_token"])  # the old one is now dead
    _token = body["access_token"]
    return _token


def _req(method, path, **kw):
    h = kw.pop("headers", {})
    h["Authorization"] = f"Bearer {token()}"
    for i in range(4):
        r = requests.request(method, f"{API}{path}", headers=h, timeout=120, **kw)
        if r.status_code == 429 and i < 3:
            time.sleep(20 * (i + 1))
            continue
        if r.status_code >= 400:
            raise CanvaError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()


def _wait(path, key="job", tries=40):
    for _ in range(tries):
        job = _req("GET", path)[key]
        if job["status"] == "success":
            return job
        if job["status"] == "failed":
            raise CanvaError(f"Canva job failed: {json.dumps(job.get('error', job))}")
        time.sleep(3)
    raise CanvaError(f"Canva job {path} took too long")


def upload_image(data, name):
    meta = {"name_base64": base64.b64encode(name.encode()).decode()}
    job = _req("POST", "/asset-uploads", data=data, headers={
        "Content-Type": "application/octet-stream", "Asset-Upload-Metadata": json.dumps(meta)})["job"]
    return _wait(f"/asset-uploads/{job['id']}")["asset"]["id"]


def fill_template(fields, title, hero_asset_id=None):
    data = {k: {"type": "text", "text": v} for k, v in fields.items()}
    if hero_asset_id:
        data["HERO"] = {"type": "image", "asset_id": hero_asset_id}
    job = _req("POST", "/autofills", json={"brand_template_id": _env("CANVA_TEMPLATE_ID"), "title": title, "data": data})["job"]
    design = _wait(f"/autofills/{job['id']}")["result"]["design"]
    return design["id"], (design.get("urls") or {}).get("edit_url") or design.get("url", "")


def export_jpg(design_id):
    """Instagram's API only accepts JPEG, so posts are exported as high-quality JPG."""
    job = _req("POST", "/exports", json={"design_id": design_id,
                                          "format": {"type": "jpg", "quality": 95, "export_quality": "pro"}})["job"]
    url = _wait(f"/exports/{job['id']}")["urls"][0]
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content

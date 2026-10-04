"""Publish to Instagram with the official Instagram API (Instagram Login).

Docs: https://developers.facebook.com/docs/instagram-platform/content-publishing
Needs: IG_USER_ID, IG_ACCESS_TOKEN (long-lived Instagram User token with
instagram_business_basic + instagram_business_content_publish).
"""
import os, time, requests

HOST = os.environ.get("IG_API_HOST", "https://graph.instagram.com")
VERSION = os.environ.get("IG_API_VERSION", "v25.0")


class InstagramError(RuntimeError):
    pass


def _call(method, path, **params):
    url = f"{HOST}/{VERSION}/{path}"
    params["access_token"] = os.environ["IG_ACCESS_TOKEN"]
    r = requests.request(method, url, data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=60)
    body = r.json() if r.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {"raw": r.text}
    if r.status_code >= 400 or "error" in body:
        raise InstagramError(f"{method} {path} failed ({r.status_code}): {body.get('error', body)}")
    return body


def _container(**params):
    user = os.environ["IG_USER_ID"]
    try:
        return _call("POST", f"{user}/media", **params)["id"]
    except InstagramError as e:
        if "alt_text" in params and "alt_text" in str(e).lower():
            params.pop("alt_text")  # fall back if alt text is rejected
            return _call("POST", f"{user}/media", **params)["id"]
        raise


def _wait_ready(container_id, tries=10):
    for _ in range(tries):
        status = _call("GET", container_id, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise InstagramError(f"container {container_id} status {status}")
        time.sleep(30)
    raise InstagramError(f"container {container_id} not ready in time")


def publish(image_urls, caption, alt_text=""):
    """Publish a single image or a carousel (2-10 images). Returns the Instagram media id."""
    user = os.environ["IG_USER_ID"]
    if not 1 <= len(image_urls) <= 10:
        raise InstagramError("Instagram allows 1-10 images per post")
    if len(image_urls) == 1:
        cid = _container(image_url=image_urls[0], caption=caption, alt_text=alt_text[:1000])
    else:
        children = []
        for i, u in enumerate(image_urls):
            params = dict(image_url=u, is_carousel_item="true")
            if i == 0 and alt_text:
                params["alt_text"] = alt_text[:1000]
            children.append(_container(**params))
        for c in children:
            _wait_ready(c)
        cid = _container(media_type="CAROUSEL", children=",".join(children), caption=caption)
    _wait_ready(cid)
    return _call("POST", f"{user}/media_publish", creation_id=cid)["id"]


def refresh_token():
    """Swap the current long-lived token for a fresh 60-day one. Returns (token, expires_in_seconds)."""
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": os.environ["IG_ACCESS_TOKEN"]}, timeout=60)
    body = r.json()
    if r.status_code >= 400 or "access_token" not in body:
        raise InstagramError(f"token refresh failed: {body}")
    return body["access_token"], body.get("expires_in")

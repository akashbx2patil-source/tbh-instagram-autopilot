"""One small wrapper around Gemini and ChatGPT (OpenAI), so either can write or check posts.

Env:
  GEMINI_API_KEY  / GEMINI_MODEL  (default gemini-2.5-flash)
  OPENAI_API_KEY  / OPENAI_MODEL  (default gpt-4.1-mini)
  WRITER  = gemini | openai   which AI writes posts and captions
  CHECKER = gemini | openai   which AI checks them (a different AI from the writer is best)
  HERO_IMAGES = gemini | openai | off   which AI paints the background picture
"""
import base64, json, os, re, time
import requests

TIMEOUT = 180


class LLMError(RuntimeError):
    pass


def _have(p):
    return bool(os.environ.get("GEMINI_API_KEY" if p == "gemini" else "OPENAI_API_KEY"))


def provider(role):
    """Pick the AI for a role ('writer' or 'checker')."""
    want = (os.environ.get(role.upper()) or "").strip().lower()
    if want in ("gemini", "openai"):
        if not _have(want):
            raise LLMError(f"{role.upper()} is set to {want} but its API key secret is missing")
        return want
    have = [p for p in ("gemini", "openai") if _have(p)]
    if not have:
        raise LLMError("Add a GEMINI_API_KEY or an OPENAI_API_KEY secret")
    if role == "checker" and len(have) == 2:
        return "openai"  # writer defaults to Gemini, so the checker is a second opinion
    return have[0]


def _post(url, headers, body, tries=4):
    for i in range(tries):
        r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
        if r.status_code in (429, 500, 502, 503, 504) and i < tries - 1:
            time.sleep(15 * (i + 1))
            continue
        if r.status_code >= 400:
            raise LLMError(f"{url.split('?')[0]} -> {r.status_code}: {r.text[:500]}")
        return r.json()


def _parse_json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise LLMError(f"AI reply was not JSON: {text[:300]}")


def ask_json(role, system, user, image_png=None, temperature=0.7, mime="image/jpeg"):
    """Ask the AI for a JSON object. image_png (image bytes, JPEG by default) is optional, for checking a finished post."""
    p = provider(role)
    if p == "gemini":
        model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
        parts = [{"text": user}]
        if image_png:
            parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(image_png).decode()}})
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": temperature}}
        out = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    {"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"}, body)
        try:
            text = "".join(pt.get("text", "") for pt in out["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError):
            raise LLMError(f"Gemini returned no text: {json.dumps(out)[:500]}")
    else:
        model = os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini"
        content = [{"type": "text", "text": user}]
        if image_png:
            content.append({"type": "image_url", "image_url": {
                "url": f"data:{mime};base64," + base64.b64encode(image_png).decode()}})
        body = {"model": model, "temperature": temperature, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        out = _post("https://api.openai.com/v1/chat/completions",
                    {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"}, body)
        text = out["choices"][0]["message"]["content"] or ""
    return _parse_json(text)


def hero_image(prompt):
    """Paint a background picture. Returns image bytes, or None when switched off or it fails."""
    p = (os.environ.get("HERO_IMAGES") or "off").strip().lower()
    if p not in ("gemini", "openai") or not _have(p):
        return None
    try:
        if p == "gemini":
            model = os.environ.get("GEMINI_IMAGE_MODEL") or "gemini-2.5-flash-image"
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "4:5"}}}
            out = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        {"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"}, body)
            for pt in out["candidates"][0]["content"]["parts"]:
                data = pt.get("inlineData") or pt.get("inline_data")
                if data:
                    return base64.b64decode(data["data"])
            return None
        model = os.environ.get("OPENAI_IMAGE_MODEL") or "gpt-image-1"
        out = _post("https://api.openai.com/v1/images/generations",
                    {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
                    {"model": model, "prompt": prompt, "size": "1024x1536", "n": 1})
        return base64.b64decode(out["data"][0]["b64_json"])
    except Exception as e:  # a missing picture should never stop the post
        print(f"::warning::Background picture failed, using the template picture instead: {e}")
        return None

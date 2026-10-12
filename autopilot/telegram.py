"""Phone approvals through a Telegram bot (free).

Env: TELEGRAM_BOT_TOKEN (from @BotFather). Your chat id comes from the TELEGRAM_CHAT_ID secret if set,
otherwise from data/state.json (saved by Setup -> telegram). Only that chat is ever listened to.
"""
import json, os
import requests


class TelegramError(RuntimeError):
    pass


def _saved_chat():
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "state.json"
    return str(json.loads(p.read_text()).get("telegram_chat_id") or "") if p.exists() else ""


def ready():
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and _chat())


def _call(method, files=None, **params):
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN'].strip()}/{method}"
    for k, v in list(params.items()):
        if isinstance(v, (dict, list)):
            params[k] = json.dumps(v)
    r = requests.post(url, data=params, files=files, timeout=90)
    body = r.json()
    if not body.get("ok"):
        raise TelegramError(f"{method}: {body.get('description', body)}")
    return body["result"]


def _chat():
    return (os.environ.get("TELEGRAM_CHAT_ID") or "").strip() or _saved_chat()


def buttons(post_id):
    return {"inline_keyboard": [[{"text": "Approve", "callback_data": f"approve:{post_id}"},
                                 {"text": "Reject", "callback_data": f"reject:{post_id}"}]]}


def send_for_approval(image_bytes, text, post_id, filename="post.png"):
    """Send the post with Approve / Reject buttons. Returns the message id."""
    msg = _call("sendPhoto", files={"photo": (filename, image_bytes)}, chat_id=_chat(),
                caption=text[:1024], reply_markup=buttons(post_id))
    return msg["message_id"]


def send_album(images, text, post_id):
    """Carousel: send the slides as an album, then a message with the buttons."""
    media, files = [], {}
    for i, (name, data) in enumerate(images[:10]):
        files[f"f{i}"] = (name, data)
        media.append({"type": "photo", "media": f"attach://f{i}"})
    _call("sendMediaGroup", files=files, chat_id=_chat(), media=media)
    return _call("sendMessage", chat_id=_chat(), text=text[:4000], reply_markup=buttons(post_id))["message_id"]


def say(text):
    if ready():
        try:
            _call("sendMessage", chat_id=_chat(), text=text[:4000], disable_web_page_preview="true")
        except Exception as e:
            print(f"::warning::Telegram message failed: {e}")


def close_buttons(message_id, note):
    try:
        _call("editMessageReplyMarkup", chat_id=_chat(), message_id=message_id, reply_markup={"inline_keyboard": []})
    except Exception:
        pass
    say(note)


def updates(offset):
    """New decisions since `offset`. Returns (list of events, next offset).
    Events: {"kind": "approve"|"reject", "post_id": ...} or {"kind": "text", "text": ...}."""
    res = _call("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"])
    events, nxt = [], offset
    for u in res:
        nxt = max(nxt, u["update_id"] + 1)
        if "callback_query" in u:
            cq = u["callback_query"]
            if str(cq.get("message", {}).get("chat", {}).get("id")) != str(_chat()):
                continue
            try:
                _call("answerCallbackQuery", callback_query_id=cq["id"], text="Got it")
            except Exception:
                pass  # too old to answer; the decision still counts
            kind, _, pid = cq.get("data", "").partition(":")
            if kind in ("approve", "reject"):
                events.append({"kind": kind, "post_id": pid})
        elif "message" in u:
            m = u["message"]
            if str(m.get("chat", {}).get("id")) == str(_chat()) and m.get("text"):
                events.append({"kind": "text", "text": m["text"].strip()})
    return events, nxt


def find_chat_id():
    """For setup: the chat id of whoever last messaged the bot."""
    res = _call("getUpdates", timeout=0)
    for u in reversed(res):
        m = u.get("message") or {}
        if m.get("chat", {}).get("id"):
            return m["chat"]["id"], (m.get("from") or {}).get("first_name", "")
    return None, None

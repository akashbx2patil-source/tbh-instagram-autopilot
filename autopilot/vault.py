"""Small encrypted store inside the repo (data/vault.json) for logins that change by themselves:
the Canva refresh token (new one every run) and the renewed Instagram token.
Encrypted with a key made from the CANVA_CLIENT_SECRET secret, so the public repo shows only gibberish.
The workflow commits data/ after every run, which is how the new values are kept."""
import base64, hashlib, json, os
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "vault.json"


def _box():
    from nacl.secret import SecretBox
    material = (os.environ.get("VAULT_KEY") or os.environ.get("CANVA_CLIENT_SECRET") or "").strip()
    if not material:
        raise RuntimeError("CANVA_CLIENT_SECRET is needed to read or write data/vault.json")
    return SecretBox(hashlib.sha256(("tbh-vault:" + material).encode()).digest())


def get(name):
    data = json.loads(PATH.read_text()) if PATH.exists() else {}
    if name not in data:
        return None
    try:
        return _box().decrypt(base64.b64decode(data[name])).decode()
    except Exception:
        return None  # key changed (e.g. a new Canva client secret): treat as not set


def put(name, value):
    data = json.loads(PATH.read_text()) if PATH.exists() else {}
    data[name] = base64.b64encode(bytes(_box().encrypt(value.encode()))).decode()
    PATH.write_text(json.dumps(data, indent=1) + "\n")

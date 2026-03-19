#!/usr/bin/env python3
"""
REEcord — System Tray Launcher

Double-click → opens chat in browser.
No terminal window — runs silently in the background.

File is .pyw so Windows does not open a console window.
"""

import os
import sys
import json
import time
import threading
import subprocess
import webbrowser
import urllib.request
from pathlib import Path
from io import BytesIO

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

# ── Package check ─────────────────────────────────────────────────────────────
def _ensure(pkg):
    try:
        __import__(pkg)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"],
                              creationflags=0x08000000)  # CREATE_NO_WINDOW

_ensure("flask")
_ensure("pystray")
_ensure("PIL")

import pystray
from PIL import Image, ImageDraw
from flask import Flask, redirect, send_from_directory, request, jsonify

LAUNCHER_PORT = 5000
WEB_UI_DIR    = PROJECT_DIR / "web_ui"
ENV_FILE      = PROJECT_DIR / ".env"

_proxy_proc = None
_proxy_lock = threading.Lock()
_tray_icon  = None

# ── .env ──────────────────────────────────────────────────────────────────────
def load_env():
    if not ENV_FILE.exists():
        return {}
    result = {}
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def save_env(data):
    existing = load_env()
    existing.update(data)
    lines = [
        "# REEcord Configuration",
        f"GENSYN_PRIVATE_KEY={existing.get('GENSYN_PRIVATE_KEY', '')}",
        f"GENSYN_WALLET_ADDRESS={existing.get('GENSYN_WALLET_ADDRESS', '')}",
        f"PINATA_API_KEY={existing.get('PINATA_API_KEY', '')}",
        "GENSYN_RPC_URL=https://gensyn-testnet.g.alchemy.com/public",
        "REE_MODEL=Qwen/Qwen2-0.5B",
        "REE_MAX_TOKENS=300",
    ]
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def is_configured():
    pk = load_env().get("GENSYN_PRIVATE_KEY", "")
    return bool(pk and pk != "0x" and len(pk) > 10)

# ── System checks ─────────────────────────────────────────────────────────────
def check_docker():
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=5,
                           creationflags=0x08000000)
        return r.returncode == 0
    except Exception:
        return False


def check_ollama():
    for port in [11436, 11434]:
        try:
            url = f"http://localhost:{port}/api/tags"
            with urllib.request.urlopen(url, timeout=3) as r:
                data = json.loads(r.read())
                return {"running": True, "port": port,
                        "models": [m["name"] for m in data.get("models", [])]}
        except Exception:
            continue
    return {"running": False, "port": None, "models": []}

# ── Proxy ─────────────────────────────────────────────────────────────────────
def proxy_alive():
    try:
        urllib.request.urlopen("http://localhost:11434/reerecord/status", timeout=1)
        return True
    except Exception:
        return False


def start_proxy(ollama_port=11436):
    global _proxy_proc
    with _proxy_lock:
        if proxy_alive():
            return
        env = load_env()
        environ = os.environ.copy()
        environ.update(env)
        environ["REE_OLLAMA_URL"] = f"http://localhost:{ollama_port}"
        environ["REE_PROXY_PORT"] = "11434"

        # Use python.exe instead of pythonw.exe — proxy needs console I/O and Docker
        python_exe = sys.executable
        if python_exe.lower().endswith("pythonw.exe"):
            candidate = python_exe[:-len("pythonw.exe")] + "python.exe"
            if Path(candidate).exists():
                python_exe = candidate

        # Explicitly add Docker and Git paths
        extra_paths = [
            r"C:\Program Files\Docker\Docker\resources\bin",
            r"C:\ProgramData\DockerDesktop\version-bin",
            r"C:\Program Files\Git\bin",
            r"C:\Program Files\Git\usr\bin",
            r"C:\Windows\System32",
        ]
        current_path = environ.get("PATH", "")
        for p in extra_paths:
            if p not in current_path and Path(p).exists():
                current_path = p + os.pathsep + current_path
        environ["PATH"] = current_path

        # Write proxy output to log file for debugging
        log_path = PROJECT_DIR / "proxy.log"
        log_file = open(log_path, "w", encoding="utf-8", buffering=1)

        _proxy_proc = subprocess.Popen(
            [python_exe, str(PROJECT_DIR / "ollama_proxy.py")],
            env=environ,
            cwd=str(PROJECT_DIR),
            stdout=log_file,
            stderr=log_file,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )

        # Wait until ready (max 10s)
        for _ in range(20):
            time.sleep(0.5)
            if proxy_alive():
                break

# ── Flask ─────────────────────────────────────────────────────────────────────
flask_app = Flask(__name__)


@flask_app.route("/")
def root():
    return redirect("/chat" if is_configured() else "/setup")


@flask_app.route("/setup")
def setup():
    return send_from_directory(str(WEB_UI_DIR), "setup.html")


@flask_app.route("/chat")
def chat():
    if not is_configured():
        return redirect("/setup")
    return send_from_directory(str(WEB_UI_DIR), "chat.html")


@flask_app.route("/<path:f>")
def static_f(f):
    return send_from_directory(str(WEB_UI_DIR), f)


@flask_app.route("/api/status")
def api_status():
    ollama = check_ollama()
    return jsonify({
        "configured":    is_configured(),
        "docker":        check_docker(),
        "ollama":        ollama["running"],
        "ollama_port":   ollama["port"],
        "ollama_models": ollama["models"],
        "proxy":         proxy_alive(),
    })


@flask_app.route("/api/save-config", methods=["POST"])
def api_save():
    d    = request.get_json(force=True) or {}
    pk   = d.get("private_key", "").strip()
    addr = d.get("wallet_address", "").strip()
    jwt  = d.get("pinata_jwt", "").strip()
    if not pk:
        return jsonify({"error": "Private key is required"}), 400
    if not pk.startswith("0x") and len(pk) == 64:
        pk = "0x" + pk
    save_env({"GENSYN_PRIVATE_KEY": pk, "GENSYN_WALLET_ADDRESS": addr, "PINATA_API_KEY": jwt})
    ollama = check_ollama()
    port   = ollama.get("port", 11436) or 11436
    threading.Thread(target=start_proxy, args=(port,), daemon=True).start()
    return jsonify({"ok": True, "redirect": "/chat"})


@flask_app.route("/api/reset-config", methods=["POST"])
def api_reset():
    global _proxy_proc
    if _proxy_proc and _proxy_proc.poll() is None:
        _proxy_proc.terminate()
    save_env({"GENSYN_PRIVATE_KEY": "0x", "GENSYN_WALLET_ADDRESS": "", "PINATA_API_KEY": ""})
    return jsonify({"ok": True, "redirect": "/setup"})

# ── Tray icon ─────────────────────────────────────────────────────────────────
def _make_icon():
    """Load REEcord logo as tray icon, fall back to a red circle."""
    logo_path = WEB_UI_DIR / "logo.png"
    if logo_path.exists():
        try:
            img = Image.open(logo_path).convert("RGBA").resize((64, 64))
            return img
        except Exception:
            pass

    # Fallback: red circle
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4,  4,  60, 60], fill=(224, 49, 49, 255))
    draw.ellipse([18, 18, 46, 46], fill=(8,   8,  8,  255))
    return img


def _open_chat(_=None):
    webbrowser.open(f"http://localhost:{LAUNCHER_PORT}/chat")


def _open_site(_=None):
    webbrowser.open(f"http://localhost:{LAUNCHER_PORT}")


def _quit(icon, _=None):
    global _proxy_proc
    icon.stop()
    if _proxy_proc and _proxy_proc.poll() is None:
        _proxy_proc.terminate()
    os._exit(0)


def _start_tray():
    global _tray_icon
    icon_img = _make_icon()
    menu = pystray.Menu(
        pystray.MenuItem("Open Chat",    _open_chat, default=True),
        pystray.MenuItem("Open Site",    _open_site),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit REEcord", _quit),
    )
    _tray_icon = pystray.Icon("REEcord", icon_img, "REEcord", menu)
    _tray_icon.run()

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Start proxy immediately if already configured
    if is_configured():
        ollama = check_ollama()
        port   = ollama.get("port", 11436) or 11436
        threading.Thread(target=start_proxy, args=(port,), daemon=True).start()

    # Start Flask in background thread
    flask_thread = threading.Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0", port=LAUNCHER_PORT,
            debug=False, use_reloader=False
        ),
        daemon=True,
    )
    flask_thread.start()

    # Open browser
    def _open_browser():
        time.sleep(2)
        webbrowser.open(f"http://localhost:{LAUNCHER_PORT}")
    threading.Thread(target=_open_browser, daemon=True).start()

    # Start tray (must run on main thread)
    _start_tray()

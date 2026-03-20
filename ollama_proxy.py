#!/usr/bin/env python3
"""
REEcord Ollama Proxy — Session-based chat recording

Each conversation is tracked as a session. When "Finalize & Record" is pressed,
the entire conversation is recorded as a single receipt → IPFS → Gensyn Testnet.

Endpoints:
  GET  /reerecord/models          → REE-compatible models installed in Ollama
  POST /reerecord/chat            → Send message, receive reply (added to session)
  GET  /reerecord/session/<id>    → Session history
  DELETE /reerecord/session/<id>  → Delete session
  POST /reerecord/finalize        → Record entire session on-chain
  GET  /reerecord/finalize/<id>   → Poll recording status
  GET  /reerecord/status          → Proxy status
  *    /*                         → Forward to Ollama (passthrough)
"""

import json
import os
import sys
import uuid

# Windows terminal encoding fix
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import threading
import time
import tempfile
from datetime import datetime
from pathlib import Path

# ── Load .env ────────────────────────────────────────────────────────────────
def _load_env():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if val and key not in os.environ:
                os.environ[key] = val

_load_env()

try:
    from flask import Flask, request, Response, jsonify
    import requests as _req
except ImportError:
    print("[Error] Missing packages: pip install flask requests")
    sys.exit(1)

# ── Settings ─────────────────────────────────────────────────────────────────
OLLAMA_URL  = os.getenv("REE_OLLAMA_URL", "http://localhost:11436")
PROXY_PORT  = int(os.getenv("REE_PROXY_PORT", "11435"))
PROJECT_DIR = Path(__file__).parent

# Ollama name → HuggingFace name (REE-compatible models)
SUPPORTED_MODELS = {
    "qwen2:0.5b":   "Qwen/Qwen2-0.5B",
    "qwen2:1.5b":   "Qwen/Qwen2-1.5B",
    "qwen2:7b":     "Qwen/Qwen2-7B",
    "qwen3:0.6b":   "Qwen/Qwen3-0.6B",
    "qwen3:1.7b":   "Qwen/Qwen3-1.7B",
    "qwen3:4b":     "Qwen/Qwen3-4B",
    "llama3:8b":    "meta-llama/Meta-Llama-3-8B-Instruct",
    "llama3.2:1b":  "meta-llama/Llama-3.2-1B-Instruct",
    "llama3.2:3b":  "meta-llama/Llama-3.2-3B-Instruct",
    "gemma2:2b":    "google/gemma-2-2b-it",
    "gemma2:9b":    "google/gemma-2-9b-it",
    "phi3:mini":    "microsoft/Phi-3-mini-4k-instruct",
    "mistral:7b":   "mistralai/Mistral-7B-Instruct-v0.3",
}

# ─── Session persistence ─────────────────────────────────────────────────────
SESSIONS_FILE = PROJECT_DIR / "sessions.json"
FINALIZE_JOBS = {}
_lock = threading.Lock()


def _load_sessions() -> dict:
    """Load sessions from disk."""
    if not SESSIONS_FILE.exists():
        return {}
    try:
        with open(SESSIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            print(f"[Sessions] {len(data)} session(s) loaded.")
            return data
    except Exception as e:
        print(f"[Sessions] Load error: {e}")
        return {}


def _save_sessions():
    """Save sessions to disk."""
    try:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(SESSIONS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Sessions] Save error: {e}")


SESSIONS = _load_sessions()

app = Flask(__name__)

# ─── CORS ────────────────────────────────────────────────────────────────────
@app.after_request
def _add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
    return resp

@app.route("/reerecord/<path:p>", methods=["OPTIONS"])
def _options(p):
    return Response("", 204)

# ─── /reerecord/models ───────────────────────────────────────────────────────
@app.route("/reerecord/models", methods=["GET"])
def get_models():
    """Return REE-compatible models installed in Ollama."""
    try:
        r = _req.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        installed = {m["name"] for m in r.json().get("models", [])}
    except Exception:
        installed = set()

    result = []
    for ollama_name, hf_name in SUPPORTED_MODELS.items():
        if ollama_name in installed:
            result.append({
                "ollama_name": ollama_name,
                "hf_name":     hf_name,
                "installed":   True,
            })

    return jsonify({"models": result})

# ─── /reerecord/chat ─────────────────────────────────────────────────────────
@app.route("/reerecord/chat", methods=["POST"])
def chat():
    """Add message to session, get reply from Ollama."""
    body       = request.get_json(force=True) or {}
    session_id = body.get("session_id") or str(uuid.uuid4())
    message    = body.get("message", "").strip()
    model      = body.get("model", "qwen2:0.5b")

    if not message:
        return jsonify({"error": "message is required"}), 400
    if model not in SUPPORTED_MODELS:
        return jsonify({"error": f"Model {model} is not supported by REE"}), 400

    hf_model = SUPPORTED_MODELS[model]

    # Create session or append to existing one
    with _lock:
        if session_id not in SESSIONS:
            SESSIONS[session_id] = {
                "model":      model,
                "hf_model":   hf_model,
                "messages":   [],
                "created_at": time.time(),
            }
        session = SESSIONS[session_id]
        session["messages"].append({
            "role":      "user",
            "content":   message,
            "timestamp": time.time(),
        })
        # Build message array for Ollama
        ollama_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in session["messages"]
        ]

    # Send to Ollama
    try:
        r = _req.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": model, "messages": ollama_messages, "stream": False},
            timeout=180,
        )
        resp_json = r.json()
        assistant_content = resp_json.get("message", {}).get("content", "")
    except _req.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to Ollama. Is Ollama running?"}), 503
    except Exception as exc:
        return jsonify({"error": f"Ollama error: {exc}"}), 503

    # Append assistant reply to session and save to disk
    with _lock:
        SESSIONS[session_id]["messages"].append({
            "role":      "assistant",
            "content":   assistant_content,
            "timestamp": time.time(),
        })
        msg_count = len(SESSIONS[session_id]["messages"])
        _save_sessions()

    print(f"[Chat] Session {session_id[:8]}… | {msg_count} messages | model: {model}")

    return jsonify({
        "session_id":    session_id,
        "response":      assistant_content,
        "model":         model,
        "message_count": msg_count,
    })

# ─── /reerecord/session ──────────────────────────────────────────────────────
@app.route("/reerecord/session/<sid>", methods=["GET"])
def get_session(sid):
    session = SESSIONS.get(sid)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(session)

@app.route("/reerecord/session/<sid>", methods=["DELETE"])
def delete_session(sid):
    with _lock:
        SESSIONS.pop(sid, None)
        _save_sessions()
    return jsonify({"ok": True})

# ─── /reerecord/sessions (list all) ─────────────────────────────────────────
@app.route("/reerecord/sessions", methods=["GET"])
def list_sessions():
    """List all sessions (for sidebar)."""
    result = []
    with _lock:
        for sid, session in SESSIONS.items():
            msgs    = session.get("messages", [])
            preview = next((m["content"][:80] for m in msgs if m["role"] == "user"), "")
            result.append({
                "session_id":    sid,
                "model":         session.get("model", ""),
                "message_count": len(msgs),
                "created_at":    session.get("created_at", 0),
                "finalized":     session.get("finalized", False),
                "tx_hash":       session.get("tx_hash", ""),
                "ipfs_cid":      session.get("ipfs_cid", ""),
                "preview":       preview,
            })
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify({"sessions": result})

# ─── /reerecord/finalize ─────────────────────────────────────────────────────
@app.route("/reerecord/finalize", methods=["POST"])
def finalize():
    """Record session on-chain (runs in background)."""
    body       = request.get_json(force=True) or {}
    session_id = body.get("session_id", "")

    session = SESSIONS.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    if not session["messages"]:
        return jsonify({"error": "No messages in session"}), 400

    job_id = str(uuid.uuid4())
    with _lock:
        FINALIZE_JOBS[job_id] = {
            "status":     "pending",
            "session_id": session_id,
            "started_at": time.time(),
            "result":     None,
            "error":      None,
        }

    # Start background thread
    t = threading.Thread(
        target=_run_finalize,
        args=(job_id, session_id, dict(session)),
        daemon=True
    )
    t.start()

    print(f"[Finalize] Job started: {job_id[:8]}… | session: {session_id[:8]}…")
    return jsonify({"job_id": job_id, "status": "pending"})


@app.route("/reerecord/finalize/<job_id>", methods=["GET"])
def finalize_status(job_id):
    """Poll job status."""
    job = FINALIZE_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


def _run_finalize(job_id, session_id, session):
    """Background: conversation → REE → IPFS → Chain."""
    sys.path.insert(0, str(PROJECT_DIR))

    def _upd(**kw):
        with _lock:
            FINALIZE_JOBS[job_id].update(kw)

    try:
        messages = session["messages"]

        # Build prompt from conversation
        conv_lines = []
        for m in messages:
            role = "User" if m["role"] == "user" else "Assistant"
            conv_lines.append(f"[{role}]: {m['content']}")
        prompt = "Recorded AI conversation:\n\n" + "\n".join(conv_lines)

        # ── Step 1: REE ───────────────────────────────────────────────────────
        _upd(status="running", step="ree", step_label="Running REE inference…")
        print(f"\n[Finalize] Step 1/3 — REE  model={session['hf_model']}", flush=True)

        import ree_runner
        _orig = ree_runner.MODEL_NAME
        ree_runner.MODEL_NAME = session["hf_model"]
        try:
            receipt_path = ree_runner.generate_receipt(prompt)
        finally:
            ree_runner.MODEL_NAME = _orig

        print(f"[Finalize] REE done → {Path(receipt_path).name}", flush=True)

        # ── Step 2: Enrich receipt ────────────────────────────────────────────
        _upd(step="receipt", step_label="Generating cryptographic receipt…")
        print("[Finalize] Step 2/3 — enriching receipt", flush=True)

        with open(receipt_path, encoding="utf-8") as f:
            ree_receipt = json.load(f)

        enriched = {
            **ree_receipt,
            "ollama_conversation": {
                "model":       session["model"],
                "hf_model":    session["hf_model"],
                "session_id":  session_id,
                "messages":    [{"role": m["role"], "content": m["content"]} for m in messages],
                "recorded_at": datetime.utcnow().isoformat() + "Z",
            },
            "reerecord_version": "2.0",
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(enriched, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name

        # ── Step 3: IPFS + Chain ──────────────────────────────────────────────
        _upd(step="chain", step_label="Encrypting & uploading to IPFS…")
        print("[Finalize] Step 3/3 — IPFS + chain", flush=True)

        from testnet_submitter import submit_receipt
        result = submit_receipt(tmp_path)

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        # ── Done ──────────────────────────────────────────────────────────────
        with _lock:
            FINALIZE_JOBS[job_id].update({
                "status":       "done",
                "step":         "done",
                "step_label":   "Complete!",
                "result":       result,
                "completed_at": time.time(),
            })
            if session_id in SESSIONS:
                SESSIONS[session_id]["finalized"] = True
                SESSIONS[session_id]["tx_hash"]   = result.get("tx_hash", "")
                SESSIONS[session_id]["ipfs_cid"]  = result.get("ipfs_cid", "")
            _save_sessions()

        tx = result.get("tx_hash", "")
        print(f"[Finalize] ✓ Done  status={result.get('status','?')}  TX={tx}", flush=True)
        if tx:
            print(f"[Finalize]   https://gensyn-testnet.explorer.alchemy.com/tx/{tx}", flush=True)

    except Exception as exc:
        import traceback
        traceback.print_exc()
        _upd(status="failed", step="failed", error=str(exc))
        print(f"[Finalize] ✗ Error: {exc}", flush=True)

# ─── /reerecord/status ───────────────────────────────────────────────────────
@app.route("/reerecord/status", methods=["GET"])
def proxy_status():
    return jsonify({
        "proxy":    "running",
        "sessions": len(SESSIONS),
        "jobs":     len(FINALIZE_JOBS),
        "ollama":   OLLAMA_URL,
        "port":     PROXY_PORT,
    })

# ─── Passthrough ─────────────────────────────────────────────────────────────
@app.route("/", defaults={"path": ""}, methods=["GET","POST","PUT","DELETE"])
@app.route("/<path:path>",             methods=["GET","POST","PUT","DELETE"])
def proxy(path):
    """Forward all other requests to Ollama."""
    target = f"{OLLAMA_URL}/{path}"
    try:
        resp = _req.request(
            method=request.method,
            url=target,
            headers={k: v for k, v in request.headers if k.lower() != "host"},
            data=request.get_data(),
            params=request.args,
            timeout=300,
            stream=True,
        )
        return Response(
            resp.iter_content(chunk_size=4096),
            status=resp.status_code,
            content_type=resp.headers.get("Content-Type", "application/json"),
        )
    except _req.exceptions.ConnectionError:
        return Response(
            json.dumps({"error": "Cannot connect to Ollama. Is http://localhost:11436 running?"}),
            status=503,
            content_type="application/json",
        )

# ─── Main ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    pk = os.getenv("GENSYN_PRIVATE_KEY", "")
    if not pk or pk == "0x":
        print("[Warning] GENSYN_PRIVATE_KEY not set — on-chain recording disabled.")

    print("=" * 60)
    print("  REEcord Ollama Proxy  (Session-based)")
    print("=" * 60)
    print(f"  Ollama   : {OLLAMA_URL}")
    print(f"  Proxy    : http://localhost:{PROXY_PORT}")
    print(f"  Chat UI  : http://localhost:8080")
    print(f"  Wallet   : {os.getenv('GENSYN_WALLET_ADDRESS', 'not set')}")
    print("=" * 60)
    print()
    print(f"  Supported models ({len(SUPPORTED_MODELS)}):")
    for k, v in SUPPORTED_MODELS.items():
        print(f"    {k:20} -> {v}")
    print()

    app.run(host="0.0.0.0", port=PROXY_PORT, debug=False)

#!/usr/bin/env python3
"""
REE Runner — drives gensynai/ree Docker container programmatically.

Output is streamed live to the server console (not buffered) so you can
watch Docker's progress in real-time in the proxy logs.
"""

import os
import sys
import glob
import shutil
import subprocess
from pathlib import Path


def _find_bash() -> str:
    if sys.platform == "win32":
        for path in [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ]:
            if Path(path).exists():
                return path
    found = shutil.which("bash")
    if found:
        return found
    raise FileNotFoundError("bash not found — install Git for Windows.")


# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_ROOT   = Path(__file__).resolve().parent
REE_SCRIPT     = PROJECT_ROOT / "run_ree.sh"
MODEL_NAME     = "Qwen/Qwen2-0.5B"
MAX_NEW_TOKENS = 300
TASKS_ROOT     = Path.home() / ".cache" / "gensyn"
REE_TIMEOUT    = 600   # 10-minute hard cap


def generate_receipt(prompt: str) -> str:
    """
    Run REE with *prompt*, return path to the generated receipt JSON.
    Docker output streams directly to console so progress is visible.
    Raises RuntimeError on failure or timeout.
    """
    TASKS_ROOT.mkdir(parents=True, exist_ok=True)

    cmd = [
        _find_bash(), str(REE_SCRIPT),
        "--model-name",    MODEL_NAME,
        "--prompt-text",   prompt,
        "--max-new-tokens", str(MAX_NEW_TOKENS),
    ]

    print(f"[REE] Starting — model={MODEL_NAME}  timeout={REE_TIMEOUT}s", flush=True)
    print(f"[REE] Docker output will appear below:", flush=True)

    # DO NOT use capture_output=True — stream Docker output directly to
    # the server console so we can see what REE is doing in real-time.
    kwargs = {"stdout": sys.stdout, "stderr": sys.stderr}
    if sys.platform == "win32":
        # CREATE_NO_WINDOW prevents a visible terminal window
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        # On Windows with CREATE_NO_WINDOW we need explicit pipe targets
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT

    try:
        if sys.platform == "win32":
            # On Windows: stream output line by line so it appears in real-time
            proc = subprocess.Popen(cmd, **kwargs, text=True)
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    print(f"[REE] {line}", flush=True)
            proc.wait(timeout=REE_TIMEOUT)
            returncode = proc.returncode
        else:
            proc = subprocess.run(cmd, timeout=REE_TIMEOUT, **kwargs)
            returncode = proc.returncode

    except subprocess.TimeoutExpired:
        try:
            proc.kill()  # proc is always defined before wait/run raises TimeoutExpired
        except (NameError, Exception):
            pass
        raise RuntimeError(
            f"REE timed out after {REE_TIMEOUT}s — Docker container may be stuck.\n"
            "Try: docker ps  to see if a container is stuck, then docker kill <id>"
        )

    print(f"[REE] Process finished (exit={returncode})", flush=True)

    if returncode != 0:
        # REE sometimes exits non-zero on Windows (Docker/bash signal quirk)
        # but still writes a valid receipt — check before giving up.
        try:
            receipt = _find_latest_receipt()
            print(f"[REE] Non-zero exit but receipt found — continuing.", flush=True)
            return receipt
        except FileNotFoundError:
            raise RuntimeError(f"REE failed (exit {returncode}) — check logs above.")

    return _find_latest_receipt()


def _find_latest_receipt() -> str:
    patterns = [
        str(TASKS_ROOT / "**" / "metadata" / "receipt_*.json"),
        str(Path.home() / ".cache" / "gensyn" / "**" / "metadata" / "receipt_*.json"),
    ]
    candidates = []
    for p in patterns:
        candidates.extend(glob.glob(p, recursive=True))
    if not candidates:
        raise FileNotFoundError(
            "REE ran but no receipt JSON was found. "
            "Check logs above for the actual receipt path."
        )
    return max(candidates, key=os.path.getmtime)

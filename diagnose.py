#!/usr/bin/env python3
"""
REEcord Diagnostic Tool
Tests every component of the finalize pipeline independently.

Usage:
    python diagnose.py
    python diagnose.py --send-tx   # actually send a dummy on-chain TX
"""

import json
import os
import sys
import glob
import socket
import hashlib
import time
import subprocess
import shutil
from pathlib import Path

# ── .env loader ───────────────────────────────────────────────────────────────
def _load_env():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        print("  [!] .env file not found next to diagnose.py")
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

import urllib.request
import urllib.error

# ── Config (same as testnet_submitter.py) ────────────────────────────────────
RPC_URL     = os.getenv("GENSYN_RPC_URL",     "https://gensyn-testnet.g.alchemy.com/public")
PRIVATE_KEY = os.getenv("GENSYN_PRIVATE_KEY", "")
PINATA_JWT  = os.getenv("PINATA_API_KEY",     "")
CHAIN_ID    = 685685
socket.setdefaulttimeout(15)

SEP  = "─" * 60
PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m⚠\033[0m"
INFO = "\033[36m·\033[0m"

def ok(msg):   print(f"  {PASS} {msg}")
def fail(msg): print(f"  {FAIL} {msg}")
def warn(msg): print(f"  {WARN} {msg}")
def info(msg): print(f"  {INFO} {msg}")


# ── 1. Environment check ──────────────────────────────────────────────────────
def check_env():
    print(f"\n{SEP}")
    print("  1. Environment")
    print(SEP)

    if PRIVATE_KEY and PRIVATE_KEY != "0x":
        ok(f"GENSYN_PRIVATE_KEY set ({PRIVATE_KEY[:6]}…)")
    else:
        fail("GENSYN_PRIVATE_KEY is NOT set — on-chain recording will fail")

    if PINATA_JWT:
        ok(f"PINATA_API_KEY set ({PINATA_JWT[:8]}…)")
    else:
        warn("PINATA_API_KEY not set — will use sha256 placeholder instead of real IPFS")

    rpc_display = RPC_URL if len(RPC_URL) < 60 else RPC_URL[:57] + "..."
    info(f"RPC_URL = {rpc_display}")

    # Check contract file
    contract_file = Path(__file__).parent / "contract_address.json"
    if contract_file.exists():
        try:
            cdata = json.loads(contract_file.read_text())
            addr = cdata.get("address", "?")
            ok(f"contract_address.json found — {addr}")
        except Exception as e:
            fail(f"contract_address.json parse error: {e}")
    else:
        warn("contract_address.json not found — will send self-tx instead of contract call")


# ── 2. Python imports ─────────────────────────────────────────────────────────
def check_imports():
    print(f"\n{SEP}")
    print("  2. Python dependencies")
    print(SEP)

    deps = [
        ("web3",            "web3"),
        ("flask",           "flask"),
        ("requests",        "requests"),
        ("eth_account",     "eth_account"),
        ("Crypto (pycryptodome)", "Crypto.Cipher"),
    ]

    all_ok = True
    for label, mod in deps:
        try:
            __import__(mod)
            ok(label)
        except ImportError:
            fail(f"{label}  →  pip install {mod.split('.')[0].lower()}")
            all_ok = False
    return all_ok


# ── 3. Docker / REE check ─────────────────────────────────────────────────────
def check_docker():
    print(f"\n{SEP}")
    print("  3. Docker & REE")
    print(SEP)

    # Docker daemon
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        if r.returncode == 0:
            ok("Docker daemon is running")
        else:
            fail("Docker daemon not responding — start Docker Desktop")
            return False
    except FileNotFoundError:
        fail("docker command not found")
        return False
    except subprocess.TimeoutExpired:
        fail("docker info timed out — is Docker starting?")
        return False

    # REE image
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", "gensynai/ree:v0.1.0"],
            capture_output=True, timeout=10
        )
        if r.returncode == 0:
            ok("gensynai/ree:v0.1.0 image is present")
        else:
            fail("gensynai/ree:v0.1.0 image NOT found — run: docker pull gensynai/ree:v0.1.0")
            return False
    except Exception as e:
        fail(f"Cannot check REE image: {e}")
        return False

    # run_ree.sh
    ree_sh = Path(__file__).parent / "run_ree.sh"
    if ree_sh.exists():
        content = ree_sh.read_text()
        if "--cpu-only" in content:
            fail("run_ree.sh contains --cpu-only flag — this causes REE to hang! Remove it.")
        else:
            ok("run_ree.sh exists (no --cpu-only flag)")
    else:
        fail("run_ree.sh not found")

    # Git bash (needed on Windows)
    if sys.platform == "win32":
        for path in [r"C:\Program Files\Git\bin\bash.exe",
                     r"C:\Program Files\Git\usr\bin\bash.exe"]:
            if Path(path).exists():
                ok(f"Git bash found: {path}")
                break
        else:
            bash = shutil.which("bash")
            if bash:
                ok(f"bash found: {bash}")
            else:
                fail("bash not found — install Git for Windows")

    return True


# ── 4. Recent receipts ────────────────────────────────────────────────────────
def check_receipts():
    print(f"\n{SEP}")
    print("  4. Existing REE receipts")
    print(SEP)

    patterns = [
        str(Path.home() / ".cache" / "gensyn" / "**" / "metadata" / "receipt_*.json"),
        str(Path.home() / ".cache" / "gensyn" / "receipt_*.json"),
    ]
    receipts = []
    for p in patterns:
        receipts.extend(glob.glob(p, recursive=True))
    receipts = sorted(receipts, key=os.path.getmtime, reverse=True)

    if not receipts:
        warn("No receipts found — REE has never run successfully on this machine")
        info("Expected path: ~/.cache/gensyn/**/metadata/receipt_*.json")
    else:
        ok(f"{len(receipts)} receipt(s) found")
        latest = receipts[0]
        mtime  = os.path.getmtime(latest)
        age    = time.time() - mtime
        age_str = f"{int(age//60)}m ago" if age < 3600 else f"{int(age//3600)}h ago"
        ok(f"Latest: {Path(latest).name}  ({age_str})")
        try:
            with open(latest, encoding="utf-8") as f:
                rj = json.load(f)
            rh = rj.get("hashes", {}).get("receipt_hash") or rj.get("receipt_hash", "")
            if rh:
                ok(f"receipt_hash: {rh[:20]}…")
        except Exception as e:
            warn(f"Could not parse receipt: {e}")
        return latest
    return None


# ── 5. RPC connectivity ───────────────────────────────────────────────────────
def _rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req  = urllib.request.Request(
        RPC_URL, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


def check_rpc():
    print(f"\n{SEP}")
    print("  5. Gensyn Testnet RPC")
    print(SEP)

    # Block number
    try:
        block_hex = _rpc("eth_blockNumber", [])
        block_num = int(block_hex, 16)
        ok(f"RPC reachable — latest block: {block_num:,}")
    except Exception as e:
        fail(f"RPC unreachable: {e}")
        info(f"URL: {RPC_URL}")
        return False

    if not PRIVATE_KEY or PRIVATE_KEY == "0x":
        warn("Skipping wallet checks — GENSYN_PRIVATE_KEY not set")
        return True

    # Wallet
    try:
        from web3 import Web3
        w3 = Web3()
        account = w3.eth.account.from_key(PRIVATE_KEY)
        addr = account.address
        ok(f"Wallet address: {addr}")

        # Balance
        bal_hex = _rpc("eth_getBalance", [addr, "latest"])
        bal_wei = int(bal_hex, 16)
        bal_eth = bal_wei / 1e18
        if bal_eth >= 0.001:
            ok(f"Balance: {bal_eth:.6f} GEN (sufficient)")
        else:
            warn(f"Balance: {bal_eth:.8f} GEN — LOW, may not cover gas fees")

        # Nonce
        nonce_hex = _rpc("eth_getTransactionCount", [addr, "latest"])
        nonce = int(nonce_hex, 16)
        ok(f"Nonce: {nonce}")

    except Exception as e:
        fail(f"Wallet check failed: {e}")
        return False

    return True


# ── 6. TX build test (offline, no broadcast) ─────────────────────────────────
def check_tx_build():
    print(f"\n{SEP}")
    print("  6. Transaction signing (offline, no broadcast)")
    print(SEP)

    if not PRIVATE_KEY or PRIVATE_KEY == "0x":
        warn("Skipped — GENSYN_PRIVATE_KEY not set")
        return False

    try:
        from web3 import Web3
        w3      = Web3()
        account = w3.eth.account.from_key(PRIVATE_KEY)

        contract_file = Path(__file__).parent / "contract_address.json"
        cdata  = json.loads(contract_file.read_text()) if contract_file.exists() else {}
        c_addr = cdata.get("address")
        c_abi  = cdata.get("abi")

        dummy_hash  = "0x" + "ab" * 32
        dummy_cid   = "sha256:" + "cd" * 32
        dummy_model = "test/model"

        if c_addr and c_abi:
            contract = w3.eth.contract(address=c_addr, abi=c_abi)
            tx = contract.functions.submitReceipt(
                dummy_hash, dummy_cid, dummy_model
            ).build_transaction({
                "from": account.address, "nonce": 0,
                "gas": 200_000,
                "gasPrice": Web3.to_wei(1, "gwei"),
                "chainId": CHAIN_ID,
            })
            ok(f"Contract TX built: {len(tx['data'])} bytes calldata")
        else:
            tx = {
                "from": account.address, "to": account.address,
                "value": 0, "gas": 100_000,
                "gasPrice": Web3.to_wei(1, "gwei"),
                "nonce": 0, "chainId": CHAIN_ID,
                "data": Web3.to_hex(text=dummy_hash),
            }
            ok("Self-TX built (no contract file)")

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        raw    = "0x" + signed.raw_transaction.hex()
        ok(f"TX signed successfully ({len(raw)//2} bytes)")
        return True

    except Exception as e:
        fail(f"TX build/sign failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 7. Send real TX ───────────────────────────────────────────────────────────
def check_tx_send():
    print(f"\n{SEP}")
    print("  7. Sending real test transaction")
    print(SEP)

    if not PRIVATE_KEY or PRIVATE_KEY == "0x":
        fail("GENSYN_PRIVATE_KEY not set — cannot send TX")
        return

    try:
        from web3 import Web3
        w3      = Web3()
        account = w3.eth.account.from_key(PRIVATE_KEY)

        nonce_hex = _rpc("eth_getTransactionCount", [account.address, "latest"])
        nonce     = int(nonce_hex, 16)

        contract_file = Path(__file__).parent / "contract_address.json"
        cdata  = json.loads(contract_file.read_text()) if contract_file.exists() else {}
        c_addr = cdata.get("address")
        c_abi  = cdata.get("abi")

        test_hash  = "diag:" + hashlib.sha256(b"diagnose_test").hexdigest()
        test_cid   = "sha256:" + hashlib.sha256(b"diagnose_cid").hexdigest()
        test_model = "diagnose/test"

        if c_addr and c_abi:
            contract = w3.eth.contract(address=c_addr, abi=c_abi)
            tx = contract.functions.submitReceipt(
                test_hash, test_cid, test_model
            ).build_transaction({
                "from": account.address, "nonce": nonce,
                "gas": 200_000,
                "gasPrice": Web3.to_wei(1, "gwei"),
                "chainId": CHAIN_ID,
            })
        else:
            tx = {
                "from": account.address, "to": account.address,
                "value": 0, "gas": 100_000,
                "gasPrice": Web3.to_wei(1, "gwei"),
                "nonce": nonce, "chainId": CHAIN_ID,
                "data": Web3.to_hex(text=test_hash),
            }

        signed   = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        raw      = "0x" + signed.raw_transaction.hex()
        tx_hash  = _rpc("eth_sendRawTransaction", [raw])
        ok(f"TX sent: {tx_hash}")
        ok(f"Explorer: https://gensyn-testnet.explorer.alchemy.com/tx/{tx_hash}")

    except Exception as e:
        fail(f"TX send failed: {e}")
        import traceback
        traceback.print_exc()


# ── 8. Sessions check ─────────────────────────────────────────────────────────
def check_sessions():
    print(f"\n{SEP}")
    print("  8. REEcord sessions")
    print(SEP)

    sessions_file = Path(__file__).parent / "sessions.json"
    if not sessions_file.exists():
        info("sessions.json not found — no sessions yet")
        return

    try:
        with open(sessions_file, encoding="utf-8") as f:
            sessions = json.load(f)
        total      = len(sessions)
        finalized  = sum(1 for s in sessions.values() if s.get("finalized"))
        with_tx    = [s for s in sessions.values() if s.get("tx_hash")]

        ok(f"{total} session(s), {finalized} finalized")
        if with_tx:
            last = with_tx[-1]
            ok(f"Last TX: {last.get('tx_hash', '')[:20]}…")
    except Exception as e:
        fail(f"sessions.json parse error: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    send_tx = "--send-tx" in sys.argv

    print("\n" + "=" * 60)
    print("  REEcord Diagnostic")
    print("=" * 60)

    check_env()
    imports_ok = check_imports()
    docker_ok  = check_docker()
    check_receipts()
    rpc_ok     = check_rpc()
    if imports_ok:
        check_tx_build()
    check_sessions()

    if send_tx:
        if rpc_ok and imports_ok:
            check_tx_send()
        else:
            print(f"\n  {WARN} Skipping TX send — RPC or imports failed")

    print(f"\n{SEP}")
    print("  Done. Fix any ✗ items above, then restart the server.")
    print(f"{SEP}\n")

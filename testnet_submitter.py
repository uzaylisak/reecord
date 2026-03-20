#!/usr/bin/env python3
"""
Testnet Submitter  —  IPFS + on-chain recording.

Uses `requests` for reliable HTTP timeouts on Windows (urllib SSL hangs).
Uses threading.Thread + join(timeout) instead of ThreadPoolExecutor
to avoid executor.shutdown(wait=True) blocking on thread hang.
"""

import json
import os
import glob
import sys
import hashlib
import threading
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
RPC_URL     = os.getenv("GENSYN_RPC_URL",     "https://gensyn-testnet.g.alchemy.com/public")
PRIVATE_KEY = os.getenv("GENSYN_PRIVATE_KEY", "")
PINATA_JWT  = os.getenv("PINATA_API_KEY",     "")

CONTRACT_FILE  = Path(__file__).parent / "contract_address.json"
CHAIN_ID       = 685685
GAS_LIMIT      = 500_000   # generous limit; testnet gas is free
HTTP_TIMEOUT   = 25        # seconds per HTTP request
CHAIN_TIMEOUT  = 90        # seconds total for chain submission


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _post(url: str, payload: dict, headers: dict = None) -> dict:
    """POST JSON, return parsed response. Uses requests with explicit timeout."""
    import requests
    resp = requests.post(
        url,
        json=payload,
        headers=headers or {},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _rpc(method: str, params: list):
    """Call Gensyn Testnet JSON-RPC."""
    data = _post(RPC_URL, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in data:
        raise RuntimeError(f"RPC {method}: {data['error']}")
    return data["result"]


# ── IPFS ──────────────────────────────────────────────────────────────────────
def _ipfs_upload(receipt: dict) -> str:
    if not PINATA_JWT:
        print("[IPFS] No PINATA_API_KEY — using sha256 placeholder.")
        return "sha256:" + hashlib.sha256(
            json.dumps(receipt, sort_keys=True).encode()
        ).hexdigest()

    try:
        result = _post(
            "https://api.pinata.cloud/pinning/pinJSONToIPFS",
            {
                "pinataContent":  receipt,
                "pinataMetadata": {"name": f"ree-{str(receipt.get('receipt_hash', ''))[:10]}"},
            },
            headers={"Authorization": f"Bearer {PINATA_JWT}"},
        )
        cid = result["IpfsHash"]
        print(f"[IPFS] Pinned: {cid}")
        return cid
    except Exception as e:
        print(f"[IPFS] Error ({e}) — using sha256 placeholder.")
        return "sha256:" + hashlib.sha256(
            json.dumps(receipt, sort_keys=True).encode()
        ).hexdigest()


# ── On-chain ──────────────────────────────────────────────────────────────────
def _submit_onchain(receipt_hash: str, ipfs_cid: str, model_name: str) -> str:
    from web3 import Web3

    w3      = Web3()                               # offline — only used for signing
    account = w3.eth.account.from_key(PRIVATE_KEY)
    print(f"[Chain] Wallet : {account.address}")

    nonce = int(_rpc("eth_getTransactionCount", [account.address, "latest"]), 16)
    print(f"[Chain] Nonce  : {nonce}")

    cdata  = json.loads(CONTRACT_FILE.read_text()) if CONTRACT_FILE.exists() else {}
    c_addr = cdata.get("address")
    c_abi  = cdata.get("abi")
    gp     = Web3.to_wei(1, "gwei")

    if c_addr and c_abi:
        contract = w3.eth.contract(address=c_addr, abi=c_abi)
        tx = contract.functions.submitReceipt(
            receipt_hash, ipfs_cid, model_name
        ).build_transaction({
            "from": account.address, "nonce": nonce,
            "gas": GAS_LIMIT, "gasPrice": gp, "chainId": CHAIN_ID,
        })
    else:
        tx = {
            "from": account.address, "to": account.address,
            "value": 0, "gas": 100_000, "gasPrice": gp,
            "nonce": nonce, "chainId": CHAIN_ID,
            "data": Web3.to_hex(text=json.dumps({
                "receipt_hash": receipt_hash,
                "ipfs_cid":     ipfs_cid,
                "model_name":   model_name,
            })),
        }

    signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    raw     = "0x" + signed.raw_transaction.hex()
    tx_hash = _rpc("eth_sendRawTransaction", [raw])
    print(f"[Chain] ✓ TX sent: {tx_hash}", flush=True)

    # Wait up to 30s for receipt to check on-chain status
    print("[Chain] Waiting for receipt…", flush=True)
    import time as _time
    for attempt in range(6):   # 6 × 5s = 30s
        _time.sleep(5)
        try:
            rcpt = _rpc("eth_getTransactionReceipt", [tx_hash])
            if rcpt is None:
                print(f"[Chain] receipt not yet available (attempt {attempt+1}/6)…", flush=True)
                continue
            status = int(rcpt.get("status", "0x0"), 16)
            gas_used = int(rcpt.get("gasUsed", "0x0"), 16)
            if status == 1:
                print(f"[Chain] ✓ TX confirmed  gasUsed={gas_used}", flush=True)
            else:
                # Failed on-chain — log details and raise so caller sees "failed"
                print(f"[Chain] ✗ TX FAILED on-chain  gasUsed={gas_used}  gasLimit={GAS_LIMIT}", flush=True)
                if gas_used >= GAS_LIMIT * 0.99:
                    raise RuntimeError(
                        f"TX failed: OUT OF GAS (used {gas_used}/{GAS_LIMIT}). "
                        "Try again — gas limit has been increased."
                    )
                else:
                    raise RuntimeError(
                        f"TX reverted on-chain (gasUsed={gas_used}, limit={GAS_LIMIT}). "
                        "The contract likely rejected duplicate receipt_hash. "
                        f"TX: {tx_hash}"
                    )
            break
        except RuntimeError:
            raise
        except Exception as e:
            print(f"[Chain] receipt check error: {e}", flush=True)

    return tx_hash


# ── Main ──────────────────────────────────────────────────────────────────────
def submit_receipt(receipt_path: str) -> dict:
    if not PRIVATE_KEY:
        raise RuntimeError("GENSYN_PRIVATE_KEY is not set.")

    with open(receipt_path, encoding="utf-8") as f:
        receipt = json.load(f)

    receipt_hash = (
        receipt.get("hashes", {}).get("receipt_hash")
        or hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    )
    model_name = receipt.get("model", {}).get("name", "unknown")

    # 1. Encrypt (optional)
    try:
        from crypto_utils import encrypt_receipt, _CRYPTO_AVAILABLE
        if _CRYPTO_AVAILABLE and PRIVATE_KEY:
            upload_data = encrypt_receipt(receipt, PRIVATE_KEY)
            print("[Crypto] Encrypted.")
        else:
            upload_data = receipt
    except Exception:
        upload_data = receipt

    # 2. IPFS upload
    ipfs_cid = _ipfs_upload(upload_data)

    # 3. On-chain submission  ← run in daemon thread with hard timeout
    #    NOTE: we do NOT use `with ThreadPoolExecutor as ex:` because
    #    the context manager calls shutdown(wait=True) on exit, which
    #    would block forever if the thread hangs.
    print(f"[Chain] Submitting on-chain (max {CHAIN_TIMEOUT}s)…")

    _result = {}
    _error  = {}
    _lock   = threading.Lock()

    def _run():
        try:
            tx = _submit_onchain(receipt_hash, ipfs_cid, model_name)
            with _lock:
                _result["tx_hash"] = tx
        except Exception as exc:
            with _lock:
                _error["msg"] = str(exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=CHAIN_TIMEOUT)

    if t.is_alive():
        raise RuntimeError(
            f"Chain submission timed out after {CHAIN_TIMEOUT}s "
            "— testnet may be congested, try again later."
        )
    with _lock:
        if _error.get("msg"):
            raise RuntimeError(f"Chain error: {_error['msg']}")

    tx_hash = _result["tx_hash"]
    return {
        "tx_hash":      tx_hash,
        "ipfs_cid":     ipfs_cid,
        "ipfs_url":     f"https://ipfs.io/ipfs/{ipfs_cid}",
        "receipt_hash": receipt_hash,
        "model_name":   model_name,
        "status":       "SENT",
    }


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not PRIVATE_KEY:
        sys.exit("GENSYN_PRIVATE_KEY is not set.")
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        matches = sorted(glob.glob(
            str(Path.home() / ".cache/gensyn/**/receipt_*.json"), recursive=True))
        if not matches:
            sys.exit("No receipt found.")
        path = matches[-1]
    print(f"Submitting: {path}\n")
    r = submit_receipt(path)
    for k, v in r.items():
        print(f"  {k}: {v}")

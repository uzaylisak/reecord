# REEcord

> **⚠ Early Access — Use at your own risk**
> REEcord is currently in active development and testing. You may encounter bugs, unexpected errors, or breaking changes at any time. We are not responsible for any issues, data loss, or problems that may arise from using this software. Proceed with caution.

**Record AI conversations on-chain using Gensyn's REE (Reproducible Execution Environment).**

Chat with a local AI model via Ollama, then finalize the conversation — REE generates a cryptographic proof, the receipt is encrypted and uploaded to IPFS, and a transaction is submitted to the Gensyn Testnet. Every conversation becomes a verifiable, permanent on-chain record.

🌐 **[reecord.click](https://reecord.click)** — Browse all on-chain recorded conversations, verify receipts, and explore the transaction history on Gensyn Testnet.

💬 **[Telegram Support Group](https://t.me/+0vcuDovbHVc4ZTk8)** — Having issues or questions? Join our group for help.

---

## How it works

```
Ollama Chat  →  REE Proof  →  AES-256-GCM Encryption  →  Pinata IPFS  →  Gensyn Testnet
```

1. **Chat** — Talk to any Ollama model locally
2. **Finalize** — REE runs reproducible inference and generates a cryptographic receipt
3. **Encrypt** — Receipt is encrypted with AES-256-GCM (only you can decrypt)
4. **IPFS** — Encrypted receipt is pinned to IPFS via Pinata
5. **On-chain** — IPFS CID + receipt hash submitted to Gensyn Testnet smart contract

---

## Requirements

| Requirement | Link |
|---|---|
| Python 3.10+ | [python.org](https://www.python.org/downloads/) |
| Docker Desktop | [docker.com](https://www.docker.com/products/docker-desktop/) |
| Ollama | [ollama.com](https://ollama.com/download) |
| Git | [git-scm.com](https://git-scm.com/) |

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/uzaylisak/reecord.git
cd reecord
```

---

### 2. Install prerequisites

<details>
<summary><b>Windows</b></summary>

1. Install [Python 3.10+](https://www.python.org/downloads/) — check **Add to PATH** during install
2. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it
3. Install [Ollama](https://ollama.com/download/windows)
4. Install [Git for Windows](https://git-scm.com/) (required to run `.sh` scripts)

</details>

<details>
<summary><b>Linux (Ubuntu / Debian)</b></summary>

```bash
# Python
sudo apt update && sudo apt install python3 python3-pip git -y

# Docker
sudo apt install docker.io -y
sudo systemctl start docker
sudo usermod -aG docker $USER   # log out and back in after this

# Ollama
curl -fsSL https://ollama.com/install.sh | sh
```

</details>

<details>
<summary><b>macOS</b></summary>

```bash
# Homebrew (if not installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Python & Git (comes with Xcode tools, or via brew)
brew install python git

# Ollama
brew install ollama
```

Also install [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/).

</details>

---

### 3. Install Python dependencies

```bash
pip install flask requests web3 pycryptodome eth-account python-dotenv
```

---

### 4. Pull an Ollama model

```bash
ollama pull qwen2:0.5b
```

> REEcord uses Gensyn's REE for cryptographic proof generation. Only [REE-supported models](https://docs.gensyn.ai/tech/ree/supported-models) can be finalized on-chain. `qwen2:0.5b` is recommended as a starting point — larger models work but will be slower depending on your hardware.

---

### 5. Pull the REE Docker image

```bash
docker pull gensynai/ree:v0.1.0
```

---

### 6. Configure credentials

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Open `.env` and set:

```env
GENSYN_PRIVATE_KEY=your_private_key_here
GENSYN_WALLET_ADDRESS=0x...
PINATA_API_KEY=your_pinata_jwt_here
```

> **⚠ Use a dedicated empty wallet for testnet — never your main wallet.**

#### Getting a Gensyn Testnet wallet
1. Install [MetaMask](https://metamask.io/)
2. Create a new account
3. Add Gensyn Testnet: Chain ID `685685`, RPC `https://gensyn-testnet.g.alchemy.com/public`
4. Export private key: MetaMask → Account Details → Export Private Key
5. Get testnet ETH tokens from the [Gensyn faucet](https://www.alchemy.com/faucets/gensyn-testnet)

#### Getting a Pinata JWT (optional)
1. Sign up at [app.pinata.cloud](https://app.pinata.cloud)
2. Go to Developers → API Keys → New Key
3. Copy the JWT token

> Without Pinata, receipts use a local SHA-256 hash fallback instead of IPFS.

---

### 7. Start REEcord

```bash
python launcher.py
```

The app opens automatically in your browser at `http://localhost:5000`.
If `.env` is not configured, the setup page will open automatically.

---

## Updating

If you already have REEcord installed, pull the latest changes:

```bash
git pull
```

Then restart REEcord:

```bash
python launcher.py
```

---

## Usage

1. **Chat** — Type a message and press Enter (or click Send)
2. **Finalize & Record** — Click the button at the bottom when ready
3. **Wait** — REE inference runs (2–5 min depending on hardware)
4. **Done** — TX hash and IPFS link appear in the chat

> ⚡ Finalize speed depends on your hardware and network connection.

### Verifying your recording

Once finalized, you can verify your on-chain recording at **[reecord.click](https://reecord.click)**:

- Connect your **wallet** to see all your recordings
- Paste a **TX hash** to publicly verify any receipt
- Check the transaction on [Gensyn Testnet Explorer](https://gensyn-testnet.explorer.alchemy.com) to verify the on-chain proof
- The smart contract stores the receipt hash, IPFS CID, and model name — all publicly verifiable

---

## Project structure

```
reecord/
├── launcher.py          # Main entry point — Flask server + browser launcher
├── ollama_proxy.py      # Ollama API proxy + finalize job runner
├── ree_runner.py        # REE Docker wrapper
├── testnet_submitter.py # IPFS upload + Gensyn Testnet TX
├── crypto_utils.py      # AES-256-GCM encryption
├── run_ree.sh           # Shell script to run REE Docker container (cross-platform)
├── contract_address.json# Smart contract address + ABI
├── contracts/
│   └── ReceiptRegistry.sol  # Solidity contract source
├── web_ui/
│   ├── chat.html        # Main chat interface
│   └── setup.html       # First-run credential setup
└── .env.example         # Environment variable template
```

---

## Smart contract

Deployed on **Gensyn Testnet** (`chain_id: 685685`):

```
0x5e4708471b92774398801D9672c7d10c0A58F2b5
```

[View on Explorer ↗](https://gensyn-testnet.explorer.alchemy.com/address/0x5e4708471b92774398801D9672c7d10c0A58F2b5)

---

## Troubleshooting

**Finalize is slow**
REE runs inference on CPU inside Docker. 2–5 minutes is normal. Check Docker Desktop has enough RAM (4GB+ recommended).

**TX fails on-chain**
Make sure your wallet has testnet ETH tokens. Check balance at the Gensyn explorer.

**Docker not found**
Make sure Docker Desktop is running before starting REEcord.

**Ollama not found**
Make sure Ollama is running: `ollama serve`

**Permission denied on run_ree.sh (Linux/macOS)**
```bash
chmod +x run_ree.sh
```

---

## License

MIT

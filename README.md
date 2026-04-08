# bce-cli

Local **RAG-style retrieval** over your documents: chunk → **BCE** embeddings → **SQLite** index → dense search → **BCE reranker**. Outputs ranked passages only (no LLM answer synthesis).

**BCEmbedding** ([upstream](https://github.com/netease-youdao/BCEmbedding)) is a **normal Python dependency** (`pip install BCEmbedding`). This repo does **not** vendor that project; it adds a small LangChain-compatible rerank wrapper (`bcecli/rerank.py`) for current `langchain-core` / Pydantic v2.

## How to deploy

Pick **one** GPU stack (**CUDA** or **Intel XPU**) and **one** run mode (**local Python** or **Docker**). Use a **separate** venv or image per stack; do **not** mix CUDA and XPU in the same environment.

**Shared rules**

- Choose **one** stack (**CUDA** *or* **Intel XPU**) and **one** way to run (**local Python** *or* **Docker**); don’t mix in the same environment.
- **Hugging Face mirror (optional):** if downloads are slow or blocked, set **`HF_ENDPOINT`** before **`warmup_hf_models.py`** or **`docker build`** (examples below).

```bash
# Windows PowerShell
$env:HF_ENDPOINT = "https://hf-mirror.com"

# Linux / macOS
export HF_ENDPOINT=https://hf-mirror.com
```

---

### Local Python

1. **Python 3.10+** (3.12 tested), new venv or conda env.
2. **Install PyTorch for your GPU (pick one):**
  - **NVIDIA CUDA** — use **[Start Locally](https://pytorch.org/get-started/locally/)** for your OS/CUDA, or e.g.  
   `pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124`
  - **Intel XPU** — follow **[Getting Started on Intel GPU](https://docs.pytorch.org/docs/stable/notes/get_start_xpu.html)** (the PyTorch “Start Locally” widget often omits XPU). Install the **[Intel GPU driver](https://www.intel.com/content/www/us/en/developer/articles/tool/pytorch-prerequisites-for-intel-gpu.html)**, then e.g.  
  `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/xpu`  
  (optional nightly: `--pre` + `…/whl/nightly/xpu`).
3. **App deps:** `pip install -r requirements.txt`
4. **Models (once, before `index` / `search`):** from **this repo root**, with network, **`python warmup_hf_models.py`** → fills **`./models`** (same default **`HF_HOME`** as **`serve`** / **`bcecli.rag`**; Docker images use **`HF_HOME=/opt/hf`** instead). If your shell still has **`HF_HOME=/opt/hf`** from Docker examples, **unset** it locally or weights land in the wrong tree. Or copy BCE weights into **`./models`** yourself. **`bcecli` does not download weights for you.**
5. Run from repo root or set **`PYTHONPATH`** to the repo root.

**Verify GPU**

- CUDA: `python -c "import torch; print(torch.cuda.is_available())"` → `True`
- XPU: `python -c "import torch; print(torch.xpu.is_available())"` → `True`

On **Intel XPU**, only **embedding** runs on the GPU; **rerank** falls back to **CPU** (BCE `RerankerModel` has no XPU path).

---

### Docker (CUDA only)

Docker support in this repo is **CUDA-only** (`Dockerfile`). For Intel XPU, use the **Local Python** path above.

Build (weights are warmed into **`/opt/hf`** at image build time):

```bash
docker build -t bcecli .
docker build -t bcecli --build-arg HF_ENDPOINT=https://hf-mirror.com .
```

- Base: **`pytorch/pytorch`** (CUDA tag must match the host driver — [tags](https://hub.docker.com/r/pytorch/pytorch/tags), override **`PYTORCH_IMAGE`** in `Dockerfile`).
- Host: Linux + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html), or Windows **Docker Desktop + WSL2** with NVIDIA GPU.
- Run with **`--gpus all`** (or `'--gpus "device=0"'`).

```bash
docker run --name bcecli -it --gpus all -p 8765:8765 bcecli
docker run --rm --gpus all bcecli python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)"
```

- Map **`8765:8765`** for HTTP. Inside: `python bcecli.py serve --host 0.0.0.0 --port 8765` (optional **`-e`** **`BCECLI_API_TOKEN`**).
- Don’t mount an empty volume over **`/opt/hf`** unless you supply weights yourself.
- **Persistent data**:

```bash
docker run --name bcecli -it --gpus all -p 8765:8765 \
  -v bcecli-data:/data \
  -e BCECLI_REGISTRY=/data/bcecli_registry.json \
  bcecli
```

## Usage

Run a single backend service (`python bcecli.py serve`) that hosts both the API and built frontend (`bcecli/static`). The same instance handles authentication, KB permissions, retrieval APIs, and the web UI.

### Server environment variables


| Variable          | Meaning                                                                                                           |
| ----------------- | ----------------------------------------------------------------------------------------------------------------- |
| `BCECLI_REGISTRY`  | Path to the index registry JSON (default: `./bcecli_registry.json` under the repo root).                           |
| `BCECLI_APP_STORE` | App metadata backend. Default `sqlite` (see `server/store/factory.py`).                                          |
| `BCECLI_APP_DB`    | SQLite path for users/KB ACL (default: `<repo>/data/bcecli_app.sqlite`).                                           |
| `BCECLI_SESSION_TTL` | Session lifetime in seconds (default: 30 days).                                                                |
| `BCECLI_API_TOKEN` | If set, matching `Authorization: Bearer <token>` acts as **superuser** (not a substitute for user session on upload/build). |
| `HF_ENDPOINT`     | Hub URL for **warmup** / **`docker build`** downloads. Defaults to **`https://huggingface.co`** where applicable. |
| `HF_HOME`         | Weight directory. **Default:** **`./models`** (local) or **`/opt/hf`** (Docker).                                  |
| `BCECLI_DEVICE`    | Force `cuda:0` or `xpu:0` (optional). CPU is not supported.                                                       |


### Frontend + API routing notes

`frontend/` is a Vite app. Build output goes to `bcecli/static`, and `python bcecli.py serve` serves it directly. You only run one backend service in production.

For client-side scoped retrieval, set `BCECLI_API_KEY` (a user API key starting with `sk-`) and send it as `X-API-Key`. Use `GET /api/subscribe-indexes` for API key scope (owned + subscribed + permission-checked), and `GET /api/search/{index}` for queries. Do not use `GET /api/indexes` for API key scoped retrieval.

![](https://github.com/SugarSong404/bce-cli/blob/dev_webui/assets/screenshot.png?raw=true)

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

### Agent Skill

This repo includes a Agent Skill at **`SKILL.md`**.

- It guides AI assistants to use bce-cli in a safe and consistent way for retrieval workflows.
- Before running skill commands, users should declare `BCECLI_API_KEY` in their environment; skill commands should read that env var and avoid exposing secret values in chat/output.

## Corpus format

Recursive under `--dir`: `.pdf`, `.txt`, `.md`.

## Models

- Embedding: `maidalun1020/bce-embedding-base_v1`
- Reranker: `maidalun1020/bce-reranker-base_v1`

**Local:** run **`warmup_hf_models.py`** (or supply files under **`./models`**) before **`index` / `search`**. **Docker:** weights are fetched during **`docker build`** unless you change the Dockerfile.

## License

- **bcecli**: Apache-2.0.
- **BCEmbedding**: Apache-2.0 (see upstream repository).

## Upstream references

- [BCEmbedding (GitHub)](https://github.com/netease-youdao/BCEmbedding)
- Models on [Hugging Face](https://huggingface.co/maidalun1020) (`bce-embedding-base_v1`, `bce-reranker-base_v1`)


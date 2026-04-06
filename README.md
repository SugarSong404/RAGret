# bcrag

Local **RAG-style retrieval** over your documents: chunk → **BCE** embeddings → **SQLite** index → dense search → **BCE reranker**. Outputs ranked passages only (no LLM answer synthesis).

**BCEmbedding** ([upstream](https://github.com/netease-youdao/BCEmbedding)) is a **normal Python dependency** (`pip install BCEmbedding`). This repo does **not** vendor that project; it adds a small LangChain-compatible rerank wrapper (`bcrag_bce_rerank.py`) for current `langchain-core` / Pydantic v2.

## Deployment

| Approach | When to use |
|----------|-------------|
| **`pip install BCEmbedding`** (PyPI) | Default; pin the version in `requirements-rag.txt`. |
| **`pip install git+https://github.com/netease-youdao/BCEmbedding.git`** | You need a specific commit or branch. |
| **Fork + `pip install git+https://github.com/you/BCEmbedding.git@branch`** | You maintain patches upstream; still do not embed BCEmbedding inside this repo. |
| **Docker** (`Dockerfile`) | **GPU-first** image based on `pytorch/pytorch` (CUDA 12.4 runtime), plus BCEmbedding and LangChain. Requires NVIDIA drivers and GPU access in Docker (`--gpus all`). Rebuild after code changes. |

Do **not** commit a full clone of BCEmbedding into this repository unless you have a strong reason (for example air-gapped builds with a private mirror).

## Requirements

- Python **3.10+** (3.12 tested)
- NVIDIA GPU optional but recommended (CUDA PyTorch)
- Network for first-time **Hugging Face** model download (or use a mirror)

## Install

1. Create a virtual env or conda env.

2. Install **PyTorch** matching your CUDA driver (example for CUDA 12.4 wheels):

   ```bash
   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
   ```

3. Install the rest:

   ```bash
   pip install -r requirements-rag.txt
   ```

4. Run **from this repository root** (so `import bcrag_rag` resolves), or set `PYTHONPATH` to the repo root.

If `huggingface.co` is slow or blocked, set before running:

```bash
# Windows PowerShell
$env:HF_ENDPOINT = "https://hf-mirror.com"

# Linux / macOS
export HF_ENDPOINT=https://hf-mirror.com
```

## Docker

The `Dockerfile` defaults to **`pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime`** — that tag is only a **reproducible default**, not a requirement. Choose any official [`pytorch/pytorch`](https://hub.docker.com/r/pytorch/pytorch/tags) image whose CUDA version fits your **host NVIDIA driver**, and override at build time with `PYTORCH_IMAGE` (see `Dockerfile` comments). Your **host** must expose the GPU to Docker (`--gpus all`).

**Host prerequisites**

- **Linux:** [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) installed; `nvidia-smi` works on the host.
- **Windows:** Docker Desktop with **WSL2** backend, recent NVIDIA driver, and GPU support enabled for WSL/Docker (see Docker Desktop docs).

**Build**

```bash
docker build -t bcrag .
```

To use a **registry mirror** or a **different** `pytorch/pytorch` tag, set build-arg `PYTORCH_IMAGE` (see comments in the `Dockerfile`).

**Run (always pass the GPU)**

Use **`--gpus all`** (or `--gpus '"device=0"'` to pin one card). Without it, the container usually **cannot** use CUDA.

**Interactive shell** (`-it`; workdir `/app`)

```bash
docker run --rm -it --gpus all bcrag
```

**Persistent data** (SQLite indexes and `bcrag_registry.json` on a named volume)

```bash
docker run --rm -it --gpus all \
  -v bcrag-data:/data \
  -e BCRAG_REGISTRY=/data/bcrag_registry.json \
  bcrag
```

Inside the container, point `--dir` at corpus data under `/data` (for example copy files into `/data/corpus`, or mount a read-only corpus directory: `-v /path/on/host/corpus:/data/corpus:ro`).

**HTTP API reachable from the host**

```bash
docker run --rm -it --gpus all -p 8765:8765 \
  -v bcrag-data:/data \
  -e BCRAG_REGISTRY=/data/bcrag_registry.json \
  bcrag
# in the shell:
python bcrag.py serve --host 0.0.0.0 --port 8765
```

Set `BCRAG_API_TOKEN` and `HF_ENDPOINT` on `docker run` with `-e` when needed.

**Verify GPU in the container**

```bash
docker run --rm --gpus all bcrag python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)"
```

**One-shot command** (no interactive shell)

```bash
docker run --rm --gpus all bcrag python bcrag.py -h
```

**CPU-only machines** do not match this image’s intent; use a local virtualenv and CPU PyTorch (see **Install** above).

## Usage

All entry points go through **`bcrag.py`**. Split responsibilities as follows.

### Server (index host)

Use the **server** role on the machine that:

- Holds or can read the document corpus
- Runs **embedding and reranking** (GPU recommended)
- Builds or updates the SQLite index
- Optionally runs the **read-only HTTP API** for remote or scripted queries

**Typical workflow**

```bash
# Help
python bcrag.py -h

# Build an index: writes <parent_of_corpus>/<name>.sqlite
python bcrag.py index --dir path/to/corpus_folder
python bcrag.py index --dir path/to/corpus_folder --name my_index

# Register a logical name for HTTP lookup (same host as `serve`)
python bcrag.py index --dir path/to/corpus --register-as mydocs

# Start the HTTP API (default bind: 127.0.0.1:8765)
python bcrag.py serve
python bcrag.py serve --host 127.0.0.1 --port 8765
```

**HTTP API (read-only)** — exposed by `serve`:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{index_name}?query=...` | Resolve `index_name` in the registry → SQLite path, run search. Optional query params: `k`, `threshold`, `top_n`, `q`; `format=text` returns plain text instead of JSON (`result` mirrors CLI `search` output). |
| GET | `/indexes` | List registered indexes, `db_path`, and `sqlite_exists`. |
| GET | `/`, `/health` | Short discovery / health. |

**Environment variables (server-focused)**

| Variable | Meaning |
|----------|---------|
| `BCRAG_REGISTRY` | Path to the index registry JSON (default: `./bcrag_registry.json` under the repo root). |
| `BCRAG_API_TOKEN` | If set, every HTTP request must send `Authorization: Bearer <token>`. |
| `HF_ENDPOINT` | Hugging Face mirror or endpoint (see Install). |

### Client

Use the **client** role when you only need to **query** an existing deployment—without rebuilding indexes on that machine.

**HTTP client (against `python bcrag.py serve`)**

You only need a tool that can issue HTTP requests (for example `curl`). Default server URL: `http://127.0.0.1:8765`. If `BCRAG_API_TOKEN` is set on the server, send the header on every request.

```bash
# List indexes
curl -sS "http://127.0.0.1:8765/indexes"

# Search (JSON body includes field "result")
curl -sS -G "http://127.0.0.1:8765/mydocs" --data-urlencode "query=your question here"
```

PowerShell example:

```powershell
curl.exe -sS -G "http://127.0.0.1:8765/mydocs" --data-urlencode "query=your question here"
```

With bearer token:

```bash
curl -sS -H "Authorization: Bearer YOUR_TOKEN" "http://127.0.0.1:8765/indexes"
```

### Agent Skill

This repo includes a Agent Skill at **SKILL.md`**. It tells the AI assistant how to use **deployed bcrag HTTP API** safely and consistently:

- **Retrieval:** run **`curl`** in the terminal with **GET** only — list indexes (`GET /indexes`), then search (`GET /{index_name}?query=...`). Parse the JSON **`result`** field (or `format=text`).
- **Remote-first:** the skill assumes a **base URL** (and optional **bearer token** and **index name**) supplied by the user or environment — not hard-coded localhost paths.
- **Indexing:** agents are instructed **not** to trigger builds or registry changes just to answer a question; indexing stays on the **server** (CLI / ops). The HTTP API does not support `POST` / `DELETE` for indexes.

## Corpus format

Recursive under `--dir`: `.pdf`, `.txt`, `.md`.

## Models

- Embedding: `maidalun1020/bce-embedding-base_v1`
- Reranker: `maidalun1020/bce-reranker-base_v1`

Weights download automatically from Hugging Face on first use.

## License

- **bcrag**: Apache-2.0.
- **BCEmbedding**: Apache-2.0 (see upstream repository).

## Upstream references

- [BCEmbedding (GitHub)](https://github.com/netease-youdao/BCEmbedding)
- Models on [Hugging Face](https://huggingface.co/maidalun1020) (`bce-embedding-base_v1`, `bce-reranker-base_v1`)

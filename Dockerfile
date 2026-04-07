# bcecli — interactive shell, GPU PyTorch (CUDA), BCE + LangChain stack
#
# Host requirements: NVIDIA driver + NVIDIA Container Toolkit (Linux) or
# Docker Desktop with WSL2 GPU (Windows). Run with: --gpus all
#
# Build (downloads ~2× BCE model weights into the image; needs Hugging Face network):
#   docker build -t bcecli .
# Slow HF from China, set at build time:
#   docker build -t bcecli --build-arg HF_ENDPOINT=https://hf-mirror.com .
#
# If Docker Hub is slow, override the base (example mirror — verify URL still works):
#   docker build -t bcecli --build-arg PYTORCH_IMAGE=docker.m.daocloud.io/pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime .
#
# Run (GPU):
#   docker run --rm -it --gpus all bcecli
#
# Persistent data:
#   docker run --rm -it --gpus all -v bcecli-data:/data -e BCECLI_REGISTRY=/data/bcecli_registry.json bcecli
#
# Inside the shell:
#   python bcecli.py index --dir /data/corpus --register-as myindex
#   python bcecli.py serve --host 0.0.0.0 --port 8765
#
# Base image tag is NOT fixed by bcecli — it is only a default pin for reproducible
# builds. Pick any official pytorch/pytorch tag that matches your host NVIDIA
# driver / CUDA (see https://hub.docker.com/r/pytorch/pytorch/tags ). Example
# pattern: <pytorch>-cuda<CUDA>-cudnn<major>-runtime. Override with:
#   docker build --build-arg PYTORCH_IMAGE=pytorch/pytorch:<tag-from-docker-hub> -t bcecli .

ARG PYTORCH_IMAGE=pytorch/pytorch:2.10.0-cuda13.0-cudnn9-runtime
FROM ${PYTORCH_IMAGE}

ARG HF_ENDPOINT=https://huggingface.co

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    curl \
    git \
    jq \
    less \
    nano \
    procps \
    unzip \
    vim-tiny \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HF_ENDPOINT=${HF_ENDPOINT} \
    HF_HOME=/opt/hf \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_BREAK_SYSTEM_PACKAGES=1

# Base image uses PEP 668 "externally managed" Python; allow pip in this image only.
# Base image already ships torch/torchvision with CUDA; do not reinstall CPU wheels.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        "numpy>=1.24" \
        "pydantic>=2" \
        langchain-core \
        langchain-community \
        langchain-huggingface \
        langchain-text-splitters \
        BCEmbedding \
        "sentencepiece>=0.1.99" \
        "protobuf>=3.20" \
        pypdf

COPY . .

# Bake embedding + reranker weights into the image so first index/search does not re-download.
RUN mkdir -p /opt/hf && python /app/warmup_hf_models.py

RUN printf '%s\n' \
    'if [ -d /app ]; then cd /app; fi' \
    'alias ll="ls -la"' \
    '# bcecli: python bcecli.py -h | index | search | serve' \
    >> /root/.bashrc

EXPOSE 8765

CMD ["/bin/bash"]

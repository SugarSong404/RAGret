# RAGret — interactive shell, GPU PyTorch (CUDA), BCE + LangChain stack
#
# Host requirements: NVIDIA driver + NVIDIA Container Toolkit (Linux) or
# Docker Desktop with WSL2 GPU (Windows). Run with: --gpus all
#
# Build (downloads ~2× BCE model weights into the image; needs Hugging Face network):
#   docker build -t ragret .
# Slow HF from China, set at build time:
#   docker build -t ragret --build-arg HF_ENDPOINT=https://hf-mirror.com .
#
# Skip baking models into the image; at run time mount a host Hugging Face cache
# (same layout as local ./models after python warmup_hf_models.py) onto /opt/hf:
#   docker build -t ragret --build-arg RAGRET_SKIP_WARMUP=1 .
#   docker run -it --gpus all -v /path/on/host/models:/opt/hf ragret
#
# If Docker Hub is slow, override the base (example mirror — verify URL still works):
#   docker build -t ragret --build-arg PYTORCH_IMAGE=docker.m.daocloud.io/pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime .
#
# Run (GPU):
#   docker run -it --gpus all ragret
#
# Persistent data (default paths under /app/runtime):
#   docker run -it --gpus all -v ragret-runtime:/app/runtime ragret
#
# Inside the shell:
#   python ragret.py index --dir /data/corpus --register-as myindex
#   python ragret.py serve --host 0.0.0.0 --port 8765
#
# Base image tag is NOT fixed by RAGret — it is only a default pin for reproducible
# builds. Pick any official pytorch/pytorch tag that matches your host NVIDIA
# driver / CUDA (see https://hub.docker.com/r/pytorch/pytorch/tags ). Example
# pattern: <pytorch>-cuda<CUDA>-cudnn<major>-runtime. Override with:
#   docker build --build-arg PYTORCH_IMAGE=pytorch/pytorch:<tag-from-docker-hub> -t ragret .

ARG PYTORCH_IMAGE=pytorch/pytorch:2.10.0-cuda13.0-cudnn9-runtime
FROM ${PYTORCH_IMAGE}

ARG HF_ENDPOINT=https://huggingface.co
# Set to 1 to skip warmup_hf_models.py at build (use a host-mounted HF cache at /opt/hf).
ARG RAGRET_SKIP_WARMUP=0

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    curl \
    git \
    jq \
    less \
    nano \
    nodejs \
    npm \
    procps \
    unzip \
    vim-tiny \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Skip Transformers on-the-fly safetensors conversion (extra Hub API calls, e.g. list commits on PR refs).
# Helps flaky/build-time networks; main model files still download from HF_ENDPOINT as usual.
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HF_ENDPOINT=${HF_ENDPOINT} \
    HF_HOME=/opt/hf \
    DISABLE_SAFETENSORS_CONVERSION=true \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_BREAK_SYSTEM_PACKAGES=1

# Base image uses PEP 668 "externally managed" Python; allow pip in this image only.
# Base image already ships torch/torchvision with CUDA; do not reinstall CPU wheels.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake embedding + reranker weights into the image unless RAGRET_SKIP_WARMUP=1 (then mount /opt/hf).
RUN mkdir -p /opt/hf && \
    if [ "$RAGRET_SKIP_WARMUP" != "1" ]; then python /app/warmup_hf_models.py; fi

RUN printf '%s\n' \
    'if [ -d /app ]; then cd /app; fi' \
    'alias ll="ls -la"' \
    '# RAGret: python ragret.py -h | index | search | serve' \
    >> /root/.bashrc

EXPOSE 8765

CMD ["/bin/bash"]

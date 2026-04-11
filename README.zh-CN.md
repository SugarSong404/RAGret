<h1 align="center" style="font-size: 2.75em; font-weight: 700; border-bottom: none;">RAGret</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/SugarSong404/RAGret?style=flat-square" alt="License" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+" /></a>
  <a href="https://www.docker.com/"><img src="https://img.shields.io/badge/docker-CUDA-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker (CUDA)" /></a>
  <a href="https://github.com/SugarSong404/RAGret"><img src="https://img.shields.io/github/stars/SugarSong404/RAGret?style=flat-square&logo=github" alt="GitHub stars" /></a>
  <a href="https://github.com/SugarSong404/RAGret/pulls"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square" alt="PRs welcome" /></a>
</p>

<p align="center">English · <a href="README.md">README.md</a></p>

## RAGret 是什么？

`RAGret`（不是 “regret” 后悔 😂）是一款自托管的 RAG 网页应用，面向小型团队（约 15–30 人）与低成本服务器（显存 ≤ 8 GB）。

使用 `RAGret`，团队成员可以把知识库发布到共享中心、订阅他人的知识库，之后通过 HTTP GET 进行检索。

### 亮点

- 创建者可设置可见范围，灵活控制访问。
- **对 Agent 友好**：提供 **API 密钥** 与 **SKILL.md**，便于智能体快速接入团队工作流。
- 支持通过 **tar 上传** 或 **GitLab / GitHub Webhook** 入库，贴合常见文档存放习惯；飞书等在线文档同步已在规划中。
- 多格式：**PDF、Word（docx）、Excel（xlsx）、Markdown（md）、邮件（eml）、TXT、CSV、网页链接（html）**。
- 中英双语界面、浅色 / 深色主题，并可通过 YAML 调整品牌（如 favicon、页面标题）。

### 技术栈

索引与检索采用 **BCE 嵌入 + SQLite + BCE 重排序**，依赖：

- [BCEmbedding（GitHub）](https://github.com/netease-youdao/BCEmbedding)
- [Hugging Face 上的模型](https://huggingface.co/maidalun1020)（`bce-embedding-base_v1`、`bce-reranker-base_v1`）

## 快速开始

**GPU 二选一：** **CUDA** 或 **Intel XPU**。**运行方式二选一：** **本机 Python** 或 **Docker**。

**通用说明：**

- 每个环境只使用一种 GPU 方案与一种运行方式。
- **Hugging Face 镜像（可选）：** 若下载慢或被墙，在运行 **`warmup_hf_models.py`** 或 **`docker build`** 之前设置 **`HF_ENDPOINT`**（见下）。

```bash
# Windows PowerShell
$env:HF_ENDPOINT = "https://hf-mirror.com"

# Linux / macOS
export HF_ENDPOINT=https://hf-mirror.com
```

### 环境准备

#### 本机 Python

1. **Python 3.10+**（已在 3.12 上测试）。创建 venv 或 conda 环境。
2. **按你的 GPU 安装 PyTorch（二选一）：**
   - **NVIDIA CUDA：** 按 **[Start Locally](https://pytorch.org/get-started/locally/)** 安装，或例如：  
     `pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124`
   - **Intel XPU：** 按 **[Get started with Intel GPU](https://docs.pytorch.org/docs/stable/notes/get_start_xpu.html)**。安装 [Intel GPU 驱动](https://www.intel.com/content/www/us/en/developer/articles/tool/pytorch-prerequisites-for-intel-gpu.html) 后，例如：  
     `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/xpu`
3. **应用依赖：** `pip install -r requirements.txt`
4. **模型（首次，在索引/检索之前）：** 在**仓库根目录**联网执行：

   ```bash
   python warmup_hf_models.py
   ```

   权重会下载到 **`./models`**。也可手动将 BCE 权重放入 **`./models`**。

5. **验证 GPU：**
   - CUDA：`python -c "import torch; print(torch.cuda.is_available())"` → 应输出 `True`
   - XPU：`python -c "import torch; print(torch.xpu.is_available())"` → 应输出 `True`

   在 **Intel XPU** 上，仅 **嵌入（embedding）** 使用 GPU；上游 **BCEmbedding** 的 **重排序（rerank）** **不支持 XPU**。

---

#### Docker（仅 CUDA）

本仓库镜像仅针对 **CUDA**（`Dockerfile`）。若使用 Intel XPU，请用上面的**本机 Python** 方式。

构建（warmup 会把权重打进镜像内的 **`/opt/hf`**）：

```bash
docker build -t ragret .
# 国内镜像；使用时请关闭代理
docker build -t ragret --build-arg HF_ENDPOINT=https://hf-mirror.com .
```

运行需 **`--gpus all`**（或 `'--gpus "device=0"'`）。

```bash
docker run --name ragret -it --gpus all -p 8765:8765 ragret
```

### 启动服务

在 `frontend` 目录：

```bash
npm run build
```

在仓库根目录：

```bash
python ragret.py serve --host 0.0.0.0 --port 8765
```

## 使用说明

### 偏好与凭据

打开 **账户（Account）**：

![account](assets/screenshot_account.png)

1. 修改头像、主题与语言。
2. 最多创建 **3 个 API 密钥**，用于检索你拥有或已订阅的知识库。
3. 若使用 GitHub 或 GitLab Webhook，在对应栏粘贴 **PAT**。为安全起见，请将 PAT 权限限定为**只读仓库**。

### 创建知识库

打开 **添加知识库（Add knowledge base）**：

![add](assets/screenshot_add.png)

1. **必填：** 名称与描述，便于 Agent 选对知识库。
2. 可选：类 README 的描述文件与封面图。
3. 设置可见性。**锁定（Locked）** 默认仅**创建者**可见；创建后可添加成员。
4. 选择类型。**Tar 上传**较直接；**Webhook** 见下图。

![webhook](assets/screenshot_webhook.png)

首次构建会从仓库拉取，因此 **仓库 URL** 与 **分支** 为必填。将 **Webhook URL** 与 **Secret Token** 复制到仓库的 Webhook 设置中，然后点击构建。

### 进行中的任务

在普通硬件上，分块与索引会排队。每次点击构建或 Webhook 触发都会注册一个任务。

打开 **任务列表（Task list）** 查看排队与运行中的任务：

![task](assets/screenshot_task.png)

需要时可取消任务。

![Tdetail](assets/screenshot_Tdetail.png)

### 管理你的知识库

打开 **我的知识库（My knowledge bases）**，选择一个进行管理。

注意：

1. 对 **Webhook** 类型知识库，若重命名知识库，请在仓库中更新 Webhook URL。
2. 所有类型的**重建均为增量**。若通过 **tar** 添加文件，请上传你希望被索引的**完整文档集**的压缩包。
3. Webhook 类型可在此页手动从仓库拉取。

![rebuild](assets/screenshot_rebuild.png)

4. 使用页面底部搜索框可针对该知识库试检索。

### 使用知识库

1. 在 **知识中心（Knowledge hub）** 订阅。
2. 在 **账户** 中复制 API 密钥。
3. 设置环境变量 **`RAGRET_API_KEY`**。

**GET 请求示例：**

```bash
# 列出已订阅的索引
curl -sS -H "X-API-Key: $RAGRET_API_KEY" "$BASE/api/subscribe-indexes"
# 检索
curl -sS -G "$BASE/api/search/INDEX_NAME" -H "X-API-Key: $RAGRET_API_KEY" --data-urlencode "query=…"
```

**智能体：** 下载 `SKILL.md` 并导入 Claude Code、Cursor、OpenClaw 或其他 Agent 工具。

## 路线图

1. 更多格式：表格、PPT、图片。
2. 与飞书等在线文档同步。
3. 分布式部署，以支持更高并发与更大团队。
4. 全栈稳定性改进。

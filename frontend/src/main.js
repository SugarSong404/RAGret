const statusEl = document.getElementById("status");
const listEl = document.getElementById("kb-list");
const formEl = document.getElementById("upload-form");
const refreshBtn = document.getElementById("refresh-btn");
const langBtn = document.getElementById("lang-btn");
const uploadProgressEl = document.getElementById("upload-progress");
const uploadProgressTextEl = document.getElementById("upload-progress-text");
const buildProgressEl = document.getElementById("build-progress");
const buildProgressTextEl = document.getElementById("build-progress-text");
const submitBtn = document.getElementById("submit-btn");
const archiveInput = document.getElementById("kb-archive");
const pickArchiveBtn = document.getElementById("pick-archive-btn");
const pickedFileNameEl = document.getElementById("picked-file-name");
const descInput = document.getElementById("kb-description");
const nameInput = document.getElementById("kb-name");

const STATE_KEY = "bcecli.frontend.state.v2";
let currentLang = "en";
let stagedUploadId = null;
let currentJobId = null;
let uploadXhr = null;

const i18n = {
  en: {
    appTitle: "bce-cli Knowledge Console",
    appSubtitle: "Upload a tar archive to register an index, or delete existing ones.",
    uploadTitle: "Upload & Register",
    indexName: "Index name",
    indexDescription: "Description",
    archiveLabel: "Archive (.tar / .tar.gz / .tgz)",
    uploadProgress: "Upload progress",
    buildProgress: "Build progress",
    buildIdle: "Idle",
    startBuild: "Build Index",
    building: "Building...",
    chooseFile: "Choose File",
    noFileChosen: "No file chosen",
    registeredTitle: "Registered Indexes",
    refresh: "Refresh",
    delete: "Delete",
    empty: "No indexes registered yet",
    confirmDelete: (name) => `Delete index "${name}"?`,
    deleting: (name) => `Deleting ${name}...`,
    deleted: (name) => `Deleted ${name}.`,
    requireFields: "Please provide index name and description.",
    requireStaged: "Choose a tar archive and wait until upload finishes.",
    uploadStart: "Uploading archive...",
    uploadStaged: "Archive uploaded. Enter name/description and click Build.",
    uploadFailed: "Upload failed.",
    buildDone: (name) => `Index ${name} built and registered.`,
    ready: "Ready.",
    phase_queued: "Queued",
    phase_extract: "Extract",
    phase_load: "Load documents",
    phase_chunk: "Chunk",
    phase_embed: "Embed",
    phase_sqlite: "Write SQLite",
    phase_register: "Register",
    phase_done: "Done",
    phase_error: "Error",
  },
  zh: {
    appTitle: "bce-cli 知识库控制台",
    appSubtitle: "上传 tar 归档注册知识库，或删除已有知识库。",
    uploadTitle: "上传并注册",
    indexName: "知识库名称",
    indexDescription: "描述",
    archiveLabel: "归档（.tar / .tar.gz / .tgz）",
    uploadProgress: "上传进度",
    buildProgress: "构建进度",
    buildIdle: "空闲",
    startBuild: "开始构建",
    building: "构建中...",
    chooseFile: "选择文件",
    noFileChosen: "未选择文件",
    registeredTitle: "已注册知识库",
    refresh: "刷新列表",
    delete: "删除",
    empty: "暂无注册知识库",
    confirmDelete: (name) => `确认删除知识库 "${name}" 吗？`,
    deleting: (name) => `正在删除 ${name}...`,
    deleted: (name) => `已删除 ${name}。`,
    requireFields: "请填写知识库名称和描述。",
    requireStaged: "请选择 tar 并等待上传完成。",
    uploadStart: "正在上传归档...",
    uploadStaged: "归档已上传。填写名称/描述后点击构建。",
    uploadFailed: "上传失败。",
    buildDone: (name) => `知识库 ${name} 构建完成并已注册。`,
    ready: "就绪。",
    phase_queued: "排队",
    phase_extract: "解压",
    phase_load: "加载文档",
    phase_chunk: "分块",
    phase_embed: "向量化",
    phase_sqlite: "写入 SQLite",
    phase_register: "注册",
    phase_done: "完成",
    phase_error: "错误",
  },
};

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.className = isError ? "error" : "";
}

function saveState() {
  localStorage.setItem(
    STATE_KEY,
    JSON.stringify({
      lang: currentLang,
      stagedUploadId,
      currentJobId,
      fileName: pickedFileNameEl.textContent,
      uploadPercent: Number(uploadProgressEl.value || 0),
      buildPercent: Number(buildProgressEl.value || 0),
      buildText: buildProgressTextEl.textContent || "",
      name: nameInput.value || "",
      description: descInput.value || "",
    }),
  );
}

function clearJobState() {
  currentJobId = null;
  buildProgressEl.value = 0;
  buildProgressTextEl.textContent = i18n[currentLang].buildIdle;
  saveState();
}

function loadState() {
  try {
    const raw = localStorage.getItem(STATE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

async function fetchJSON(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function phaseText(phase) {
  return i18n[currentLang][`phase_${phase || "queued"}`] || String(phase || "");
}

async function loadIndexes() {
  const data = await fetchJSON("/api/indexes");
  const items = data.indexes || [];
  listEl.innerHTML = "";
  if (items.length === 0) {
    listEl.innerHTML = `<li class='empty'>${i18n[currentLang].empty}</li>`;
    return;
  }
  for (const item of items) {
    const li = document.createElement("li");
    li.innerHTML = `<div><strong>${item.name}</strong><p>${item.description || ""}</p></div><button data-name="${item.name}" class="danger">${i18n[currentLang].delete}</button>`;
    listEl.appendChild(li);
  }
}

function setLang(lang) {
  currentLang = lang;
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (i18n[currentLang][key]) el.textContent = i18n[currentLang][key];
  });
  nameInput.placeholder = lang === "zh" ? "例如：handbook" : "e.g. handbook";
  descInput.placeholder = lang === "zh" ? "知识库描述" : "Index description";
  langBtn.textContent = lang === "zh" ? "English" : "中文";
  saveState();
}

function startUpload(file) {
  if (uploadXhr) uploadXhr.abort();
  stagedUploadId = null;
  uploadProgressEl.value = 0;
  uploadProgressTextEl.textContent = "0%";
  setStatus(i18n[currentLang].uploadStart);
  saveState();

  const fd = new FormData();
  fd.append("file", file);
  const xhr = new XMLHttpRequest();
  uploadXhr = xhr;
  xhr.open("POST", "/api/upload");
  xhr.upload.onprogress = (evt) => {
    if (!evt.lengthComputable) return;
    const p = Math.min(100, Math.round((evt.loaded / evt.total) * 100));
    uploadProgressEl.value = p;
    uploadProgressTextEl.textContent = `${p}%`;
    saveState();
  };
  xhr.onload = () => {
    uploadXhr = null;
    let data = {};
    try {
      data = JSON.parse(xhr.responseText || "{}");
    } catch {
      setStatus(i18n[currentLang].uploadFailed, true);
      return;
    }
    if (xhr.status >= 200 && xhr.status < 300 && data.ok && data.upload_id) {
      stagedUploadId = data.upload_id;
      uploadProgressEl.value = 100;
      uploadProgressTextEl.textContent = "100%";
      setStatus(i18n[currentLang].uploadStaged);
      saveState();
      return;
    }
    setStatus(data.error || i18n[currentLang].uploadFailed, true);
  };
  xhr.onerror = () => {
    uploadXhr = null;
    setStatus(i18n[currentLang].uploadFailed, true);
  };
  xhr.send(fd);
}

async function pollJob(jobId) {
  currentJobId = jobId;
  saveState();
  while (true) {
    const job = await fetchJSON(`/api/jobs/${encodeURIComponent(jobId)}`);
    buildProgressEl.value = Number(job.percent || 0);
    buildProgressTextEl.textContent = `${job.percent || 0}% — ${phaseText(job.phase)}${job.detail ? ` · ${job.detail}` : ""}`;
    saveState();
    if (job.status === "done") {
      clearJobState();
      return job;
    }
    if (job.status === "error") {
      clearJobState();
      throw new Error(job.error || "Build failed");
    }
    await new Promise((r) => setTimeout(r, 500));
  }
}

formEl.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = nameInput.value.trim();
  const description = descInput.value.trim();
  if (!name || !description) return setStatus(i18n[currentLang].requireFields, true);
  if (!stagedUploadId) return setStatus(i18n[currentLang].requireStaged, true);

  submitBtn.disabled = true;
  submitBtn.textContent = i18n[currentLang].building;
  try {
    const start = await fetchJSON("/api/indexes/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description, upload_id: stagedUploadId }),
    });
    const done = await pollJob(start.job_id);
    await loadIndexes();
    setStatus(i18n[currentLang].buildDone(done.result?.name || name));
    stagedUploadId = null;
    clearJobState();
    archiveInput.value = "";
    pickedFileNameEl.textContent = i18n[currentLang].noFileChosen;
    uploadProgressEl.value = 0;
    uploadProgressTextEl.textContent = "0%";
    nameInput.value = "";
    descInput.value = "";
    saveState();
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = i18n[currentLang].startBuild;
  }
});

refreshBtn.addEventListener("click", () => {
  loadIndexes().catch((err) => setStatus(err.message, true));
});

listEl.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-name]");
  if (!btn) return;
  const name = btn.dataset.name;
  if (!confirm(i18n[currentLang].confirmDelete(name))) return;
  try {
    setStatus(i18n[currentLang].deleting(name));
    await fetchJSON(`/api/indexes/${encodeURIComponent(name)}?delete_sqlite=1`, { method: "DELETE" });
    await loadIndexes();
    setStatus(i18n[currentLang].deleted(name));
  } catch (err) {
    setStatus(err.message, true);
  }
});

langBtn.addEventListener("click", async () => {
  setLang(currentLang === "en" ? "zh" : "en");
  await loadIndexes().catch((err) => setStatus(err.message, true));
});

pickArchiveBtn.addEventListener("click", () => archiveInput.click());
archiveInput.addEventListener("change", () => {
  const f = archiveInput.files[0];
  pickedFileNameEl.textContent = f?.name || i18n[currentLang].noFileChosen;
  if (f) startUpload(f);
  else {
    if (uploadXhr) uploadXhr.abort();
    stagedUploadId = null;
    uploadProgressEl.value = 0;
    uploadProgressTextEl.textContent = "0%";
    saveState();
  }
});

const restored = loadState();
if (restored?.lang === "zh" || restored?.lang === "en") currentLang = restored.lang;
setLang(currentLang);
if (restored) {
  stagedUploadId = restored.stagedUploadId || null;
  currentJobId = restored.currentJobId || null;
  nameInput.value = restored.name || "";
  descInput.value = restored.description || "";
  uploadProgressEl.value = Number(restored.uploadPercent || 0);
  uploadProgressTextEl.textContent = `${Math.round(uploadProgressEl.value)}%`;
  buildProgressEl.value = Number(restored.buildPercent || 0);
  buildProgressTextEl.textContent = restored.buildText || i18n[currentLang].buildIdle;
  pickedFileNameEl.textContent = restored.fileName || i18n[currentLang].noFileChosen;
}
setStatus(i18n[currentLang].ready);
loadIndexes().catch((err) => setStatus(err.message, true));
if (currentJobId) {
  pollJob(currentJobId)
    .then(async () => {
      await loadIndexes();
      clearJobState();
    })
    .catch((err) => {
      clearJobState();
      setStatus(err.message, true);
    });
}

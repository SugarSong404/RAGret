const AUTH_TOKEN_KEY = "bcecli.auth.token";
const STATE_KEY = "bcecli.frontend.state.v3";
/** Persisted interface language (mirrored from state for stable preference). */
const UI_LANG_KEY = "bcecli.ui.lang";
/** Served from Vite `public/` → static root */
const DEFAULT_AVATAR_URL = "/default-avatar.svg";
const DEFAULT_KB_ICON_URL = "/default-kb.svg";

let currentLang = "en";
let stagedUploadId = null;
let currentJobId = null;
let uploadXhr = null;

/** Shared avatar blobs keyed by `${userId}:1` (has custom avatar). */
const avatarBlobCache = new Map();
/** Shared KB icon blobs keyed by kb name. */
const kbIconBlobCache = new Map();

function revokeAllAvatarBlobs() {
  for (const url of avatarBlobCache.values()) {
    try {
      URL.revokeObjectURL(url);
    } catch {
      /* ignore */
    }
  }
  avatarBlobCache.clear();
  for (const url of kbIconBlobCache.values()) {
    try {
      URL.revokeObjectURL(url);
    } catch {
      /* ignore */
    }
  }
  kbIconBlobCache.clear();
}

function invalidateUserAvatarBlobs(userId) {
  const p = `${userId}:`;
  for (const k of [...avatarBlobCache.keys()]) {
    if (k.startsWith(p)) {
      const url = avatarBlobCache.get(k);
      if (url) URL.revokeObjectURL(url);
      avatarBlobCache.delete(k);
    }
  }
}

async function ensureAvatarOnImg(img, userId, hasAvatar) {
  if (!img) return;
  if (userId == null || Number.isNaN(Number(userId))) {
    delete img.dataset.blobUrl;
    img.src = DEFAULT_AVATAR_URL;
    return;
  }
  const uid = Number(userId);
  if (!hasAvatar) {
    delete img.dataset.blobUrl;
    img.src = DEFAULT_AVATAR_URL;
    return;
  }
  const key = `${uid}:1`;
  const hit = avatarBlobCache.get(key);
  if (hit) {
    img.dataset.blobUrl = hit;
    img.src = hit;
    return;
  }
  try {
    const res = await fetch(`/api/users/${encodeURIComponent(uid)}/avatar`, { headers: { ...authHeaders() } });
    if (res.status === 401) {
      setToken("");
      go("/login");
      return;
    }
    if (!res.ok) {
      delete img.dataset.blobUrl;
      img.src = DEFAULT_AVATAR_URL;
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    avatarBlobCache.set(key, url);
    img.dataset.blobUrl = url;
    img.src = url;
  } catch {
    delete img.dataset.blobUrl;
    img.src = DEFAULT_AVATAR_URL;
  }
}

const appEl = document.getElementById("app");
const statusEl = document.getElementById("status");
let toastHost = null;

const KB_UNLOCK = "\u{1F513}";
const KB_LOCK = "\u{1F512}";

const i18n = {
  en: {
    appTitle: "bce-cli Knowledge Console",
    loginTitle: "Sign in",
    registerTitle: "Create account",
    username: "Username",
    password: "Password",
    signIn: "Sign in",
    createAccount: "Create account",
    needAccount: "Need an account? Register",
    haveAccount: "Already have an account? Sign in",
    logout: "Log out",
    myKnowledgeBases: "My knowledge bases",
    subtitlePlaza: "Open a card to browse.",
    subtitleMyKb: "Knowledge bases I created or subscribed.",
    myCreatedSection: "Created by me",
    mySubscribedSection: "Subscribed by me",
    kbListSearch: "Search",
    kbListSearchPlaceholder: "Search by name or description",
    navSection: "Menu",
    navKbPlaza: "Knowledge plaza",
    navMyKb: "My knowledge bases",
    navAddKb: "Add knowledge base",
    backToPlaza: "Back to plaza",
    kbManageSubtitle: "Management — members, search, and settings",
    kbManageFixedSubtitle: "Manage my knowledge base",
    kbDetailFixedSubtitle: "Knowledge base details",
    subscribe: "Subscribe",
    subscribed: "Subscribed",
    unsubscribe: "Unsubscribe",
    goManage: "Manage this library",
    openSearchTools: "Search & tools",
    subscribeDone: "Subscription updated.",
    saveDone: "Saved.",
    memberAdded: "Member added.",
    memberRemoved: "Member removed.",
    visibilityUpdated: "Visibility updated.",
    kbDeleted: "Knowledge base deleted.",
    confirmTitle: "Please confirm",
    confirmGeneric: "Are you sure?",
    confirmDeleteMember: (u) => `Remove member ${u}?`,
    confirmAddMember: (u) => `Add member ${u}?`,
    confirmVisibilityChange: "Change visibility?",
    confirmLogout: "Log out now?",
    newKb: "Add knowledge base",
    addKbSubtitle: "Upload a tar archive, then name and describe your index.",
    indexName: "Name",
    indexDescription: "Description",
    indexReadme: "README",
    readmePreview: "Preview",
    readmeEdit: "Edit",
    archiveLabel: "Archive (.tar / .tar.gz / .tgz)",
    chooseFile: "Choose file",
    noFileChosen: "No file chosen",
    uploadProgress: "Upload",
    buildProgress: "Build",
    buildIdle: "Idle",
    startBuild: "Build",
    building: "Building…",
    open: "Open",
    back: "Back to list",
    search: "Search",
    searchPlaceholder: "Ask a question…",
    runSearch: "Run search",
    results: "Results",
    noResults: "No answer text yet — try a query.",
    addMember: "Add member",
    memberUser: "Username",
    canRead: "Read (search)",
    canWrite: "Can edit content & description",
    canDelete: "Delete library",
    kbVisibility: "Access",
    kbIcon: "Knowledge base image",
    uploadKbIcon: "Upload image",
    removeKbIcon: "Reset to default",
    iconUpdated: "Image updated.",
    kbLockOpenTitle: "Unlocked — any signed-in user can view",
    kbLockClosedTitle: "Locked — only the owner and members listed below",
    kbEveryoneCanView: "Any signed-in user can view this library.",
    kbLockedMembersBelow: "Only the owner and the members listed below can view this library.",
    visibleMembers: "Visible members",
    membersLoadError: "Could not load the member list.",
    removeMember: "Remove",
    saveDescription: "Save description",
    renameKb: "Rename knowledge base",
    newKbName: "New knowledge base name",
    saveName: "Save name",
    renameDone: "Knowledge base renamed.",
    deleteKb: "Delete library",
    confirmDeleteKb: (n) => `Delete knowledge base "${n}"? This removes registration and the SQLite file.`,
    refresh: "Refresh",
    ready: "Ready.",
    requireFields: "Name and description are required.",
    requireStaged: "Upload a tar archive first.",
    uploadStart: "Uploading…",
    uploadStaged: "Upload done. Fill name and description, then build.",
    uploadFailed: "Upload failed.",
    buildDone: (n) => `Built "${n}".`,
    phase_queued: "Queued",
    phase_extract: "Extract",
    phase_load: "Load",
    phase_chunk: "Chunk",
    phase_embed: "Embed",
    phase_sqlite: "SQLite",
    phase_register: "Register",
    phase_done: "Done",
    phase_error: "Error",
    legacyBadge: "Legacy (registry only)",
    sqliteMissing: "Index file missing",
    navAccount: "Account",
    interfaceLanguage: "Language",
    accountTitle: "Account settings",
    accountSubtitle: "Profile photo",
    apiKeysTitle: "API keys",
    apiKeyName: "Label (optional)",
    apiKeyCreate: "Create API key",
    apiKeyDelete: "Delete",
    apiKeyEyeShow: "Show",
    apiKeyEyeHide: "Hide",
    apiKeyEmpty: "No API keys yet.",
    apiKeyMaxHint: "Up to 5 keys. New keys start with sk-",
    apiKeyCreateDone: "API key created.",
    apiKeyDeleteDone: "API key deleted.",
    confirmDeleteApiKey: "Delete this API key?",
    avatarHint: "PNG, JPEG, GIF or WebP · max 2 MB",
    changeAvatarBtn: "Upload or change photo",
    removeAvatar: "Remove photo",
    changePasswordNav: "Change password",
    changePasswordTitle: "Change password",
    changePasswordSubtitle: "Re-login after save.",
    backToAccount: "← Back to account",
    passwordMismatch: "New passwords do not match",
    passwordChangedRelogin: "Password updated. Please sign in again.",
    avatarUpdated: "Avatar updated.",
    avatarRemoved: "Avatar removed.",
    currentPassword: "Current password",
    newPassword: "New password",
    confirmPassword: "Confirm new password",
    saveNewPassword: "Save",
  },
  zh: {
    appTitle: "bce-cli 知识库",
    loginTitle: "登录",
    registerTitle: "注册",
    username: "用户名",
    password: "密码",
    signIn: "登录",
    createAccount: "注册",
    needAccount: "没有账号？去注册",
    haveAccount: "已有账号？去登录",
    logout: "退出",
    myKnowledgeBases: "我的知识库",
    subtitlePlaza: "点击卡片浏览",
    subtitleMyKb: "我创建或订阅的知识库",
    myCreatedSection: "我创建的",
    mySubscribedSection: "我订阅的",
    kbListSearch: "搜索",
    kbListSearchPlaceholder: "按名称或描述搜索",
    navSection: "导航",
    navKbPlaza: "知识库广场",
    navMyKb: "我的知识库",
    navAddKb: "添加知识库",
    backToPlaza: "返回知识库广场",
    kbManageSubtitle: "管理 — 成员、检索与设置",
    kbManageFixedSubtitle: "管理我的知识库",
    kbDetailFixedSubtitle: "知识库详情",
    subscribe: "订阅",
    subscribed: "已订阅",
    unsubscribe: "取消订阅",
    goManage: "管理此知识库",
    openSearchTools: "检索与工具",
    subscribeDone: "订阅状态已更新。",
    saveDone: "已保存。",
    memberAdded: "成员已添加。",
    memberRemoved: "成员已移除。",
    visibilityUpdated: "可见性已更新。",
    kbDeleted: "知识库已删除。",
    confirmTitle: "请确认",
    confirmGeneric: "确认继续？",
    confirmDeleteMember: (u) => `确认移除成员 ${u}？`,
    confirmAddMember: (u) => `确认添加成员 ${u}？`,
    confirmVisibilityChange: "确认修改可见性？",
    confirmLogout: "确认退出登录？",
    newKb: "添加知识库",
    addKbSubtitle: "上传 tar 归档，填写名称与描述后构建索引。",
    indexName: "名称",
    indexDescription: "描述",
    indexReadme: "README",
    readmePreview: "预览",
    readmeEdit: "编辑",
    archiveLabel: "归档（.tar / .tar.gz / .tgz）",
    chooseFile: "选择文件",
    noFileChosen: "未选择文件",
    uploadProgress: "上传",
    buildProgress: "构建",
    buildIdle: "空闲",
    startBuild: "开始构建",
    building: "构建中…",
    open: "进入",
    back: "返回列表",
    search: "检索",
    searchPlaceholder: "输入问题…",
    runSearch: "检索",
    results: "结果",
    noResults: "暂无结果，请先提问。",
    addMember: "添加成员",
    memberUser: "用户名",
    canRead: "查（检索）",
    canWrite: "可编辑内容与描述",
    canDelete: "删（删除库）",
    kbVisibility: "访问范围",
    kbIcon: "知识库图片",
    uploadKbIcon: "上传图片",
    removeKbIcon: "恢复默认",
    iconUpdated: "图片已更新。",
    kbLockOpenTitle: "无锁 — 所有登录用户可查看",
    kbLockClosedTitle: "上锁 — 仅创建者与下方成员可查看",
    kbEveryoneCanView: "所有登录用户均可查看此知识库。",
    kbLockedMembersBelow: "仅创建者与下方列出的成员可查看。",
    visibleMembers: "可见成员",
    membersLoadError: "无法加载成员列表。",
    removeMember: "移除",
    saveDescription: "保存描述",
    renameKb: "重命名知识库",
    newKbName: "新的知识库名称",
    saveName: "保存名称",
    renameDone: "知识库已重命名。",
    deleteKb: "删除知识库",
    confirmDeleteKb: (n) => `确认删除知识库「${n}」？将注销注册并删除 SQLite 文件。`,
    refresh: "刷新",
    ready: "就绪。",
    requireFields: "请填写名称和描述。",
    requireStaged: "请先上传 tar 并完成上传。",
    uploadStart: "正在上传…",
    uploadStaged: "上传完成。填写名称与描述后构建。",
    uploadFailed: "上传失败。",
    buildDone: (n) => `已构建「${n}」。`,
    phase_queued: "排队",
    phase_extract: "解压",
    phase_load: "加载",
    phase_chunk: "分块",
    phase_embed: "向量化",
    phase_sqlite: "SQLite",
    phase_register: "注册",
    phase_done: "完成",
    phase_error: "错误",
    legacyBadge: "旧版（仅注册表）",
    sqliteMissing: "索引文件缺失",
    navAccount: "账户",
    interfaceLanguage: "界面语言",
    accountTitle: "账户设置",
    accountSubtitle: "头像",
    apiKeysTitle: "API Key 列表",
    apiKeyName: "备注（可选）",
    apiKeyCreate: "创建 API Key",
    apiKeyDelete: "删除",
    apiKeyEyeShow: "显示",
    apiKeyEyeHide: "隐藏",
    apiKeyEmpty: "暂无 API Key。",
    apiKeyMaxHint: "最多 5 条，新建前缀为 sk-",
    apiKeyCreateDone: "API Key 已创建。",
    apiKeyDeleteDone: "API Key 已删除。",
    confirmDeleteApiKey: "确认删除这条 API Key？",
    avatarHint: "支持 PNG / JPEG / GIF / WebP，最大 2 MB",
    changeAvatarBtn: "上传或更换头像",
    removeAvatar: "移除头像",
    changePasswordNav: "修改密码",
    changePasswordTitle: "修改密码",
    changePasswordSubtitle: "保存后重新登录",
    backToAccount: "← 返回账户",
    passwordMismatch: "两次输入的新密码不一致",
    passwordChangedRelogin: "密码已更新，请重新登录。",
    avatarUpdated: "头像已更新。",
    avatarRemoved: "已移除头像。",
    currentPassword: "当前密码",
    newPassword: "新密码",
    confirmPassword: "确认新密码",
    saveNewPassword: "保存",
  },
};

function T(key, ...args) {
  const v = i18n[currentLang][key];
  if (typeof v === "function") return v(...args);
  return v ?? key;
}

function setStatus(msg, isError = false) {
  const text = String(msg || "").trim();
  if (!text) return;
  if (!toastHost) {
    toastHost = document.createElement("div");
    toastHost.className = "toast-host";
    document.body.appendChild(toastHost);
  }
  const node = document.createElement("div");
  node.className = `toast${isError ? " is-error" : " is-success"}`;
  node.textContent = text;
  toastHost.appendChild(node);
  // Trigger CSS transition for fade/slide-in.
  requestAnimationFrame(() => node.classList.add("is-show"));
  const ttl = isError ? 3200 : 2400;
  const hide = () => {
    node.classList.remove("is-show");
    window.setTimeout(() => {
      node.remove();
      if (toastHost && toastHost.childElementCount === 0) {
        toastHost.remove();
        toastHost = null;
      }
    }, 260);
  };
  window.setTimeout(hide, ttl);
}

function clearStatus() {
  if (!statusEl) return;
  statusEl.textContent = "";
  statusEl.className = "global-status";
  statusEl.hidden = true;
}

function setAuthPageLayout(isAuth) {
  document.body.classList.toggle("auth-page", isAuth);
  appEl.classList.toggle("app-root--auth", isAuth);
  if (isAuth) {
    clearStatus();
    statusEl.hidden = true;
  } else {
    statusEl.hidden = true;
  }
}

function getToken() {
  return localStorage.getItem(AUTH_TOKEN_KEY) || "";
}

function setToken(t) {
  if (t) localStorage.setItem(AUTH_TOKEN_KEY, t);
  else localStorage.removeItem(AUTH_TOKEN_KEY);
}

function authHeaders() {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function fetchJSON(url, options = {}) {
  const headers = { ...(options.headers || {}), ...authHeaders() };
  const res = await fetch(url, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (
    res.status === 401 &&
    !url.includes("/auth/login") &&
    !url.includes("/auth/register")
  ) {
    setToken("");
    go("/login");
  }
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function go(path) {
  history.pushState({}, "", path);
  render();
}

window.addEventListener("popstate", () => render());

function parseRoute() {
  const p = location.pathname.replace(/\/+$/, "") || "/";
  if (p === "/login") return { type: "login" };
  if (p === "/register") return { type: "register" };
  if (p === "/add-kb") return { type: "addKb" };
  if (p === "/my-kb") return { type: "myKb" };
  if (p === "/profile") return { type: "profile" };
  if (p === "/change-password") return { type: "changePassword" };
  if (p.startsWith("/kb/")) {
    const segs = p
      .slice(4)
      .split("/")
      .filter(Boolean)
      .map((s) => decodeURIComponent(s));
    if (!segs.length) return { type: "home" };
    const name = segs[0];
    if (segs[1] === "manage") return { type: "kbManage", name };
    return { type: "kb", name };
  }
  if (p === "/" || p === "") return { type: "home" };
  return { type: "home" };
}

function phaseText(phase) {
  return T(`phase_${phase || "queued"}`) || String(phase || "");
}

async function pollJob(jobId) {
  currentJobId = jobId;
  saveState();
  const buildProgressEl = document.getElementById("build-progress");
  const buildProgressTextEl = document.getElementById("build-progress-text");
  while (true) {
    const job = await fetchJSON(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (buildProgressEl) buildProgressEl.value = Number(job.percent || 0);
    if (buildProgressTextEl) {
      buildProgressTextEl.textContent = `${job.percent || 0}% — ${phaseText(job.phase)}${job.detail ? ` · ${job.detail}` : ""}`;
    }
    saveState();
    if (job.status === "done") {
      currentJobId = null;
      saveState();
      return job;
    }
    if (job.status === "error") {
      currentJobId = null;
      saveState();
      throw new Error(job.error || "Build failed");
    }
    await new Promise((r) => setTimeout(r, 500));
  }
}

function saveState() {
  try {
    const payload = JSON.stringify({
      lang: currentLang,
      stagedUploadId,
      currentJobId,
    });
    localStorage.setItem(STATE_KEY, payload);
    localStorage.setItem(UI_LANG_KEY, currentLang);
  } catch {
    /* ignore */
  }
}

function loadState() {
  try {
    const raw = localStorage.getItem(STATE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function setLang(lang) {
  currentLang = lang === "zh" ? "zh" : "en";
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  saveState();
  render();
}

async function ensureSession() {
  if (!getToken()) return null;
  try {
    const me = await fetchJSON("/api/auth/me");
    return me.user || null;
  } catch {
    return null;
  }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function markdownToHtml(md) {
  const src = String(md || "").replace(/\r\n/g, "\n");
  const lines = src.split("\n");
  const out = [];
  let inCode = false;
  for (const raw of lines) {
    const line = raw;
    if (line.trim().startsWith("```")) {
      if (!inCode) {
        inCode = true;
        out.push("<pre><code>");
      } else {
        inCode = false;
        out.push("</code></pre>");
      }
      continue;
    }
    if (inCode) {
      out.push(`${esc(line)}\n`);
      continue;
    }
    if (/^###\s+/.test(line)) {
      out.push(`<h3>${esc(line.replace(/^###\s+/, ""))}</h3>`);
      continue;
    }
    if (/^##\s+/.test(line)) {
      out.push(`<h2>${esc(line.replace(/^##\s+/, ""))}</h2>`);
      continue;
    }
    if (/^#\s+/.test(line)) {
      out.push(`<h1>${esc(line.replace(/^#\s+/, ""))}</h1>`);
      continue;
    }
    if (/^-\s+/.test(line)) {
      out.push(`<li>${esc(line.replace(/^-+\s+/, ""))}</li>`);
      continue;
    }
    if (/^>\s+/.test(line)) {
      out.push(`<blockquote>${esc(line.replace(/^>\s+/, ""))}</blockquote>`);
      continue;
    }
    if (!line.trim()) {
      out.push("");
      continue;
    }
    let html = esc(line)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
    out.push(`<p>${html}</p>`);
  }
  let merged = out.join("\n");
  merged = merged.replace(/(<li>[\s\S]*?<\/li>\n?)+/g, (m) => `<ul>${m}</ul>`);
  if (inCode) merged += "</code></pre>";
  return merged;
}

function showConfirmDialog(message) {
  return new Promise((resolve) => {
    const mask = document.createElement("div");
    mask.className = "confirm-mask";
    mask.innerHTML = `
      <div class="confirm-card card" role="dialog" aria-modal="true">
        <h3>${esc(T("confirmTitle"))}</h3>
        <p>${esc(message || T("confirmGeneric"))}</p>
        <div class="confirm-actions">
          <button type="button" class="secondary" data-cancel="1">${currentLang === "zh" ? "取消" : "Cancel"}</button>
          <button type="button" data-ok="1">${currentLang === "zh" ? "确认" : "Confirm"}</button>
        </div>
      </div>
    `;
    const done = (ok) => {
      mask.remove();
      resolve(ok);
    };
    mask.addEventListener("click", (e) => {
      if (e.target === mask) done(false);
    });
    mask.querySelector("[data-cancel]")?.addEventListener("click", () => done(false));
    mask.querySelector("[data-ok]")?.addEventListener("click", () => done(true));
    document.body.appendChild(mask);
  });
}

function kbLockInlineHtml(isPublic) {
  if (isPublic) return "";
  const title = isPublic ? T("kbLockOpenTitle") : T("kbLockClosedTitle");
  const sym = isPublic ? KB_UNLOCK : KB_LOCK;
  return `<span class="kb-lock-inline${isPublic ? " is-unlocked" : ""}" title="${esc(title)}" aria-label="${esc(title)}">${sym}</span>`;
}

async function ensureKbIconOnImg(img, kbName) {
  if (!img) return;
  const key = String(kbName || "").trim();
  if (!key) {
    img.src = DEFAULT_KB_ICON_URL;
    return;
  }
  const hit = kbIconBlobCache.get(key);
  if (hit) {
    img.src = hit;
    return;
  }
  try {
    const res = await fetch(`/api/kb/${encodeURIComponent(key)}/icon`, { headers: { ...authHeaders() } });
    if (res.status === 401) {
      setToken("");
      go("/login");
      return;
    }
    if (!res.ok) {
      img.src = DEFAULT_KB_ICON_URL;
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    kbIconBlobCache.set(key, url);
    img.src = url;
  } catch {
    img.src = DEFAULT_KB_ICON_URL;
  }
}

function renderKbCardsInnerHtml(indexes, mode) {
  return indexes
    .map((item) => {
      const owner = item.owner || {};
      const oid = owner.id != null ? Number(owner.id) : NaN;
      const oidAttr = !Number.isNaN(oid) ? String(oid) : "";
      const oHas = !!owner.has_avatar;
      const oun = owner.username != null ? String(owner.username) : "";
      let cidx = Number(item.list_color_idx);
      if (Number.isNaN(cidx)) cidx = 0;
      cidx = Math.max(0, Math.min(4, cidx));
      const miss = !item.sqlite_exists ? T("sqliteMissing") : "";
      const pub = !!item.is_public;
      const showOwner = mode === "plaza" || mode === "my-subscribed";
      const showLock = true;
      return `
                <article class="kb-card kb-card--tone${cidx}" data-kb="${esc(item.name)}" data-card-mode="${esc(mode)}" data-owner-id="${esc(oidAttr)}">
                  <h3 class="kb-card-title-row"><img class="kb-card-kb-icon" alt="" width="20" height="20" src="${DEFAULT_KB_ICON_URL}" data-kb-name="${esc(item.name)}" /><span class="kb-card-name">${esc(item.name)}</span></h3>
                  <p class="desc">${esc(item.description || "")}</p>
                  ${
                    showOwner
                      ? `<div class="kb-card-owner">
                    <img class="kb-card-owner-avatar" alt="" width="22" height="22" loading="lazy" decoding="async" src="${DEFAULT_AVATAR_URL}" data-owner-id="${oidAttr === "" ? "" : esc(oidAttr)}" data-owner-has-avatar="${oHas ? "1" : "0"}" />
                    <span class="kb-card-owner-name">${esc(oun)}</span>
                  </div>`
                      : ""
                  }
                  ${showLock ? `<div class="kb-card-lock-corner">${kbLockInlineHtml(pub)}</div>` : ""}
                  ${miss ? `<p class="small muted kb-card-meta">${esc(miss)}</p>` : ""}
                </article>`;
    })
    .join("");
}

function bindKbGridNavigation(user) {
  appEl.querySelectorAll(".kb-card").forEach((card) => {
    card.addEventListener("click", () => {
      const name = card.getAttribute("data-kb");
      const mode = card.getAttribute("data-card-mode");
      if (!name) return;
      if (mode === "mine-created") {
        go(`/kb/${encodeURIComponent(name)}/manage`);
        return;
      }
      if (mode === "my-subscribed") {
        go(`/kb/${encodeURIComponent(name)}`);
        return;
      }
      const ownerIdRaw = card.getAttribute("data-owner-id");
      const oid = ownerIdRaw ? Number(ownerIdRaw) : NaN;
      const mine = !Number.isNaN(oid) && user?.id != null && oid === Number(user.id);
      go(mine ? `/kb/${encodeURIComponent(name)}/manage` : `/kb/${encodeURIComponent(name)}`);
    });
  });
}

function renderKbLockToggle(isPublic) {
  const openT = T("kbLockOpenTitle");
  const closedT = T("kbLockClosedTitle");
  const lockedActive = !isPublic;
  const openActive = isPublic;
  return `<div class="kb-lock-choice" id="kb-vis-toggle" role="radiogroup" aria-label="${esc(T("kbVisibility"))}">
    <button type="button" class="kb-lock-opt${lockedActive ? " is-active" : ""}" data-vis="private" aria-pressed="${lockedActive ? "true" : "false"}" title="${esc(closedT)}">${KB_LOCK}</button>
    <button type="button" class="kb-lock-opt${openActive ? " is-active" : ""}" data-vis="public" aria-pressed="${openActive ? "true" : "false"}" title="${esc(openT)}">${KB_UNLOCK}</button>
  </div>`;
}

function renderKbLockFieldNew() {
  const openT = T("kbLockOpenTitle");
  const closedT = T("kbLockClosedTitle");
  return `<label class="kb-lock-field">
    <span>${esc(T("kbVisibility"))}</span>
    <div class="kb-lock-choice" id="kb-vis-new" role="radiogroup" aria-label="${esc(T("kbVisibility"))}">
      <button type="button" class="kb-lock-opt is-active" data-vis="private" aria-pressed="true" title="${esc(closedT)}">${KB_LOCK}</button>
      <button type="button" class="kb-lock-opt" data-vis="public" aria-pressed="false" title="${esc(openT)}">${KB_UNLOCK}</button>
    </div>
    <input type="hidden" id="kb-visibility-new" value="private" />
  </label>`;
}

function wireKbLockChoiceNewForm() {
  const wrap = document.getElementById("kb-vis-new");
  const hidden = document.getElementById("kb-visibility-new");
  if (!wrap || !hidden) return;
  wrap.querySelectorAll(".kb-lock-opt").forEach((btn) => {
    btn.addEventListener("click", () => {
      const v = btn.getAttribute("data-vis");
      hidden.value = v || "private";
      wrap.querySelectorAll(".kb-lock-opt").forEach((b) => {
        const on = b.getAttribute("data-vis") === v;
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    });
  });
}

function renderShellSidebar(active) {
  const plazaCl = active === "plaza" ? " active" : "";
  const myCl = active === "my" ? " active" : "";
  const addCl = active === "add" ? " active" : "";
  const profCl = active === "profile" ? " active" : "";
  return `
    <aside class="sidebar" aria-label="${esc(T("navSection"))}">
      <div class="sidebar-title">${esc(T("navSection"))}</div>
      <button type="button" class="nav-item${plazaCl}" data-nav="plaza">${esc(T("navKbPlaza"))}</button>
      <button type="button" class="nav-item${myCl}" data-nav="my">${esc(T("navMyKb"))}</button>
      <button type="button" class="nav-item${addCl}" data-nav="add">${esc(T("navAddKb"))}</button>
      <button type="button" class="nav-item${profCl}" data-nav="profile">${esc(T("navAccount"))}</button>
      <div class="sidebar-spacer" aria-hidden="true"></div>
      <button type="button" class="nav-item nav-item--logout" id="sidebar-logout-btn">${esc(T("logout"))}</button>
    </aside>`;
}

function renderInterfaceLangSelect(selectId) {
  const enSel = currentLang === "en" ? " selected" : "";
  const zhSel = currentLang === "zh" ? " selected" : "";
  return `
    <label class="lang-select-wrap">
      <span class="lang-select-label">${esc(T("interfaceLanguage"))}</span>
      <select id="${esc(selectId)}" class="lang-select" aria-label="${esc(T("interfaceLanguage"))}">
        <option value="en"${enSel}>English</option>
        <option value="zh"${zhSel}>中文</option>
      </select>
    </label>`;
}

function bindInterfaceLangSelect(selectId) {
  const el = document.getElementById(selectId);
  el?.addEventListener("change", () => {
    if (el.value === "en" || el.value === "zh") setLang(el.value);
  });
}

function renderTopbarUser(user) {
  if (!user?.username) return "";
  return `
    <div class="topbar-user" title="${esc(user.username)}">
      <img id="topbar-avatar" class="topbar-avatar" src="${DEFAULT_AVATAR_URL}" alt="" width="36" height="36" />
      <span class="topbar-user-name">${esc(user.username)}</span>
    </div>`;
}

function renderTopbar(user, { title, subtitle }) {
  const sub = subtitle ? `<p class="muted small topbar-subtitle">${esc(subtitle)}</p>` : "";
  const userStrip = renderTopbarUser(user);
  return `
    <header class="topbar">
      <div class="topbar-lead">
        <div class="topbar-text">
          <h1>${esc(title)}</h1>
          ${sub}
        </div>
      </div>
      ${userStrip ? `<div class="header-actions">${userStrip}</div>` : ""}
    </header>`;
}

async function refreshTopbarAvatar(user) {
  const img = document.getElementById("topbar-avatar");
  if (!img || !user?.id) return;
  await ensureAvatarOnImg(img, user.id, !!user.has_avatar);
}

function bindShellChrome(user) {
  document.getElementById("sidebar-logout-btn")?.addEventListener("click", async () => {
    if (!(await showConfirmDialog(T("confirmLogout")))) return;
    try {
      await fetchJSON("/api/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    } catch {
      /* ignore */
    }
    revokeAllAvatarBlobs();
    setToken("");
    go("/login");
  });
  appEl.querySelectorAll("[data-nav]").forEach((el) => {
    el.addEventListener("click", () => {
      const n = el.getAttribute("data-nav");
      if (n === "plaza") go("/");
      if (n === "my") go("/my-kb");
      if (n === "add") go("/add-kb");
      if (n === "profile") go("/profile");
    });
  });
}

function bindUploadForm() {
  const form = document.getElementById("upload-form");
  if (!form) return;
  const pickBtn = document.getElementById("pick-archive-btn");
  const archiveInput = document.getElementById("kb-archive");
  const pickedFileNameEl = document.getElementById("picked-file-name");
  pickBtn?.addEventListener("click", () => archiveInput?.click());
  archiveInput?.addEventListener("change", () => {
    const f = archiveInput.files[0];
    pickedFileNameEl.textContent = f?.name || T("noFileChosen");
    if (f) startUpload(f);
    else {
      if (uploadXhr) uploadXhr.abort();
      stagedUploadId = null;
      const u = document.getElementById("upload-progress");
      const ut = document.getElementById("upload-progress-text");
      if (u) u.value = 0;
      if (ut) ut.textContent = "0%";
      saveState();
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("kb-name").value.trim();
    const description = document.getElementById("kb-description").value.trim();
    const readmeMd = document.getElementById("kb-readme").value.trim();
    if (!name || !description) return setStatus(T("requireFields"), true);
    if (!stagedUploadId) return setStatus(T("requireStaged"), true);
    const submitBtn = document.getElementById("submit-btn");
    submitBtn.disabled = true;
    submitBtn.textContent = T("building");
    try {
      const is_public = document.getElementById("kb-visibility-new")?.value === "public";
      const iconFile = document.getElementById("kb-icon-new-file")?.files?.[0] || null;
      const start = await fetchJSON("/api/indexes/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, description, readme_md: readmeMd, upload_id: stagedUploadId, is_public }),
      });
      const done = await pollJob(start.job_id);
      const builtName = done.result?.name || name;
      if (iconFile) {
        const fd = new FormData();
        fd.append("file", iconFile);
        await fetchJSON(`/api/kb/${encodeURIComponent(builtName)}/icon`, {
          method: "POST",
          body: fd,
        });
      }
      setStatus(T("buildDone", done.result?.name || name));
      stagedUploadId = null;
      archiveInput.value = "";
      pickedFileNameEl.textContent = T("noFileChosen");
      document.getElementById("upload-progress").value = 0;
      document.getElementById("upload-progress-text").textContent = "0%";
      document.getElementById("kb-name").value = "";
      document.getElementById("kb-description").value = "";
      document.getElementById("kb-readme").value = "";
      document.getElementById("build-progress").value = 0;
      document.getElementById("build-progress-text").textContent = T("buildIdle");
      saveState();
      go("/");
    } catch (err) {
      setStatus(err.message, true);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = T("startBuild");
    }
  });

  if (currentJobId) {
    pollJob(currentJobId)
      .then(() => render())
      .catch((err) => setStatus(err.message, true));
  }
}

async function renderLogin(register) {
  appEl.innerHTML = `
    <div class="auth-screen" style="position:relative">
      <div class="auth-title-block">
        <h1>${esc(T("appTitle"))}</h1>
        <p class="muted">${esc(register ? T("registerTitle") : T("loginTitle"))}</p>
      </div>
      <main class="auth-card card">
        <p id="auth-error" class="error small auth-error-line" hidden></p>
        <form id="auth-form">
          <label><span>${esc(T("username"))}</span><input id="auth-user" autocomplete="username" required /></label>
          <label><span>${esc(T("password"))}</span><input id="auth-pass" type="password" autocomplete="${register ? "new-password" : "current-password"}" required /></label>
          <button type="submit">${esc(register ? T("createAccount") : T("signIn"))}</button>
        </form>
        <p class="muted small auth-toggle-wrap"><a href="#" id="auth-toggle">${esc(register ? T("haveAccount") : T("needAccount"))}</a></p>
      </main>
    </div>
  `;
  const errEl = document.getElementById("auth-error");
  const flash = sessionStorage.getItem("bcecli.flash");
  if (flash) {
    sessionStorage.removeItem("bcecli.flash");
    errEl.textContent = flash;
    errEl.hidden = false;
    errEl.classList.add("auth-flash-success");
    errEl.classList.remove("error");
  }
  document.getElementById("auth-toggle").addEventListener("click", (e) => {
    e.preventDefault();
    go(register ? "/login" : "/register");
  });
  document.getElementById("auth-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    errEl.hidden = true;
    errEl.textContent = "";
    errEl.classList.remove("auth-flash-success");
    errEl.classList.add("error");
    const username = document.getElementById("auth-user").value.trim();
    const password = document.getElementById("auth-pass").value;
    const path = register ? "/api/auth/register" : "/api/auth/login";
    try {
      const data = await fetchJSON(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      setToken(data.token);
      const returnTo = sessionStorage.getItem("bcecli.returnTo") || "/";
      sessionStorage.removeItem("bcecli.returnTo");
      history.replaceState({}, "", returnTo);
      render();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.hidden = false;
    }
  });
}

async function renderPlaza(user) {
  let indexes = [];
  try {
    const data = await fetchJSON("/api/indexes");
    indexes = data.indexes || [];
  } catch (e) {
    setStatus(e.message, true);
  }

  const renderPlazaGrid = (query) => {
    const q = String(query || "").trim().toLowerCase();
    const rows = !q
      ? indexes
      : indexes.filter((item) => {
          const n = String(item.name || "").toLowerCase();
          const d = String(item.description || "").toLowerCase();
          return n.includes(q) || d.includes(q);
        });
    return rows.length === 0
      ? `<p class="muted empty-hint">${currentLang === "zh" ? "没有匹配结果" : "No matches found."}</p>`
      : renderKbCardsInnerHtml(rows, "plaza");
  };

  appEl.innerHTML = `
    <div class="app-shell">
      ${renderShellSidebar("plaza")}
      <div class="shell-main">
        <div class="shell-content">
          ${renderTopbar(user, { title: T("navKbPlaza"), subtitle: T("subtitlePlaza") })}
          <div class="shell-body">
            <label class="kb-list-search">
              <span>${esc(T("kbListSearch"))}</span>
              <input id="plaza-search-input" placeholder="${esc(T("kbListSearchPlaceholder"))}" />
            </label>
            <div class="kb-grid" id="kb-grid">
              ${indexes.length === 0 ? `<p class="muted empty-hint">${currentLang === "zh" ? "暂无知识库，可在左侧添加" : "No knowledge bases yet. Use the sidebar to add one."}</p>` : renderPlazaGrid("")}
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  bindShellChrome(user);
  void refreshTopbarAvatar(user);
  void hydrateKbCardOwnerAvatars();
  void hydrateKbCardIcons();
  bindKbGridNavigation(user);
  document.getElementById("plaza-search-input")?.addEventListener("input", async (e) => {
    const val = e.target.value;
    const grid = document.getElementById("kb-grid");
    if (!grid) return;
    grid.innerHTML = renderPlazaGrid(val);
    await hydrateKbCardOwnerAvatars();
    await hydrateKbCardIcons();
    bindKbGridNavigation(user);
  });
}

async function renderMyKb(user) {
  let indexes = [];
  let subscribed = [];
  try {
    const data = await fetchJSON("/api/indexes");
    indexes = data.indexes || [];
    const sub = await fetchJSON("/api/user/subscriptions");
    subscribed = sub.indexes || [];
  } catch (e) {
    setStatus(e.message, true);
  }

  const created = indexes.filter((item) => {
    const oid = item.owner?.id != null ? Number(item.owner.id) : NaN;
    return user?.id != null && !Number.isNaN(oid) && oid === Number(user.id);
  });
  subscribed = subscribed.filter((item) => {
    const oid = item.owner?.id != null ? Number(item.owner.id) : NaN;
    return user?.id != null && !Number.isNaN(oid) && oid !== Number(user.id);
  });

  const renderCreatedGrid = (query) => {
    const q = String(query || "").trim().toLowerCase();
    const rows = !q
      ? created
      : created.filter((item) => {
          const n = String(item.name || "").toLowerCase();
          const d = String(item.description || "").toLowerCase();
          return n.includes(q) || d.includes(q);
        });
    return rows.length === 0
      ? `<p class="muted empty-hint">${currentLang === "zh" ? "没有匹配结果" : "No matches found."}</p>`
      : renderKbCardsInnerHtml(rows, "mine-created");
  };
  const renderSubscribedGrid = (query) => {
    const q = String(query || "").trim().toLowerCase();
    const rows = !q
      ? subscribed
      : subscribed.filter((item) => {
          const n = String(item.name || "").toLowerCase();
          const d = String(item.description || "").toLowerCase();
          const o = String(item.owner?.username || "").toLowerCase();
          return n.includes(q) || d.includes(q) || o.includes(q);
        });
    return rows.length === 0
      ? `<p class="muted empty-hint">${currentLang === "zh" ? "没有匹配结果" : "No matches found."}</p>`
      : renderKbCardsInnerHtml(rows, "my-subscribed");
  };

  appEl.innerHTML = `
    <div class="app-shell">
      ${renderShellSidebar("my")}
      <div class="shell-main">
        <div class="shell-content">
          ${renderTopbar(user, { title: T("myKnowledgeBases"), subtitle: T("subtitleMyKb") })}
          <div class="shell-body">
            <h2 class="section-title">${esc(T("myCreatedSection"))}</h2>
            <label class="kb-list-search">
              <span>${esc(T("kbListSearch"))}</span>
              <input id="my-created-search-input" placeholder="${esc(T("kbListSearchPlaceholder"))}" />
            </label>
            <div class="kb-grid" id="kb-grid-created">
              ${created.length === 0 ? `<p class="muted empty-hint">${currentLang === "zh" ? "你还没有创建知识库。": "No libraries created by you yet."}</p>` : renderCreatedGrid("")}
            </div>
            <hr class="hr-soft" />
            <h2 class="section-title">${esc(T("mySubscribedSection"))}</h2>
            <label class="kb-list-search">
              <span>${esc(T("kbListSearch"))}</span>
              <input id="my-subscribed-search-input" placeholder="${esc(T("kbListSearchPlaceholder"))}" />
            </label>
            <div class="kb-grid" id="kb-grid-subscribed">
              ${subscribed.length === 0 ? `<p class="muted empty-hint">${currentLang === "zh" ? "你还没有订阅知识库。": "No subscribed libraries yet."}</p>` : renderSubscribedGrid("")}
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  bindShellChrome(user);
  void refreshTopbarAvatar(user);
  void hydrateKbCardOwnerAvatars();
  void hydrateKbCardIcons();
  bindKbGridNavigation(user);
  document.getElementById("my-created-search-input")?.addEventListener("input", (e) => {
    const grid = document.getElementById("kb-grid-created");
    if (!grid) return;
    grid.innerHTML = renderCreatedGrid(e.target.value);
    void hydrateKbCardIcons();
    bindKbGridNavigation(user);
  });
  document.getElementById("my-subscribed-search-input")?.addEventListener("input", async (e) => {
    const grid = document.getElementById("kb-grid-subscribed");
    if (!grid) return;
    grid.innerHTML = renderSubscribedGrid(e.target.value);
    await hydrateKbCardOwnerAvatars();
    await hydrateKbCardIcons();
    bindKbGridNavigation(user);
  });
}

async function renderAddKb(user) {
  appEl.innerHTML = `
    <div class="app-shell">
      ${renderShellSidebar("add")}
      <div class="shell-main">
        <div class="shell-content">
          ${renderTopbar(user, { title: T("newKb"), subtitle: T("addKbSubtitle") })}
          <div class="shell-body add-kb-page">
            <section class="card">
              <form id="upload-form">
                <label><span>${esc(T("indexName"))}</span><input id="kb-name" required /></label>
                <label><span>${esc(T("indexDescription"))}</span><textarea id="kb-description" rows="3" required></textarea></label>
                <label>
                  <span>${esc(T("indexReadme"))}</span>
                  <div class="md-toggle-row">
                    <button type="button" class="secondary small" id="readme-add-edit-tab">${esc(T("readmeEdit"))}</button>
                    <button type="button" class="secondary small" id="readme-add-preview-tab">${esc(T("readmePreview"))}</button>
                  </div>
                  <textarea id="kb-readme" rows="8"></textarea>
                  <div id="kb-readme-add-preview" class="md-preview" style="display:none"></div>
                </label>
                <label>
                  <span>${esc(T("kbIcon"))}</span>
                  <input type="file" id="kb-icon-new-file" class="hidden-file-input" accept="image/png,image/jpeg,image/gif,image/webp,.png,.jpg,.jpeg,.gif,.webp" />
                  <div class="kb-icon-manage-row">
                    <img id="kb-icon-add-preview" class="kb-manage-icon-preview" src="${DEFAULT_KB_ICON_URL}" alt="" />
                  </div>
                  <div class="file-picker-row">
                    <button type="button" class="secondary" id="pick-kb-icon-btn">${esc(T("uploadKbIcon"))}</button>
                  </div>
                </label>
                ${renderKbLockFieldNew()}
                <label>
                  <span>${esc(T("archiveLabel"))}</span>
                  <input type="file" id="kb-archive" class="hidden-file-input" accept=".tar,.tgz,.tar.gz,.tar.bz2,.tar.xz,application/x-tar" />
                  <div class="file-picker-row">
                    <button type="button" class="secondary" id="pick-archive-btn">${esc(T("chooseFile"))}</button>
                    <span id="picked-file-name">${esc(T("noFileChosen"))}</span>
                  </div>
                </label>
                <div class="progress-group">
                  <label class="progress-label"><span>${esc(T("uploadProgress"))}</span></label>
                  <progress id="upload-progress" value="0" max="100"></progress>
                  <span id="upload-progress-text">0%</span>
                </div>
                <div class="progress-group">
                  <label class="progress-label"><span>${esc(T("buildProgress"))}</span></label>
                  <progress id="build-progress" value="0" max="100"></progress>
                  <span id="build-progress-text">${esc(T("buildIdle"))}</span>
                </div>
                <div class="form-actions">
                  <button type="button" class="secondary" id="cancel-add-kb">${esc(T("back"))}</button>
                  <button type="submit" id="submit-btn">${esc(T("startBuild"))}</button>
                </div>
              </form>
            </section>
          </div>
        </div>
      </div>
    </div>
  `;

  bindShellChrome(user);
  void refreshTopbarAvatar(user);
  document.getElementById("cancel-add-kb")?.addEventListener("click", () => go("/"));
  document.getElementById("pick-kb-icon-btn")?.addEventListener("click", () => {
    document.getElementById("kb-icon-new-file")?.click();
  });
  document.getElementById("readme-add-edit-tab")?.addEventListener("click", () => {
    document.getElementById("kb-readme").style.display = "";
    document.getElementById("kb-readme-add-preview").style.display = "none";
  });
  document.getElementById("readme-add-preview-tab")?.addEventListener("click", () => {
    const text = document.getElementById("kb-readme")?.value || "";
    const p = document.getElementById("kb-readme-add-preview");
    p.innerHTML = text ? markdownToHtml(text) : "";
    p.style.display = "";
    document.getElementById("kb-readme").style.display = "none";
  });
  document.getElementById("kb-icon-new-file")?.addEventListener("change", (e) => {
    const f = e.target?.files?.[0] || null;
    const img = document.getElementById("kb-icon-add-preview");
    if (!img) return;
    const prev = img.dataset.previewUrl;
    if (prev) {
      try {
        URL.revokeObjectURL(prev);
      } catch {
        /* ignore */
      }
      delete img.dataset.previewUrl;
    }
    if (!f) {
      img.src = DEFAULT_KB_ICON_URL;
      return;
    }
    const url = URL.createObjectURL(f);
    img.dataset.previewUrl = url;
    img.src = url;
  });
  wireKbLockChoiceNewForm();
  bindUploadForm();
}

async function hydrateKbCardOwnerAvatars() {
  const nodes = appEl.querySelectorAll("img.kb-card-owner-avatar[data-owner-id]");
  const tasks = [];
  nodes.forEach((img) => {
    const idRaw = img.getAttribute("data-owner-id");
    if (!idRaw) return;
    const uid = Number(idRaw);
    if (Number.isNaN(uid)) return;
    const has = img.getAttribute("data-owner-has-avatar") === "1";
    tasks.push(ensureAvatarOnImg(img, uid, has));
  });
  await Promise.all(tasks);
}

async function hydrateKbCardIcons() {
  const nodes = appEl.querySelectorAll("img.kb-card-kb-icon[data-kb-name]");
  const tasks = [];
  nodes.forEach((img) => {
    const kbName = img.getAttribute("data-kb-name");
    if (!kbName) return;
    tasks.push(ensureKbIconOnImg(img, kbName));
  });
  await Promise.all(tasks);
}

async function hydrateProfileAvatar(user) {
  const img = document.getElementById("profile-avatar-preview");
  if (!img) return;
  const preview = img.dataset.previewUrl;
  if (preview) {
    URL.revokeObjectURL(preview);
    delete img.dataset.previewUrl;
  }
  await ensureAvatarOnImg(img, user?.id, !!user?.has_avatar);
}

function maskApiKey(k) {
  const s = String(k || "");
  if (!s) return "";
  if (s.length <= 8) return "********";
  return `${s.slice(0, 3)}${"*".repeat(Math.max(6, s.length - 7))}${s.slice(-4)}`;
}

function renderApiKeyList(keys) {
  if (!keys?.length) return `<p class="muted small">${esc(T("apiKeyEmpty"))}</p>`;
  return `<ul class="api-key-list">
    ${keys
      .map(
        (k) => `<li class="api-key-row" data-key-id="${esc(String(k.id))}">
          <div class="api-key-main">
            <p class="api-key-label">${esc(k.name || "—")}</p>
            <code class="api-key-value" data-full="${esc(k.key)}" data-visible="0">${esc(maskApiKey(k.key))}</code>
          </div>
          <div class="api-key-actions">
            <button type="button" class="secondary small api-key-eye">${esc(T("apiKeyEyeShow"))}</button>
            <button type="button" class="secondary small api-key-del">${esc(T("apiKeyDelete"))}</button>
          </div>
        </li>`,
      )
      .join("")}
  </ul>`;
}

async function renderProfile(user) {
  let apiKeys = [];
  try {
    const data = await fetchJSON("/api/user/api-keys");
    apiKeys = data.keys || [];
  } catch (e) {
    setStatus(e.message, true);
  }
  appEl.innerHTML = `
    <div class="app-shell">
      ${renderShellSidebar("profile")}
      <div class="shell-main">
        <div class="shell-content">
          ${renderTopbar(user, { title: T("accountTitle"), subtitle: T("accountSubtitle") })}
          <div class="shell-body profile-panel">
            <section class="card">
              <h2 class="profile-section-title">${esc(T("navAccount"))}</h2>
              <div class="profile-avatar-row">
                <img id="profile-avatar-preview" class="profile-avatar-preview" src="${DEFAULT_AVATAR_URL}" alt="" />
                <div class="profile-avatar-actions">
                  <p class="profile-username-line">${esc(user?.username || "")}</p>
                  <p class="muted small">${esc(T("avatarHint"))}</p>
                  <input type="file" id="profile-avatar-file" class="hidden-file-input" accept="image/png,image/jpeg,image/gif,image/webp,.png,.jpg,.jpeg,.gif,.webp" />
                  <button type="button" id="profile-avatar-upload-btn" style="margin-top:10px">${esc(T("changeAvatarBtn"))}</button>
                  <button type="button" class="secondary" id="profile-change-password" style="margin-top:12px;display:block">${esc(T("changePasswordNav"))}</button>
                  <p id="profile-msg" class="small muted" style="margin-top:8px"></p>
                </div>
              </div>
              <hr class="hr-soft" />
              ${renderInterfaceLangSelect("profile-lang-select")}
              <hr class="hr-soft" />
              <h2 class="profile-section-title">${esc(T("apiKeysTitle"))}</h2>
              <p class="muted small">${esc(T("apiKeyMaxHint"))}</p>
              <div id="api-key-list-wrap">${renderApiKeyList(apiKeys)}</div>
              <div class="api-key-create">
                <label><span>${esc(T("apiKeyName"))}</span><input id="api-key-name" /></label>
                <button type="button" id="api-key-create-btn">${esc(T("apiKeyCreate"))}</button>
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  `;

  bindShellChrome(user);
  void refreshTopbarAvatar(user);
  await hydrateProfileAvatar(user);
  bindInterfaceLangSelect("profile-lang-select");

  const fileInput = document.getElementById("profile-avatar-file");
  const msg = document.getElementById("profile-msg");
  const uploadBtn = document.getElementById("profile-avatar-upload-btn");

  document.getElementById("profile-change-password")?.addEventListener("click", () => go("/change-password"));

  uploadBtn?.addEventListener("click", () => fileInput?.click());

  fileInput?.addEventListener("change", async () => {
    const f = fileInput.files[0];
    msg.textContent = "";
    if (!f) return;
    const prevImg = document.getElementById("profile-avatar-preview");
    const prev = prevImg?.dataset.previewUrl;
    if (prev) URL.revokeObjectURL(prev);
    const url = URL.createObjectURL(f);
    prevImg.dataset.previewUrl = url;
    prevImg.src = url;

    const fd = new FormData();
    fd.append("file", f);
    uploadBtn.disabled = true;
    try {
      const res = await fetch("/api/user/avatar", { method: "POST", headers: { ...authHeaders() }, body: fd });
  const data = await res.json().catch(() => ({}));
      if (res.status === 401) {
        setToken("");
        go("/login");
        return;
      }
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
      fileInput.value = "";
      if (user?.id != null) invalidateUserAvatarBlobs(user.id);
      setStatus(T("avatarUpdated"));
      await render();
    } catch (e) {
      msg.textContent = e.message;
    } finally {
      uploadBtn.disabled = false;
    }
  });

  document.getElementById("api-key-create-btn")?.addEventListener("click", async () => {
    const name = document.getElementById("api-key-name")?.value?.trim() || "";
    try {
      await fetchJSON("/api/user/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      setStatus(T("apiKeyCreateDone"));
      await renderProfile(user);
    } catch (e) {
      setStatus(e.message, true);
    }
  });

  document.getElementById("api-key-list-wrap")?.addEventListener("click", async (e) => {
    const row = e.target.closest(".api-key-row");
    if (!row) return;
    const codeEl = row.querySelector(".api-key-value");
    if (e.target.closest(".api-key-eye")) {
      const vis = codeEl.getAttribute("data-visible") === "1";
      const full = codeEl.getAttribute("data-full") || "";
      codeEl.setAttribute("data-visible", vis ? "0" : "1");
      codeEl.textContent = vis ? maskApiKey(full) : full;
      const eyeBtn = row.querySelector(".api-key-eye");
      if (eyeBtn) eyeBtn.textContent = vis ? T("apiKeyEyeShow") : T("apiKeyEyeHide");
    return;
  }
    if (e.target.closest(".api-key-del")) {
      if (!(await showConfirmDialog(T("confirmDeleteApiKey")))) return;
      const id = row.getAttribute("data-key-id");
      try {
        await fetchJSON(`/api/user/api-keys/${encodeURIComponent(id)}`, { method: "DELETE" });
        setStatus(T("apiKeyDeleteDone"));
        await renderProfile(user);
      } catch (err) {
        setStatus(err.message, true);
      }
    }
  });
}

async function renderChangePassword(user) {
  appEl.innerHTML = `
    <div class="auth-screen" style="position:relative">
      <a href="#" class="auth-back-top link-button" id="cp-back">${esc(T("backToAccount"))}</a>
      <div class="auth-title-block">
        <h1>${esc(T("changePasswordTitle"))}</h1>
        <p class="muted small">${esc(T("changePasswordSubtitle"))}</p>
      </div>
      <main class="auth-card card">
        <p id="cp-error" class="error small auth-error-line" hidden></p>
        <form id="cp-form">
          <label><span>${esc(T("currentPassword"))}</span><input type="password" id="cp-cur" autocomplete="current-password" required /></label>
          <label><span>${esc(T("newPassword"))}</span><input type="password" id="cp-new" autocomplete="new-password" required minlength="8" /></label>
          <label><span>${esc(T("confirmPassword"))}</span><input type="password" id="cp-new2" autocomplete="new-password" required minlength="8" /></label>
          <button type="submit">${esc(T("saveNewPassword"))}</button>
        </form>
        <div class="auth-card-footer-lang">
          ${renderInterfaceLangSelect("cp-lang-select")}
        </div>
      </main>
    </div>
  `;
  bindInterfaceLangSelect("cp-lang-select");
  document.getElementById("cp-back").addEventListener("click", (e) => {
    e.preventDefault();
    go("/profile");
  });
  document.getElementById("cp-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("cp-error");
    errEl.hidden = true;
    errEl.textContent = "";
    const cur = document.getElementById("cp-cur").value;
    const nw = document.getElementById("cp-new").value;
    const nw2 = document.getElementById("cp-new2").value;
    if (nw !== nw2) {
      errEl.textContent = T("passwordMismatch");
      errEl.hidden = false;
      return;
    }
    try {
      await fetchJSON("/api/user/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: cur, new_password: nw }),
      });
      try {
        await fetch("/api/auth/logout", { method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() }, body: "{}" });
      } catch {
        /* ignore */
      }
      setToken("");
      revokeAllAvatarBlobs();
      sessionStorage.setItem("bcecli.flash", T("passwordChangedRelogin"));
      history.replaceState({}, "", "/login");
      render();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.hidden = false;
    }
  });
}

function startUpload(file) {
  if (uploadXhr) uploadXhr.abort();
  stagedUploadId = null;
  const uploadProgressEl = document.getElementById("upload-progress");
  const uploadProgressTextEl = document.getElementById("upload-progress-text");
  uploadProgressEl.value = 0;
  uploadProgressTextEl.textContent = "0%";
  setStatus(T("uploadStart"));
  saveState();

  const fd = new FormData();
  fd.append("file", file);
  const xhr = new XMLHttpRequest();
  uploadXhr = xhr;
  xhr.open("POST", "/api/upload");
  xhr.setRequestHeader("Authorization", authHeaders().Authorization || "");
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
      setStatus(T("uploadFailed"), true);
      return;
    }
    if (xhr.status >= 200 && xhr.status < 300 && data.ok && data.upload_id) {
      stagedUploadId = data.upload_id;
      uploadProgressEl.value = 100;
      uploadProgressTextEl.textContent = "100%";
      setStatus(T("uploadStaged"));
      saveState();
      return;
    }
    if (xhr.status === 401) {
      setToken("");
      go("/login");
      return;
    }
    setStatus(data.error || T("uploadFailed"), true);
  };
  xhr.onerror = () => {
    uploadXhr = null;
    setStatus(T("uploadFailed"), true);
  };
  xhr.send(fd);
}

function renderKbDetailTopbar(user, { name, subtitle = "" }) {
  return `
    <header class="topbar topbar--detail">
      <div class="topbar-lead">
        <div class="topbar-text">
          <h1>${esc(name)}</h1>
          ${subtitle ? `<p class="muted small topbar-subtitle">${esc(subtitle)}</p>` : ""}
        </div>
      </div>
      <div class="header-actions">
        ${renderTopbarUser(user)}
      </div>
    </header>`;
}

async function renderKbPublicDetail(user, name) {
  let meta = null;
  try {
    meta = await fetchJSON(`/api/kb/${encodeURIComponent(name)}`);
  } catch (e) {
    setStatus(e.message, true);
  }
  if (!meta?.ok) {
    appEl.innerHTML = `
      <div class="app-shell">
        ${renderShellSidebar(null)}
        <div class="shell-main">
          <div class="shell-content">
            ${renderTopbar(user, { title: T("navKbPlaza"), subtitle: "" })}
            <div class="shell-body">
              <div class="narrow-card card">
                <p class="error">${currentLang === "zh" ? "无法加载知识库" : "Knowledge base not found."}</p>
                <button type="button" class="secondary" id="back-miss">${esc(T("backToPlaza"))}</button>
              </div>
            </div>
          </div>
        </div>
      </div>`;
    bindShellChrome(user);
    void refreshTopbarAvatar(user);
    document.getElementById("back-miss").addEventListener("click", () => go("/"));
    return;
  }

  const legacy = !!meta.legacy_registry_only;
  const isPub = !!meta.is_public;
  const isOwner = !!meta.permission?.is_owner;
  const subscribed = !!meta.subscribed;

  const canSubscribe = !legacy && !isOwner;
  const subBtn = canSubscribe
    ? subscribed
      ? `<button type="button" class="secondary" id="kb-unsub-btn">${esc(T("unsubscribe"))}</button>`
      : `<button type="button" class="secondary" id="kb-sub-btn">${esc(T("subscribe"))}</button>`
    : "";
  const actions = [subBtn].filter(Boolean).join("");

  appEl.innerHTML = `
    <div class="app-shell">
      ${renderShellSidebar(null)}
      <div class="shell-main">
        <div class="shell-content">
          ${renderKbDetailTopbar(user, { name, subtitle: T("kbDetailFixedSubtitle") })}
          <div class="shell-body profile-panel kb-detail-shell">
            <section class="card kb-detail-card">
              <div class="kb-detail-block">
                <h2 class="kb-detail-block-title">${esc(T("indexDescription"))}</h2>
                <p class="kb-detail-desc-text${meta.description ? "" : " muted"}">${meta.description ? esc(meta.description) : currentLang === "zh" ? "暂无描述" : "No description."}</p>
              </div>
              <hr class="hr-soft hr-soft--kb-detail" />
              ${
                meta.readme_md
                  ? `<div class="kb-detail-block">
                      <h2 class="kb-detail-block-title">${esc(T("indexReadme"))}</h2>
                      <div class="md-content">${markdownToHtml(meta.readme_md)}</div>
                    </div>
                    <hr class="hr-soft hr-soft--kb-detail" />`
                  : ""
              }
              <div class="kb-detail-block">
                <h2 class="kb-detail-block-title">${esc(T("kbVisibility"))}</h2>
                <p class="muted small vis-hint-inline">
                  <span class="kb-lock-ico" title="${esc(isPub ? T("kbLockOpenTitle") : T("kbLockClosedTitle"))}">${isPub ? KB_UNLOCK : KB_LOCK}</span>
                  ${esc(isPub ? T("kbEveryoneCanView") : T("kbLockedMembersBelow"))}
                </p>
              </div>
              <hr class="hr-soft hr-soft--kb-detail" />
              <div class="kb-detail-block kb-detail-actions">
                ${actions || `<p class="muted small">—</p>`}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  `;

  bindShellChrome(user);
  void refreshTopbarAvatar(user);
  document.getElementById("kb-sub-btn")?.addEventListener("click", async () => {
    try {
      await fetchJSON(`/api/kb/${encodeURIComponent(name)}/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      setStatus(T("subscribeDone"));
      await render();
    } catch (e) {
      setStatus(e.message, true);
    }
  });
  document.getElementById("kb-unsub-btn")?.addEventListener("click", async () => {
    try {
      await fetchJSON(`/api/kb/${encodeURIComponent(name)}/subscribe`, { method: "DELETE" });
      setStatus(T("subscribeDone"));
      await render();
    } catch (e) {
      setStatus(e.message, true);
    }
  });
}

async function renderKbManage(user, name) {
  let meta = null;
  try {
    meta = await fetchJSON(`/api/kb/${encodeURIComponent(name)}`);
  } catch (e) {
    setStatus(e.message, true);
  }
  if (!meta?.ok) {
    appEl.innerHTML = `
      <div class="app-shell">
        ${renderShellSidebar(null)}
        <div class="shell-main">
          <div class="shell-content">
            ${renderTopbar(user, { title: T("navKbPlaza"), subtitle: "" })}
            <div class="shell-body">
              <div class="narrow-card card">
                <p class="error">${currentLang === "zh" ? "无法加载知识库" : "Knowledge base not found."}</p>
                <button type="button" class="secondary" id="back-miss">${esc(T("backToPlaza"))}</button>
              </div>
            </div>
          </div>
        </div>
      </div>`;
    bindShellChrome(user);
    void refreshTopbarAvatar(user);
    document.getElementById("back-miss").addEventListener("click", () => go("/"));
    return;
  }

  const perm = meta.permission || {};
  const isOwner = !!perm.is_owner;
  const canWrite = !!perm.can_write;
  const canDelete = !!perm.can_delete;
  const legacy = !!meta?.legacy_registry_only;
  const isPub = !!meta?.is_public;

  let members = null;
  if (!legacy && !isPub) {
    try {
      members = await fetchJSON(`/api/kb/${encodeURIComponent(name)}/members`);
    } catch {
      members = null;
    }
  }

  const nameSection =
    isOwner && !legacy
      ? `<div class="kb-detail-block">
          <h2 class="kb-detail-block-title">${esc(T("renameKb"))}</h2>
          <label><span>${esc(T("newKbName"))}</span><input id="kb-name-edit" value="${esc(name)}" /></label>
          <button type="button" id="save-name-btn">${esc(T("saveName"))}</button>
        </div>`
      : "";

  const visibilitySection = !legacy
    ? isOwner
      ? `<div class="kb-detail-block">
          <h2 class="kb-detail-block-title">${esc(T("kbVisibility"))}</h2>
          ${renderKbLockToggle(isPub)}
          ${isPub ? `<p class="muted small kb-vis-note">${esc(T("kbEveryoneCanView"))}</p>` : ""}
        </div>`
      : `<div class="kb-detail-block">
          <p class="muted small vis-hint-inline">
            <span class="kb-lock-ico" title="${esc(isPub ? T("kbLockOpenTitle") : T("kbLockClosedTitle"))}">${isPub ? KB_UNLOCK : KB_LOCK}</span>
            ${esc(isPub ? T("kbEveryoneCanView") : T("kbLockedMembersBelow"))}
          </p>
        </div>`
    : "";
  const iconSection = !legacy && isOwner
    ? `<div class="kb-detail-block">
          <h2 class="kb-detail-block-title">${esc(T("kbIcon"))}</h2>
          <div class="kb-icon-manage-row">
            <img id="kb-icon-edit-preview" class="kb-manage-icon-preview" src="${DEFAULT_KB_ICON_URL}" alt="" />
            <div class="kb-icon-manage-actions">
              <input type="file" id="kb-icon-edit-file" class="hidden-file-input" accept="image/png,image/jpeg,image/gif,image/webp,.png,.jpg,.jpeg,.gif,.webp" />
              <button type="button" id="pick-icon-btn">${esc(T("uploadKbIcon"))}</button>
              <button type="button" class="secondary" id="remove-icon-btn">${esc(T("removeKbIcon"))}</button>
            </div>
          </div>
        </div>`
    : "";
  const descSection =
    canWrite && !legacy
      ? `<div class="kb-detail-block">
          <h2 class="kb-detail-block-title">${esc(T("indexDescription"))}</h2>
          <textarea id="kb-desc-edit" class="kb-detail-textarea" rows="3" aria-label="${esc(T("indexDescription"))}">${esc(meta?.description || "")}</textarea>
          <button type="button" id="save-desc-btn">${esc(T("saveDescription"))}</button>
        </div>`
      : `<div class="kb-detail-block">
          <h2 class="kb-detail-block-title">${esc(T("indexDescription"))}</h2>
          <p class="kb-detail-desc-text${meta?.description ? "" : " muted"}">${meta?.description ? esc(meta.description) : currentLang === "zh" ? "暂无描述" : "No description."}</p>
        </div>`;
  const readmeSection =
    canWrite && !legacy
      ? `<div class="kb-detail-block">
          <h2 class="kb-detail-block-title">${esc(T("indexReadme"))}</h2>
          <div class="md-toggle-row">
            <button type="button" class="secondary small" id="readme-edit-tab">${esc(T("readmeEdit"))}</button>
            <button type="button" class="secondary small" id="readme-preview-tab">${esc(T("readmePreview"))}</button>
          </div>
          <textarea id="kb-readme-edit" class="kb-detail-textarea" rows="12">${esc(meta?.readme_md || "")}</textarea>
          <div id="kb-readme-preview" class="md-preview" style="display:none">${meta?.readme_md ? markdownToHtml(meta.readme_md) : ""}</div>
          <button type="button" id="save-readme-btn">${esc(T("saveDone"))}</button>
        </div>`
      : `<div class="kb-detail-block">
          <h2 class="kb-detail-block-title">${esc(T("indexReadme"))}</h2>
          <div class="md-preview">${meta?.readme_md ? markdownToHtml(meta.readme_md) : ""}</div>
        </div>`;

  const membersSection =
    legacy || isPub
      ? ""
      : `<div class="kb-detail-block">
          <h2 class="kb-detail-block-title">${esc(T("visibleMembers"))}</h2>
          <div id="members-body">
            ${members?.ok ? renderMembersList(members.members, name, isOwner) : `<p class="muted small">${esc(T("membersLoadError"))}</p>`}
          </div>
          ${isOwner && members?.ok ? renderAddMemberForm() : ""}
        </div>`;

  appEl.innerHTML = `
    <div class="app-shell">
      ${renderShellSidebar(null)}
      <div class="shell-main">
        <div class="shell-content">
          ${renderKbDetailTopbar(user, { name, subtitle: T("kbManageFixedSubtitle") })}
          <div class="shell-body profile-panel kb-detail-shell">
            <section class="card kb-detail-card">
                ${nameSection ? `${nameSection}<hr class="hr-soft hr-soft--kb-detail" />` : ""}
                ${iconSection}
                ${iconSection ? `<hr class="hr-soft hr-soft--kb-detail" />` : ""}
                ${visibilitySection}
                ${!legacy ? `<hr class="hr-soft hr-soft--kb-detail" />` : ""}
                ${descSection}
                <hr class="hr-soft hr-soft--kb-detail" />
                ${readmeSection}
                ${membersSection ? `<hr class="hr-soft hr-soft--kb-detail" />${membersSection}` : ""}
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title">${esc(T("search"))}</h2>
                  <div class="search-row">
                    <input id="search-q" placeholder="${esc(T("searchPlaceholder"))}" />
                    <button type="button" id="search-btn">${esc(T("runSearch"))}</button>
                  </div>
                  <pre id="search-out" class="search-out">${esc(T("noResults"))}</pre>
                </div>
                ${canDelete ? `<hr class="hr-soft hr-soft--kb-detail" /><div class="kb-detail-block kb-detail-block--danger"><button type="button" class="danger" id="del-kb-btn">${esc(T("deleteKb"))}</button></div>` : ""}
            </section>
          </div>
        </div>
      </div>
    </div>
  `;

  bindShellChrome(user);
  void refreshTopbarAvatar(user);
  void ensureKbIconOnImg(document.getElementById("kb-icon-edit-preview"), name);

  const runSearch = async () => {
    const q = document.getElementById("search-q").value.trim();
    const out = document.getElementById("search-out");
    if (!q) return;
    out.textContent = "…";
    try {
      const data = await fetchJSON(`/api/search/${encodeURIComponent(name)}?${new URLSearchParams({ query: q })}`);
      out.textContent = data.result || "";
    } catch (e) {
      out.textContent = e.message;
    }
  };
  document.getElementById("search-btn").addEventListener("click", runSearch);
  document.getElementById("search-q").addEventListener("keydown", (e) => {
    if (e.key === "Enter") runSearch();
  });

  const saveDescBtn = document.getElementById("save-desc-btn");
  if (saveDescBtn) {
    saveDescBtn.addEventListener("click", async () => {
      const description = document.getElementById("kb-desc-edit").value.trim();
      try {
        await fetchJSON(`/api/kb/${encodeURIComponent(name)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description }),
        });
        setStatus(T("saveDone"));
      } catch (e) {
        setStatus(e.message, true);
      }
    });
  }
  document.getElementById("readme-edit-tab")?.addEventListener("click", () => {
    document.getElementById("kb-readme-edit").style.display = "";
    document.getElementById("kb-readme-preview").style.display = "none";
  });
  document.getElementById("readme-preview-tab")?.addEventListener("click", () => {
    const text = document.getElementById("kb-readme-edit")?.value || "";
    const p = document.getElementById("kb-readme-preview");
    p.innerHTML = text ? markdownToHtml(text) : "";
    p.style.display = "";
    document.getElementById("kb-readme-edit").style.display = "none";
  });
  document.getElementById("save-readme-btn")?.addEventListener("click", async () => {
    const readme_md = document.getElementById("kb-readme-edit")?.value?.trim() || "";
    try {
      await fetchJSON(`/api/kb/${encodeURIComponent(name)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ readme_md }),
      });
      setStatus(T("saveDone"));
    } catch (e) {
      setStatus(e.message, true);
    }
  });
  const doUploadKbIcon = async (f) => {
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      kbIconBlobCache.delete(name);
      await fetchJSON(`/api/kb/${encodeURIComponent(name)}/icon`, { method: "POST", body: fd });
      setStatus(T("iconUpdated"));
      await render();
    } catch (e) {
      setStatus(e.message, true);
    }
  };
  document.getElementById("pick-icon-btn")?.addEventListener("click", () => {
    document.getElementById("kb-icon-edit-file")?.click();
  });
  document.getElementById("kb-icon-edit-file")?.addEventListener("change", async (e) => {
    await doUploadKbIcon(e.target?.files?.[0]);
    e.target.value = "";
  });
  document.getElementById("remove-icon-btn")?.addEventListener("click", async () => {
    try {
      kbIconBlobCache.delete(name);
      await fetchJSON(`/api/kb/${encodeURIComponent(name)}/icon`, { method: "DELETE" });
      setStatus(T("iconUpdated"));
      await render();
    } catch (e) {
      setStatus(e.message, true);
    }
  });

  const saveNameBtn = document.getElementById("save-name-btn");
  if (saveNameBtn) {
    saveNameBtn.addEventListener("click", async () => {
      const newName = document.getElementById("kb-name-edit").value.trim();
      if (!newName) return;
      try {
        const res = await fetchJSON(`/api/kb/${encodeURIComponent(name)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: newName }),
        });
        setStatus(T("renameDone"));
        go(`/kb/${encodeURIComponent(res.name || newName)}/manage`);
      } catch (e) {
        setStatus(e.message, true);
      }
    });
  }

  const delBtn = document.getElementById("del-kb-btn");
  if (delBtn) {
    delBtn.addEventListener("click", async () => {
      if (!(await showConfirmDialog(T("confirmDeleteKb", name)))) return;
      try {
    await fetchJSON(`/api/indexes/${encodeURIComponent(name)}?delete_sqlite=1`, { method: "DELETE" });
        setStatus(T("kbDeleted"));
        go("/");
      } catch (e) {
        setStatus(e.message, true);
      }
    });
  }

  bindMemberEvents(name, isOwner);

  document.getElementById("kb-vis-toggle")?.addEventListener("click", async (e) => {
    const btn = e.target.closest(".kb-lock-opt");
    if (!btn) return;
    const wantPublic = btn.getAttribute("data-vis") === "public";
    if (!(await showConfirmDialog(T("confirmVisibilityChange")))) return;
    try {
      await fetchJSON(`/api/kb/${encodeURIComponent(name)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_public: wantPublic }),
      });
      setStatus(T("visibilityUpdated"));
      await render();
  } catch (err) {
    setStatus(err.message, true);
  }
});
}

function renderMembersList(members, kbName, isOwner) {
  if (!members?.length) return `<p class="muted small">—</p>`;
  return `<ul class="member-list">
    ${members
      .map((m) => {
        const isMemOwner = m.role === "owner";
        const rm =
          isOwner && !isMemOwner
            ? `<button type="button" class="secondary small remove-member" data-user="${esc(m.username)}">${esc(T("removeMember"))}</button>`
            : "";
        const roleLabel =
          isMemOwner
            ? currentLang === "zh"
              ? "创建者"
              : "Owner"
            : "";
        return `<li class="member-row">
          <span class="member-row-main"><strong>${esc(m.username)}</strong>${roleLabel ? ` <span class="muted small">${esc(roleLabel)}</span>` : ""}</span>
          ${rm}
        </li>`;
      })
      .join("")}
  </ul>`;
}

function renderAddMemberForm() {
  return `
    <div class="add-member">
      <label><span>${esc(T("memberUser"))}</span><input id="new-member-user" /></label>
      <button type="button" id="add-member-btn">${esc(T("addMember"))}</button>
    </div>
  `;
}

function bindMemberEvents(kbName, isOwner) {
  if (!isOwner) return;
  const body = document.getElementById("members-body");
  body?.addEventListener("click", async (e) => {
    const btn = e.target.closest(".remove-member");
    if (!btn) return;
    const u = btn.getAttribute("data-user");
    if (!(await showConfirmDialog(T("confirmDeleteMember", u || "")))) return;
    try {
      await fetchJSON(`/api/kb/${encodeURIComponent(kbName)}/members/${encodeURIComponent(u)}`, { method: "DELETE" });
      setStatus(T("memberRemoved"));
      render();
    } catch (err) {
      setStatus(err.message, true);
    }
  });
  document.getElementById("add-member-btn")?.addEventListener("click", async () => {
    const username = document.getElementById("new-member-user").value.trim();
    if (!username) return;
    if (!(await showConfirmDialog(T("confirmAddMember", username)))) return;
    try {
      await fetchJSON(`/api/kb/${encodeURIComponent(kbName)}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, can_write: false }),
      });
      setStatus(T("memberAdded"));
      render();
    } catch (err) {
      setStatus(err.message, true);
    }
  });
}

async function render() {
  const route = parseRoute();
  document.documentElement.lang = currentLang === "zh" ? "zh-CN" : "en";

  if (route.type === "login" || route.type === "register") {
    setAuthPageLayout(true);
    if (getToken()) {
      const u = await ensureSession();
      if (u) {
        history.replaceState({}, "", "/");
        return render();
      }
    }
    await renderLogin(route.type === "register");
    return;
  }

  if (!getToken()) {
    setAuthPageLayout(true);
    sessionStorage.setItem("bcecli.returnTo", location.pathname + location.search);
    history.replaceState({}, "", "/login");
    return render();
  }

  const user = await ensureSession();
  if (!user) {
    setAuthPageLayout(true);
    history.replaceState({}, "", "/login");
    return render();
  }

  if (route.type === "changePassword") {
    setAuthPageLayout(true);
    await renderChangePassword(user);
    return;
  }

  setAuthPageLayout(false);
  clearStatus();

  if (route.type === "kb") {
    await renderKbPublicDetail(user, route.name);
    return;
  }

  if (route.type === "kbManage") {
    await renderKbManage(user, route.name);
    return;
  }

  if (route.type === "myKb") {
    await renderMyKb(user);
    return;
  }

  if (route.type === "addKb") {
    await renderAddKb(user);
    return;
  }

  if (route.type === "profile") {
    await renderProfile(user);
    return;
  }

  await renderPlaza(user);
}

const restored = loadState();
if (restored?.lang === "zh" || restored?.lang === "en") {
  currentLang = restored.lang;
} else {
  try {
    const ul = localStorage.getItem(UI_LANG_KEY);
    if (ul === "zh" || ul === "en") currentLang = ul;
  } catch {
    /* ignore */
  }
}
try {
  localStorage.setItem(UI_LANG_KEY, currentLang);
} catch {
  /* ignore */
}
if (restored?.stagedUploadId) stagedUploadId = restored.stagedUploadId;
if (restored?.currentJobId) currentJobId = restored.currentJobId;

render().catch((e) => setStatus(e.message, true));

import { loadUiConfig, uiConfig } from "./ui_config.js";

const AUTH_TOKEN_KEY = "ragret.auth.token";
const STATE_KEY = "ragret.frontend.state.v3";
/** Persisted interface language (mirrored from state for stable preference). */
const UI_LANG_KEY = "ragret.ui.lang";
/** Persisted UI theme: "dark" | "light" (mirrored for early paint). */
const UI_THEME_KEY = "ragret.ui.theme";
/** Served from Vite `public/` → static root */
const DEFAULT_AVATAR_URL = "/default-avatar.svg";
const DEFAULT_KB_ICON_URL = "/default-kb.svg";

let currentLang = "en";
let currentTheme = "light";
let stagedUploadId = null;
/** Manage-page corpus update: staged tar (not persisted across reload). */
let manageCorpusUploadId = null;
let manageCorpusKbFor = null;
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
    navQuickQa: "Quick Q&A",
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
    kbTypeLabel: "Knowledge base type",
    kbTypeTar: "Local push",
    kbTypeWebhook: "Webhook (GitLab / GitHub)",
    indexName: "Name",
    indexDescription: "Description",
    indexReadme: "README",
    readmePreview: "Preview",
    readmeEdit: "Edit",
    archiveLabel: "Archive (.tar / .tar.gz / .tgz)",
    pushArchiveLabel: "Local push settings",
    webhookAddKbSectionTitle: "Webhook & repository (HTTP clone URL, provider, secret)",
    webhookProviderLabel: "Webhook provider",
    webhookProviderGitlab: "GitLab",
    webhookProviderGithub: "GitHub",
    webhookUrlLabel: "Webhook URL",
    folderPushUrlLabel: "Folder push URL",
    folderPushHint: "Your client can upload a tar archive to this URL; the server queues an incremental rebuild.",
    folderPushHeaderHint: "Send secret in header: X-Webhook-Token: <secret>",
    webhookSecretLabel: "Secret token",
    webhookRepoUrlLabel: "Repository URL (HTTP/HTTPS)",
    webhookBranchLabel: "Branch to build",
    webhookBranchPlaceholder: "e.g. main or refs/heads/main",
    webhookRepoUrlPlaceholder: "e.g. https://github.com/org/repo.git or GitLab HTTP URL",
    webhookSecretPlaceholderGitlab: "Optional; GitLab sends it as X-Gitlab-Token",
    webhookSecretPlaceholderGithub: "Same secret as in GitHub webhook settings (HMAC SHA-256)",
    webhookSecretRegenerate: "Regenerate secret",
    webhookConfigured: "Webhook configured. Build starts when push events arrive.",
    webhookSaveRepo: "Save repository settings",
    webhookManualPull: "Pull now",
    webhookManualPullHint:
      "Queues the same build as a push webhook. Uses the repository URL saved below (updated automatically on each push).",
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
    quickQaEntryTitle: "Quick Q&A",
    quickQaEntryDesc: "Open an AI Q&A interface powered by a local LangGraph agent.",
    quickQaOpen: "Open AI Q&A",
    quickQaTitle: "Quick Q&A",
    quickQaSubtitle: "Quickly test knowledge base effects. Content on this page is not kept after refresh.",
    quickQaInputLabel: "Your question",
    quickQaInputPlaceholder: "For example: What time is it now?",
    quickQaSend: "Send",
    quickQaThinking: "Thinking...",
    quickQaWelcome: "Hi! I'm the RAGret Q&A assistant. What would you like to ask?",
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
    renameKbWebhookWarning:
      "Renaming changes the webhook URL path (the last segment matches the knowledge base name).",
    newKbName: "New knowledge base name",
    saveName: "Save name",
    renameDone: "Knowledge base renamed.",
    deleteKb: "Delete library",
    confirmDeleteKb: (n) => `Delete knowledge base "${n}"? This removes registration and the SQLite file.`,
    refresh: "Refresh",
    ready: "Ready.",
    requireFields: "Name and description are required.",
    requireWebhookBranch: "Enter the branch to clone for webhook builds.",
    requireStaged: "Upload a tar archive first.",
    requireStagedManage: "Upload a tar archive first, then rebuild the index.",
    requireDescriptionForRebuild: "Add a non-empty description (above) before rebuilding.",
    uploadStart: "Uploading…",
    uploadStaged: "Upload done. Fill name and description, then build.",
    uploadStagedManage: "Upload complete. Click rebuild to update the index (incremental when possible).",
    uploadFailed: "Upload failed.",
    kbUpdateCorpusTitle: "Update corpus",
    kbUpdateCorpusHint:
      "Upload a new tar with the same folder layout. The server compares file fingerprints and re-embeds only changed files when possible.",
    updateCorpusRebuild: "Rebuild index",
    kbManageCorpusStaged: "Tar archive staged for this library — click rebuild when ready.",
    navTasks: "Tasks",
    navSkill: "SKILL.md",
    tasksTitle: "Build tasks",
    tasksSubtitle: "Queued and running index jobs for your account.",
    tasksEmpty: "No tasks yet.",
    taskDetailTitle: "Task",
    taskOpCreate: "Create library",
    taskOpUpdate: "Update corpus",
    taskCancel: "Cancel task",
    taskCancelConfirm: "Cancel this task?",
    phase_cancelled: "Cancelled",
    taskEnqueued: "Task queued. Open the tasks page to track progress.",
    taskEnqueuedHint: "Rebuild runs as a background task; this library stays searchable until the swap completes.",
    tasksSearchPlaceholder: "Search by library name or status",
    taskStatusTitle: "Status",
    taskProgressTitle: "Progress",
    taskCardCreate: "Create",
    taskCardUpdate: "Update",
    taskCardQueued: "Queued",
    taskCardRunning: "Running",
    taskCardRunningHint: "Build in progress",
    taskBackToList: "Back to task list",
    taskNotFoundOrDone: "This task is no longer available.",
    skillTitle: "SKILL.md",
    skillSubtitle: "Project skill document",
    skillDownload: "Download ZIP",
    skillDownloadQuick: "Download RAGret SKILL.md",
    taskJobRemovedAfterDone: "Build finished. Returning to tasks.",
    taskCancelledRemoved: "Task cancelled.",
    buildDone: (n) => `Built "${n}".`,
    phase_queued: "Queued",
    phase_extract: "Extract",
    phase_git_clone: "Git clone",
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
    preferencesTitle: "Preferences",
    themeLabel: "Appearance",
    themeDark: "Dark",
    themeLight: "Light",
    accountTitle: "Account settings",
    accountSubtitle: "Profile photo",
    apiKeysTitle: "API keys",
    repoPatSectionTitle: "Repository PATs",
    repoPatSectionBlurb: "Used to clone private repos when building from GitLab or GitHub webhooks.",
    gitlabPatSubTitle: "GitLab",
    gitlabPatLabel: "Personal access token (read_repository)",
    gitlabPatPlaceholder: "glpat-...",
    gitlabPatSave: "Save token",
    gitlabPatSaved: "GitLab token saved.",
    gitlabPatConfigured: "GitLab token is configured.",
    gitlabPatNotConfigured: "GitLab token is not configured.",
    githubPatSubTitle: "GitHub",
    githubPatLabel: "Fine-grained or classic PAT (repo read / contents)",
    githubPatPlaceholder: "github_pat_... or ghp_...",
    githubPatSave: "Save token",
    githubPatSaved: "GitHub token saved.",
    githubPatConfigured: "GitHub token is configured.",
    githubPatNotConfigured: "GitHub token is not configured.",
    apiKeyCreate: "Create API key",
    apiKeyDelete: "Delete",
    apiKeyEyeShow: "Show",
    apiKeyEyeHide: "Hide",
    apiKeyEmpty: "No API keys yet.",
    apiKeyMaxHint: "Up to 3 keys. New keys start with sk-",
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
    navQuickQa: "快速问答",
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
    kbTypeLabel: "知识库类型",
    kbTypeTar: "本地推送",
    kbTypeWebhook: "Webhook（GitLab / GitHub）",
    indexName: "名称",
    indexDescription: "描述",
    indexReadme: "README",
    readmePreview: "预览",
    readmeEdit: "编辑",
    archiveLabel: "归档（.tar / .tar.gz / .tgz）",
    pushArchiveLabel: "本地推送设置",
    webhookAddKbSectionTitle: "Webhook 与仓库（HTTP 克隆地址、平台、Secret）",
    webhookProviderLabel: "Webhook 平台",
    webhookProviderGitlab: "GitLab",
    webhookProviderGithub: "GitHub",
    webhookUrlLabel: "Webhook 链接",
    folderPushUrlLabel: "文件夹推送 URL",
    folderPushHint: "你的客户端可向此地址上传 tar 包，服务端会自动排队增量重建。",
    folderPushHeaderHint: "请求头携带：X-Webhook-Token: <secret>",
    webhookSecretLabel: "Secret Token",
    webhookRepoUrlLabel: "仓库地址（HTTP/HTTPS）",
    webhookBranchLabel: "构建分支",
    webhookBranchPlaceholder: "例如 main 或 refs/heads/main",
    webhookRepoUrlPlaceholder: "例如：https://github.com/org/repo.git 或 GitLab HTTP 地址",
    webhookSecretPlaceholderGitlab: "可选；GitLab 以 X-Gitlab-Token 请求头发送",
    webhookSecretPlaceholderGithub: "与 GitHub Webhook 里填写的 Secret 一致（HMAC SHA-256）",
    webhookSecretRegenerate: "重新生成 Secret",
    webhookConfigured: "Webhook 配置完成，后续 push 事件会触发构建。",
    webhookSaveRepo: "保存仓库设置",
    webhookManualPull: "立即拉取",
    webhookManualPullHint:
      "与 push webhook 相同的构建任务；使用下方保存的仓库地址（每次 push 会自动更新）。",
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
    quickQaEntryTitle: "快速问答",
    quickQaEntryDesc: "打开一个基于本地 LangGraph agent 的 AI 问答界面。",
    quickQaOpen: "打开 AI 问答",
    quickQaTitle: "快速问答",
    quickQaSubtitle: "快速测试知识库效果，注意本页面内容刷新不保留",
    quickQaInputLabel: "你的问题",
    quickQaInputPlaceholder: "例如：现在几点了？",
    quickQaSend: "发送",
    quickQaThinking: "思考中...",
    quickQaWelcome: "你好！我是RAGret问答助手，有什么想问的呢？",
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
    renameKbWebhookWarning:
      "重命名后 Webhook 地址会变化（URL 路径末尾与知识库名一致）。",
    newKbName: "新的知识库名称",
    saveName: "保存名称",
    renameDone: "知识库已重命名。",
    deleteKb: "删除知识库",
    confirmDeleteKb: (n) => `确认删除知识库「${n}」？将注销注册并删除 SQLite 文件。`,
    refresh: "刷新",
    ready: "就绪。",
    requireFields: "请填写名称和描述。",
    requireWebhookBranch: "请填写 Webhook 克隆所用的分支名称。",
    requireStaged: "请先上传 tar 并完成上传。",
    requireStagedManage: "请先上传 tar，再点击重建索引。",
    requireDescriptionForRebuild: "请先填写非空描述（上方描述框）后再重建。",
    uploadStart: "正在上传…",
    uploadStaged: "上传完成。填写名称与描述后构建。",
    uploadStagedManage: "上传完成。点击重建索引以更新语料（在条件允许时走增量更新）。",
    uploadFailed: "上传失败。",
    kbUpdateCorpusTitle: "更新语料",
    kbUpdateCorpusHint:
      "上传新的 tar，目录结构可与上次一致。服务端按文件指纹比对，尽量只对变更文件重新向量化。",
    updateCorpusRebuild: "重建索引",
    kbManageCorpusStaged: "已为该知识库暂存 tar — 准备好后点击重建索引。",
    navTasks: "任务",
    navSkill: "SKILL.md",
    tasksTitle: "构建任务",
    tasksSubtitle: "当前账号的排队与运行中的索引任务。",
    tasksEmpty: "暂无任务。",
    taskDetailTitle: "任务",
    taskOpCreate: "创建知识库",
    taskOpUpdate: "更新语料",
    taskCancel: "取消任务",
    taskCancelConfirm: "确认取消该任务？",
    phase_cancelled: "已取消",
    taskEnqueued: "任务已加入队列，可在任务页查看进度。",
    taskEnqueuedHint: "重建在后台排队执行；在替换完成前仍可正常检索本库。",
    tasksSearchPlaceholder: "按知识库名称或状态搜索",
    taskStatusTitle: "状态",
    taskProgressTitle: "进度",
    taskCardCreate: "创建",
    taskCardUpdate: "更新",
    taskCardQueued: "排队",
    taskCardRunning: "运行中",
    taskCardRunningHint: "正在构建",
    taskBackToList: "返回任务列表",
    taskNotFoundOrDone: "该任务已结束或不存在。",
    skillTitle: "SKILL.md",
    skillSubtitle: "项目技能文档",
    skillDownload: "下载 ZIP",
    skillDownloadQuick: "下载 RAGret SKILL.md",
    taskJobRemovedAfterDone: "构建已完成，正在返回任务列表。",
    taskCancelledRemoved: "任务已取消。",
    buildDone: (n) => `已构建「${n}」。`,
    phase_queued: "排队",
    phase_extract: "解压",
    phase_git_clone: "拉取仓库",
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
    preferencesTitle: "偏好设置",
    themeLabel: "界面风格",
    themeDark: "暗色",
    themeLight: "亮色",
    accountTitle: "账户设置",
    accountSubtitle: "头像",
    apiKeysTitle: "API Key 列表",
    repoPatSectionTitle: "代码仓库访问令牌",
    repoPatSectionBlurb: "Webhook 构建私有仓库时，用于 GitLab / GitHub 的 HTTP 克隆鉴权。",
    gitlabPatSubTitle: "GitLab",
    gitlabPatLabel: "Personal access token（read_repository）",
    gitlabPatPlaceholder: "glpat-...",
    gitlabPatSave: "保存令牌",
    gitlabPatSaved: "GitLab 令牌已保存。",
    gitlabPatConfigured: "GitLab 令牌已配置。",
    gitlabPatNotConfigured: "GitLab 令牌未配置。",
    githubPatSubTitle: "GitHub",
    githubPatLabel: "Fine-grained 或 classic PAT（需能读仓库内容）",
    githubPatPlaceholder: "github_pat_... 或 ghp_...",
    githubPatSave: "保存令牌",
    githubPatSaved: "GitHub 令牌已保存。",
    githubPatConfigured: "GitHub 令牌已配置。",
    githubPatNotConfigured: "GitHub 令牌未配置。",
    apiKeyCreate: "创建 API Key",
    apiKeyDelete: "删除",
    apiKeyEyeShow: "显示",
    apiKeyEyeHide: "隐藏",
    apiKeyEmpty: "暂无 API Key。",
    apiKeyMaxHint: "最多 3 条，新建前缀为 sk-",
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

/** Job row removed after terminal state (404) — same as unknown id for API. */
function isJobGoneError(e) {
  const m = String(e?.message || "");
  return m === "Unknown job" || /\b404\b/.test(m);
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
  if (p === "/plaza") return { type: "home" };
  if (p === "/quick-qa") return { type: "quickQa" };
  if (p === "/") return { type: "quickQa" };
  if (p === "/profile") return { type: "profile" };
  if (p === "/change-password") return { type: "changePassword" };
  if (p === "/tasks" || p === "/tasks/") return { type: "tasks" };
  if (p.startsWith("/tasks/")) {
    const rest = p
      .slice("/tasks/".length)
      .split("/")
      .filter(Boolean)
      .map((s) => decodeURIComponent(s));
    if (rest[0]) return { type: "taskDetail", jobId: rest[0] };
    return { type: "tasks" };
  }
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
  if (p === "/" || p === "") return { type: "quickQa" };
  return { type: "home" };
}

function phaseText(phase) {
  return T(`phase_${phase || "queued"}`) || String(phase || "");
}

async function pollJobUntilTerminal(jobId, onTick) {
  while (true) {
    let job;
    try {
      job = await fetchJSON(`/api/jobs/${encodeURIComponent(jobId)}`);
    } catch (e) {
      if (isJobGoneError(e)) {
        return { status: "done", _jobRemoved: true };
      }
      throw e;
    }
    if (onTick) onTick(job);
    if (job.status === "done") return job;
    if (job.status === "error") return job;
    if (job.status === "cancelled") return job;
    const delayMs = String(job.status) === "queued" ? 2000 : 900;
    await new Promise((r) => setTimeout(r, delayMs));
  }
}

function saveState() {
  try {
    const payload = JSON.stringify({
      lang: currentLang,
      theme: currentTheme,
      stagedUploadId,
    });
    localStorage.setItem(STATE_KEY, payload);
    localStorage.setItem(UI_LANG_KEY, currentLang);
    localStorage.setItem(UI_THEME_KEY, currentTheme);
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

function applyTheme() {
  if (currentTheme === "light") document.documentElement.setAttribute("data-theme", "light");
  else document.documentElement.removeAttribute("data-theme");
}

function setTheme(theme) {
  currentTheme = theme === "light" ? "light" : "dark";
  applyTheme();
  saveState();
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

function sanitizeFenceLang(s) {
  const t = String(s || "").trim();
  if (!t || !/^[a-zA-Z][\w+#.-]{0,31}$/.test(t)) return "";
  return t;
}

function markdownInlineToHtml(s) {
  const placeholders = [];
  let html = esc(String(s || ""));
  html = html.replace(/`([^`]+)`/g, (_, codeText) => {
    const token = `__RAGRET_MD_CODE_${placeholders.length}__`;
    placeholders.push(`<code class="md-inline-code">${codeText}</code>`);
    return token;
  });
  html = html.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer">$1</a>',
  );
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  return html.replace(/__RAGRET_MD_CODE_(\d+)__/g, (_, i) => placeholders[Number(i)] || "");
}

function markdownToHtml(md) {
  const src = String(md || "").replace(/\r\n/g, "\n");
  const lines = src.split("\n");
  const out = [];
  let i = 0;
  const parseTableCells = (line) =>
    String(line || "")
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => c.trim());
  const isTableSepLine = (line) => {
    const cells = parseTableCells(line);
    if (!cells.length) return false;
    return cells.every((c) => /^:?-{3,}:?$/.test(c));
  };
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();
    const fenceMatch = trimmed.match(/^```(\S*)\s*$/);
    if (fenceMatch) {
      const fenceLang = sanitizeFenceLang(fenceMatch[1]);
      const codeLines = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().match(/^```(\S*)\s*$/)) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      const langClass = fenceLang ? ` language-${esc(fenceLang)}` : "";
      out.push(`<pre class="md-fence"><code class="md-fence-code${langClass}">${codeLines.map(esc).join("\n")}</code></pre>`);
      continue;
    }
    if (!trimmed) {
      i += 1;
      continue;
    }
    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const n = heading[1].length;
      out.push(`<h${n}>${markdownInlineToHtml(heading[2])}</h${n}>`);
      i += 1;
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      out.push('<hr class="md-hr"/>');
      i += 1;
      continue;
    }
    if (/^>\s+/.test(trimmed)) {
      const bq = [];
      while (i < lines.length && /^>\s+/.test(lines[i].trim())) {
        bq.push(lines[i].trim().replace(/^>\s+/, ""));
        i += 1;
      }
      out.push(`<blockquote>${bq.map(markdownInlineToHtml).join("<br/>")}</blockquote>`);
      continue;
    }
    if (/^\s*-\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*-\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*-\s+/, ""));
        i += 1;
      }
      out.push(`<ul>${items.map((it) => `<li>${markdownInlineToHtml(it)}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i += 1;
      }
      out.push(`<ol>${items.map((it) => `<li>${markdownInlineToHtml(it)}</li>`).join("")}</ol>`);
      continue;
    }
    if (trimmed.includes("|") && i + 1 < lines.length && isTableSepLine(lines[i + 1])) {
      const headCells = parseTableCells(line);
      i += 2;
      const bodyRows = [];
      while (i < lines.length && lines[i].trim().includes("|")) {
        bodyRows.push(parseTableCells(lines[i]));
        i += 1;
      }
      const thead = `<thead><tr>${headCells.map((c) => `<th>${markdownInlineToHtml(c)}</th>`).join("")}</tr></thead>`;
      const tbody = bodyRows.length
        ? `<tbody>${bodyRows
            .map((cells) => `<tr>${headCells.map((_, idx) => `<td>${markdownInlineToHtml(cells[idx] || "")}</td>`).join("")}</tr>`)
            .join("")}</tbody>`
        : "";
      out.push(`<table class="md-table">${thead}${tbody}</table>`);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !lines[i].trim().match(/^```(\S*)\s*$/)) {
      const t = lines[i].trim();
      if (/^(#{1,6})\s+/.test(t) || /^>\s+/.test(t) || /^\s*-\s+/.test(lines[i]) || /^\s*\d+\.\s+/.test(lines[i])) break;
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) break;
      if (t.includes("|") && i + 1 < lines.length && isTableSepLine(lines[i + 1])) break;
      para.push(t);
      i += 1;
    }
    if (para.length) {
      out.push(`<p>${markdownInlineToHtml(para.join(" "))}</p>`);
      continue;
    }
    i += 1;
  }
  return out.join("\n");
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

function renderTaskCardsInnerHtml(jobs) {
  return jobs
    .map((j, idx) => {
      const cidx = idx % 5;
      const jid = String(j.job_id || "");
      const opText = String(j.op) === "update" ? T("taskCardUpdate") : T("taskCardCreate");
      const st = String(j.status || "").toLowerCase();
      const stateText = st === "running" ? T("taskCardRunning") : T("taskCardQueued");
      const runHint = esc(T("taskCardRunningHint"));
      const runningCorner =
        st === "running"
          ? `<div class="kb-card-running-corner" title="${runHint}" aria-label="${runHint}"><span class="task-card-running-spin" aria-hidden="true"></span></div>`
          : "";
      return `<article class="kb-card kb-card--tone${cidx}" data-task-job="${esc(jid)}">
                  <h3 class="kb-card-title-row"><img class="kb-card-kb-icon" alt="" width="20" height="20" src="${DEFAULT_KB_ICON_URL}" /><span class="kb-card-name">${esc(String(j.kb_name || ""))}</span></h3>
                  <p class="desc task-card-status-line" role="status"><span class="task-card-op">${esc(opText)}</span><span class="task-card-sep" aria-hidden="true"> · </span><span class="task-card-state">${esc(stateText)}</span></p>
                  ${runningCorner}
                </article>`;
    })
    .join("");
}

function bindTaskGridNavigation() {
  appEl.querySelectorAll("[data-task-job]").forEach((card) => {
    card.addEventListener("click", () => {
      const id = card.getAttribute("data-task-job");
      if (id) go(`/tasks/${encodeURIComponent(id)}`);
    });
  });
}

function bindKbGridNavigation(user) {
  appEl.querySelectorAll(".kb-card").forEach((card) => {
    card.addEventListener("click", () => {
      if (card.hasAttribute("data-task-job")) return;
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
  return `<div class="kb-lock-field">
    <div class="kb-lock-choice" id="kb-vis-new" role="radiogroup" aria-label="${esc(T("kbVisibility"))}">
      <button type="button" class="kb-lock-opt is-active" data-vis="private" aria-pressed="true" title="${esc(closedT)}">${KB_LOCK}</button>
      <button type="button" class="kb-lock-opt" data-vis="public" aria-pressed="false" title="${esc(openT)}">${KB_UNLOCK}</button>
    </div>
    <input type="hidden" id="kb-visibility-new" value="private" />
  </div>`;
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
  const quickQaCl = active === "quickQa" ? " active" : "";
  const plazaCl = active === "plaza" ? " active" : "";
  const myCl = active === "my" ? " active" : "";
  const addCl = active === "add" ? " active" : "";
  const tasksCl = active === "tasks" ? " active" : "";
  const profCl = active === "profile" ? " active" : "";
  return `
    <aside class="sidebar" aria-label="${esc(T("navSection"))}">
      <div class="sidebar-title">${esc(T("navSection"))}</div>
      <button type="button" class="nav-item${quickQaCl}" data-nav="quickQa">${esc(T("navQuickQa"))}</button>
      <button type="button" class="nav-item${plazaCl}" data-nav="plaza">${esc(T("navKbPlaza"))}</button>
      <button type="button" class="nav-item${myCl}" data-nav="my">${esc(T("navMyKb"))}</button>
      <button type="button" class="nav-item${addCl}" data-nav="add">${esc(T("navAddKb"))}</button>
      <button type="button" class="nav-item${tasksCl}" data-nav="tasks">${esc(T("navTasks"))}</button>
      <button type="button" class="nav-item${profCl}" data-nav="profile">${esc(T("navAccount"))}</button>
      <div class="sidebar-spacer" aria-hidden="true"></div>
      <button type="button" class="nav-item nav-item--logout" id="sidebar-logout-btn">${esc(T("logout"))}</button>
    </aside>`;
}

function renderInterfaceLangSelect(selectId, omitOuterLabel = false) {
  const enSel = currentLang === "en" ? " selected" : "";
  const zhSel = currentLang === "zh" ? " selected" : "";
  const sel = `<select id="${esc(selectId)}" class="lang-select" aria-label="${esc(T("interfaceLanguage"))}">
        <option value="en"${enSel}>English</option>
        <option value="zh"${zhSel}>中文</option>
      </select>`;
  if (omitOuterLabel) {
    return `<div class="lang-select-wrap lang-select-wrap--bare">${sel}</div>`;
  }
  return `
    <label class="lang-select-wrap">
      <span class="lang-select-label">${esc(T("interfaceLanguage"))}</span>
      ${sel}
    </label>`;
}

function bindInterfaceLangSelect(selectId) {
  const el = document.getElementById(selectId);
  el?.addEventListener("change", () => {
    if (el.value === "en" || el.value === "zh") setLang(el.value);
  });
}

function renderThemeSelect(selectId, omitOuterLabel = false) {
  const darkSel = currentTheme === "dark" ? " selected" : "";
  const lightSel = currentTheme === "light" ? " selected" : "";
  const sel = `<select id="${esc(selectId)}" class="lang-select" aria-label="${esc(T("themeLabel"))}">
        <option value="dark"${darkSel}>${esc(T("themeDark"))}</option>
        <option value="light"${lightSel}>${esc(T("themeLight"))}</option>
      </select>`;
  if (omitOuterLabel) {
    return `<div class="lang-select-wrap lang-select-wrap--bare">${sel}</div>`;
  }
  return `
    <label class="lang-select-wrap">
      <span class="lang-select-label">${esc(T("themeLabel"))}</span>
      ${sel}
    </label>`;
}

function bindThemeSelect(selectId) {
  const el = document.getElementById(selectId);
  el?.addEventListener("change", () => {
    if (el.value === "dark" || el.value === "light") setTheme(el.value);
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
      if (n === "plaza") go("/plaza");
      if (n === "quickQa") go("/");
      if (n === "my") go("/my-kb");
      if (n === "add") go("/add-kb");
      if (n === "tasks") go("/tasks");
      if (n === "profile") go("/profile");
    });
  });
}

async function downloadSkillZip() {
  const res = await fetch("/api/skill-md/download", { headers: { ...authHeaders() } });
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(j.error || `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const u = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = u;
  a.download = "ragret.zip";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(u);
}

function bindUploadForm() {
  const form = document.getElementById("upload-form");
  if (!form) return;
  const pickBtn = document.getElementById("pick-archive-btn");
  const archiveInput = document.getElementById("kb-archive");
  const pickedFileNameEl = document.getElementById("picked-file-name");
  const sourceTypeEl = document.getElementById("kb-source-type");
  const tarBlock = document.getElementById("kb-source-tar-block");
  const webhookBlock = document.getElementById("kb-source-webhook-block");
  const uploadBlock = document.getElementById("kb-upload-progress-block");
  const folderPushUrlInput = document.getElementById("kb-folder-push-url");
  const webhookUrlInput = document.getElementById("kb-webhook-url");
  const webhookSecretInput = document.getElementById("kb-webhook-secret");
  const webhookSecretEyeBtn = document.getElementById("kb-webhook-secret-eye");
  const webhookSecretRegenBtn = document.getElementById("kb-webhook-secret-regen");
  const webhookRepoUrlInput = document.getElementById("kb-webhook-repo-url");
  const webhookRefInput = document.getElementById("kb-webhook-ref");
  const webhookProviderEl = document.getElementById("kb-webhook-provider");
  const nameInput = document.getElementById("kb-name");
  const sourceSectionTitleEl = document.getElementById("kb-source-section-title");
  const pushSecretInput = document.getElementById("kb-push-secret");
  const pushSecretEyeBtn = document.getElementById("kb-push-secret-eye");
  const pushSecretRegenBtn = document.getElementById("kb-push-secret-regen");
  let webhookSecretRaw = "";
  let webhookSecretVisible = false;
  const webhookBases = {
    gitlab: `${window.location.origin}/api/webhooks/gitlab/`,
    github: `${window.location.origin}/api/webhooks/github/`,
  };
  const refreshWebhookSecretInput = () => {
    if (!webhookSecretInput) return;
    webhookSecretInput.value = webhookSecretVisible ? webhookSecretRaw : "*".repeat(String(webhookSecretRaw || "").length);
    webhookSecretEyeBtn.textContent = webhookSecretVisible ? T("apiKeyEyeHide") : T("apiKeyEyeShow");
    if (pushSecretInput) {
      pushSecretInput.value = webhookSecretVisible ? webhookSecretRaw : "*".repeat(String(webhookSecretRaw || "").length);
    }
    if (pushSecretEyeBtn) pushSecretEyeBtn.textContent = webhookSecretVisible ? T("apiKeyEyeHide") : T("apiKeyEyeShow");
    if (folderPushUrlInput) folderPushUrlInput.value = buildFolderPushUrl();
  };
  const generateWebhookSecret = async () => {
    const d = await fetchJSON("/api/user/webhook-secret/generate");
    webhookSecretRaw = String(d?.secret || "");
    webhookSecretVisible = false;
    refreshWebhookSecretInput();
  };
  let webhookBaseUrl = webhookBases.gitlab;
  fetchJSON("/api/webhook-base")
    .then((r) => {
      const bases = r?.bases;
      if (bases && typeof bases === "object") {
        const g = String(bases.gitlab || "").trim();
        const h = String(bases.github || "").trim();
        if (g) webhookBases.gitlab = g.endsWith("/") ? g : `${g}/`;
        if (h) webhookBases.github = h.endsWith("/") ? h : `${h}/`;
      }
      const b = String(r?.base_url || "").trim();
      if (b) webhookBases.gitlab = b.endsWith("/") ? b : `${b}/`;
      applyWebhookProviderUi();
    })
    .catch(() => {
      /* keep defaults */
    });
  const buildWebhookUrl = () => {
    const kb = (nameInput?.value || "").trim();
    const seg = kb ? encodeURIComponent(kb) : "<kb-name>";
    const base = webhookBaseUrl.endsWith("/") ? webhookBaseUrl : `${webhookBaseUrl}/`;
    return `${base}${seg}`;
  };
  const buildFolderPushUrl = () => {
    const kb = (nameInput?.value || "").trim();
    const seg = kb ? encodeURIComponent(kb) : "<kb-name>";
    return `${window.location.origin}/api/push/${seg}`;
  };
  const refreshWebhookUrl = () => {
    if (webhookUrlInput) webhookUrlInput.value = buildWebhookUrl();
    if (folderPushUrlInput) folderPushUrlInput.value = buildFolderPushUrl();
  };
  const applyWebhookProviderUi = () => {
    const prov = webhookProviderEl?.value === "github" ? "github" : "gitlab";
    webhookBaseUrl = webhookBases[prov] || webhookBases.gitlab;
    if (webhookSecretInput) {
      webhookSecretInput.placeholder =
        prov === "github" ? T("webhookSecretPlaceholderGithub") : T("webhookSecretPlaceholderGitlab");
    }
    refreshWebhookUrl();
  };
  const syncSourceTypeUi = () => {
    const tp = sourceTypeEl?.value === "webhook" ? "webhook" : "tar";
    if (tarBlock) tarBlock.style.display = tp === "tar" ? "" : "none";
    if (uploadBlock) uploadBlock.style.display = tp === "tar" ? "" : "none";
    if (webhookBlock) webhookBlock.style.display = tp === "webhook" ? "" : "none";
    if (sourceSectionTitleEl) {
      sourceSectionTitleEl.textContent = tp === "webhook" ? T("webhookAddKbSectionTitle") : T("pushArchiveLabel");
    }
    applyWebhookProviderUi();
    if (tp === "webhook" && !webhookSecretRaw) {
      void generateWebhookSecret();
    }
    if (tp === "tar" && !webhookSecretRaw) {
      void generateWebhookSecret();
    }
  };
  syncSourceTypeUi();
  sourceTypeEl?.addEventListener("change", syncSourceTypeUi);
  webhookProviderEl?.addEventListener("change", () => {
    applyWebhookProviderUi();
  });
  nameInput?.addEventListener("input", refreshWebhookUrl);
  webhookSecretEyeBtn?.addEventListener("click", () => {
    webhookSecretVisible = !webhookSecretVisible;
    refreshWebhookSecretInput();
  });
  pushSecretEyeBtn?.addEventListener("click", () => {
    webhookSecretVisible = !webhookSecretVisible;
    refreshWebhookSecretInput();
  });
  webhookSecretRegenBtn?.addEventListener("click", async () => {
    try {
      await generateWebhookSecret();
    } catch (e) {
      setStatus(e.message, true);
    }
  });
  pushSecretRegenBtn?.addEventListener("click", async () => {
    try {
      await generateWebhookSecret();
    } catch (e) {
      setStatus(e.message, true);
    }
  });
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
    const sourceType = sourceTypeEl?.value === "webhook" ? "webhook" : "tar";
    if (!name || !description) return setStatus(T("requireFields"), true);
    if (sourceType === "tar" && !stagedUploadId) return setStatus(T("requireStaged"), true);
    if (sourceType === "webhook" && !String(webhookRepoUrlInput?.value || "").trim()) {
      return setStatus(T("requireFields"), true);
    }
    if (sourceType === "webhook" && !String(webhookRefInput?.value || "").trim()) {
      return setStatus(T("requireWebhookBranch"), true);
    }
    const submitBtn = document.getElementById("submit-btn");
    submitBtn.disabled = true;
    submitBtn.textContent = T("building");
    try {
      const is_public = document.getElementById("kb-visibility-new")?.value === "public";
      const start = await fetchJSON("/api/indexes/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          description,
          readme_md: readmeMd,
          upload_id: sourceType === "tar" ? stagedUploadId : undefined,
          source_type: sourceType,
          webhook_provider:
            sourceType === "webhook" ? (webhookProviderEl?.value === "github" ? "github" : "gitlab") : undefined,
          webhook_secret: webhookSecretRaw || undefined,
          repo_url: sourceType === "webhook" ? String(webhookRepoUrlInput?.value || "").trim() : undefined,
          ref: sourceType === "webhook" ? String(webhookRefInput?.value || "").trim() : undefined,
          is_public,
        }),
      });
      const jid = start.job_id;
      stagedUploadId = null;
      archiveInput.value = "";
      pickedFileNameEl.textContent = T("noFileChosen");
      const u = document.getElementById("upload-progress");
      const ut = document.getElementById("upload-progress-text");
      if (u) u.value = 0;
      if (ut) ut.textContent = "0%";
      saveState();
      setStatus(T("taskEnqueued"));
      go(`/tasks/${encodeURIComponent(jid)}`);
    } catch (err) {
      setStatus(err.message, true);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = T("startBuild");
    }
  });
}

async function renderLogin(register) {
  const loginLogo = uiConfig.login_logo_url
    ? `<div class="auth-logo-wrap" aria-hidden="true"><img class="auth-logo" src="${esc(uiConfig.login_logo_url)}" alt="" /></div>`
    : "";
  const anchorClass = loginLogo ? "auth-title-anchor auth-title-anchor--with-logo" : "auth-title-anchor";
  appEl.innerHTML = `
    <div class="auth-screen" style="position:relative">
      <div class="auth-center-stack">
        <div class="auth-title-block">
          <div class="auth-title-line">
            <div class="${anchorClass}">
              ${loginLogo}
              <h1 class="auth-title-heading">${esc(uiConfig.login_title)}</h1>
            </div>
          </div>
          <p class="muted auth-title-sub">${esc(register ? T("registerTitle") : T("loginTitle"))}</p>
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
    </div>
  `;
  const errEl = document.getElementById("auth-error");
  const flash = sessionStorage.getItem("ragret.flash");
  if (flash) {
    sessionStorage.removeItem("ragret.flash");
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
      const returnTo = sessionStorage.getItem("ragret.returnTo") || "/";
      sessionStorage.removeItem("ragret.returnTo");
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

function appendQuickQaMessage(host, role, text) {
  if (!host) return;
  const div = document.createElement("div");
  div.className = `quick-qa-msg ${role === "user" ? "is-user" : "is-assistant"}`;
  if (role === "assistant") {
    div.innerHTML = `<div class="quick-qa-bubble quick-qa-msg-md md-content">${markdownToHtml(String(text || ""))}</div>`;
  } else {
    div.innerHTML = `<div class="quick-qa-bubble quick-qa-msg-md md-content quick-qa-msg-plain"><p>${esc(String(text || "")).replace(/\n/g, "<br/>")}</p></div>`;
  }
  host.appendChild(div);
  host.scrollTop = host.scrollHeight;
}

function appendQuickQaThinking(host, label) {
  if (!host) return null;
  const div = document.createElement("div");
  div.className = "quick-qa-msg is-assistant is-thinking";
  div.innerHTML = `
    <div class="quick-qa-bubble quick-qa-msg-md md-content quick-qa-msg-plain quick-qa-thinking-bubble">
      <p>
        <span class="thinking-label">${esc(String(label || ""))}</span>
        <span class="thinking-dots" aria-hidden="true"><i></i><i></i><i></i></span>
      </p>
    </div>`;
  host.appendChild(div);
  host.scrollTop = host.scrollHeight;
  return div;
}

function setQuickQaThinkingLabel(thinkingEl, label) {
  if (!thinkingEl) return;
  const lab = thinkingEl.querySelector(".thinking-label");
  if (lab) lab.textContent = String(label || "");
}

async function renderQuickQa(user) {
  appEl.innerHTML = `
    <div class="app-shell">
      ${renderShellSidebar("quickQa")}
      <div class="shell-main">
        <div class="shell-content">
          ${renderTopbar(user, { title: T("quickQaTitle"), subtitle: T("quickQaSubtitle") })}
          <div class="shell-body">
            <div class="quick-qa-shell">
              <div id="quick-qa-messages" class="quick-qa-messages"></div>
              <form id="quick-qa-form" class="quick-qa-form">
                <div class="quick-qa-form-head">
                  <div aria-hidden="true"></div>
                  <button type="button" class="secondary quick-qa-skill-btn" id="quick-qa-skill-download-btn">${esc(T("skillDownloadQuick"))}</button>
                </div>
                <div class="quick-qa-input-wrap">
                  <textarea id="quick-qa-input" rows="2" placeholder="${esc(T("quickQaInputPlaceholder"))}" required></textarea>
                  <button id="quick-qa-send-btn" class="quick-qa-send-inside" type="submit">${esc(T("quickQaSend"))}</button>
                </div>
              </form>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
  bindShellChrome(user);
  void refreshTopbarAvatar(user);

  const msgs = document.getElementById("quick-qa-messages");
  const form = document.getElementById("quick-qa-form");
  const input = document.getElementById("quick-qa-input");
  const sendBtn = document.getElementById("quick-qa-send-btn");
  const chatMessages = [];
  input?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form?.requestSubmit();
    }
  });
  document.getElementById("quick-qa-skill-download-btn")?.addEventListener("click", async () => {
    try {
      await downloadSkillZip();
    } catch (e) {
      setStatus(e.message, true);
    }
  });
  appendQuickQaMessage(msgs, "assistant", T("quickQaWelcome"));
  chatMessages.push({ role: "assistant", content: String(T("quickQaWelcome")) });
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = String(input?.value || "").trim();
    if (!q) return;
    appendQuickQaMessage(msgs, "user", q);
    chatMessages.push({ role: "user", content: q });
    if (input) input.value = "";
    if (sendBtn) {
      sendBtn.disabled = true;
      sendBtn.textContent = T("quickQaThinking");
    }
    const thinkingEl = appendQuickQaThinking(msgs, T("quickQaThinking"));
    try {
      const res = await fetch("/api/quick-qa", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ question: q, messages: chatMessages, stream: true, lang: currentLang }),
      });
      if (res.status === 401) {
        setToken("");
        go("/login");
        return;
      }
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error || `HTTP ${res.status}`);
      }
      if (!res.body) {
        throw new Error("Empty response stream");
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let finalData = null;
      const processLine = (line) => {
        const ln = String(line || "").trim();
        if (!ln) return;
        const evt = JSON.parse(ln);
        if (evt.type === "tool_event") {
          setQuickQaThinkingLabel(thinkingEl, String(evt.text || T("quickQaThinking")));
        } else if (evt.type === "error") {
          throw new Error(String(evt.error || "Error"));
        } else if (evt.type === "final") {
          finalData = evt;
        }
      };
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          buf += decoder.decode();
          break;
        }
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, idx);
          buf = buf.slice(idx + 1);
          processLine(line);
        }
      }
      if (buf.trim()) processLine(buf);
      if (!finalData) {
        throw new Error("No final response from stream");
      }
      thinkingEl?.remove();
      const ans = String(finalData?.answer || "");
      if (!ans) throw new Error("Empty answer from server");
      appendQuickQaMessage(msgs, "assistant", ans);
      chatMessages.push({ role: "assistant", content: ans });
    } catch (err) {
      thinkingEl?.remove();
      const errMsg = String(err?.message || "Error");
      appendQuickQaMessage(msgs, "assistant", errMsg);
      chatMessages.push({ role: "assistant", content: errMsg });
    } finally {
      if (sendBtn) {
        sendBtn.disabled = false;
        sendBtn.textContent = T("quickQaSend");
      }
      input?.focus();
    }
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
          <div class="shell-body add-kb-page profile-panel kb-detail-shell page-frame page-frame--wide">
            <div class="page-frame__inner">
              <form id="upload-form" class="kb-detail-flow">
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title">${esc(T("indexName"))}</h2>
                  <input id="kb-name" required aria-label="${esc(T("indexName"))}" />
                </div>
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title">${esc(T("indexDescription"))}</h2>
                  <textarea id="kb-description" class="kb-detail-textarea" rows="3" required aria-label="${esc(T("indexDescription"))}"></textarea>
                </div>
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title">${esc(T("indexReadme"))}</h2>
                  <div class="md-toggle-row">
                    <button type="button" class="secondary small" id="readme-add-edit-tab">${esc(T("readmeEdit"))}</button>
                    <button type="button" class="secondary small" id="readme-add-preview-tab">${esc(T("readmePreview"))}</button>
                  </div>
                  <textarea id="kb-readme" class="kb-detail-textarea" rows="8" aria-label="${esc(T("indexReadme"))}"></textarea>
                  <div id="kb-readme-add-preview" class="md-preview" style="display:none"></div>
                </div>
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title">${esc(T("kbIcon"))}</h2>
                  <input type="file" id="kb-icon-new-file" class="hidden-file-input" accept="image/png,image/jpeg,image/gif,image/webp,.png,.jpg,.jpeg,.gif,.webp" />
                  <div class="kb-icon-manage-row">
                    <img id="kb-icon-add-preview" class="kb-manage-icon-preview" src="${DEFAULT_KB_ICON_URL}" alt="" />
                  </div>
                  <div class="file-picker-row">
                    <button type="button" class="secondary" id="pick-kb-icon-btn">${esc(T("uploadKbIcon"))}</button>
                  </div>
                </div>
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title">${esc(T("kbVisibility"))}</h2>
                  ${renderKbLockFieldNew()}
                </div>
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title">${esc(T("kbTypeLabel"))}</h2>
                  <label class="lang-select-wrap lang-select-wrap--bare">
                    <select id="kb-source-type" class="lang-select" aria-label="${esc(T("kbTypeLabel"))}">
                      <option value="tar">${esc(T("kbTypeTar"))}</option>
                      <option value="webhook">${esc(T("kbTypeWebhook"))}</option>
                    </select>
                  </label>
                </div>
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title" id="kb-source-section-title">${esc(T("archiveLabel"))}</h2>
                  <div id="kb-source-tar-block">
                    <input type="file" id="kb-archive" class="hidden-file-input" accept=".tar,.tgz,.tar.gz,.tar.bz2,.tar.xz,application/x-tar" />
                    <div class="file-picker-row">
                      <button type="button" class="secondary" id="pick-archive-btn">${esc(T("chooseFile"))}</button>
                      <span id="picked-file-name">${esc(T("noFileChosen"))}</span>
                    </div>
                  </div>
                  <div id="kb-source-webhook-block" style="display:none">
                    <label class="lang-select-wrap lang-select-wrap--bare">
                      <span class="lang-select-label">${esc(T("webhookProviderLabel"))}</span>
                      <select id="kb-webhook-provider" class="lang-select" aria-label="${esc(T("webhookProviderLabel"))}">
                        <option value="gitlab">${esc(T("webhookProviderGitlab"))}</option>
                        <option value="github">${esc(T("webhookProviderGithub"))}</option>
                      </select>
                    </label>
                    <label><span>${esc(T("webhookUrlLabel"))}</span><input id="kb-webhook-url" readonly /></label>
                    <label><span>${esc(T("webhookRepoUrlLabel"))}</span><input id="kb-webhook-repo-url" placeholder="${esc(T("webhookRepoUrlPlaceholder"))}" /></label>
                    <label><span>${esc(T("webhookBranchLabel"))}</span><input id="kb-webhook-ref" placeholder="${esc(T("webhookBranchPlaceholder"))}" autocomplete="off" /></label>
                    <label><span>${esc(T("webhookSecretLabel"))}</span><input id="kb-webhook-secret" readonly placeholder="${esc(T("webhookSecretPlaceholderGitlab"))}" /></label>
                    <p class="form-actions" style="margin-top:0.5rem">
                      <button type="button" class="secondary" id="kb-webhook-secret-eye">${esc(T("apiKeyEyeShow"))}</button>
                      <button type="button" class="secondary" id="kb-webhook-secret-regen">${esc(T("webhookSecretRegenerate"))}</button>
                    </p>
                  </div>
                  <label><span>${esc(T("folderPushUrlLabel"))}</span><input id="kb-folder-push-url" readonly /></label>
                  <label><span>${esc(T("webhookSecretLabel"))}</span><input id="kb-push-secret" readonly /></label>
                  <p class="form-actions" style="margin-top:0.5rem">
                    <button type="button" class="secondary" id="kb-push-secret-eye">${esc(T("apiKeyEyeShow"))}</button>
                    <button type="button" class="secondary" id="kb-push-secret-regen">${esc(T("webhookSecretRegenerate"))}</button>
                  </p>
                  <p class="muted small">${esc(T("folderPushHint"))}<br />${esc(T("folderPushHeaderHint"))}</p>
                </div>
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block" id="kb-upload-progress-block">
                  <h2 class="kb-detail-block-title">${esc(T("uploadProgress"))}</h2>
                  <progress id="upload-progress" value="0" max="100"></progress>
                  <span id="upload-progress-text" class="muted small" style="display:inline-block;margin-top:8px">0%</span>
                </div>
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block kb-detail-actions">
                  <button type="button" class="secondary" id="cancel-add-kb">${esc(T("back"))}</button>
                  <button type="submit" id="submit-btn">${esc(T("startBuild"))}</button>
                </div>
              </form>
            </div>
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

function maskSameLength(s) {
  const n = String(s || "").length;
  if (n <= 0) return "";
  return "*".repeat(n);
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
  let hasGitlabPat = false;
  let gitlabPatRaw = "";
  let hasGithubPat = false;
  let githubPatRaw = "";
  try {
    const data = await fetchJSON("/api/user/api-keys");
    apiKeys = data.keys || [];
  } catch (e) {
    setStatus(e.message, true);
  }
  try {
    const data = await fetchJSON("/api/user/gitlab-pat");
    hasGitlabPat = !!data?.has_pat;
    gitlabPatRaw = String(data?.pat || "");
  } catch {
    hasGitlabPat = false;
    gitlabPatRaw = "";
  }
  try {
    const data = await fetchJSON("/api/user/github-pat");
    hasGithubPat = !!data?.has_pat;
    githubPatRaw = String(data?.pat || "");
  } catch {
    hasGithubPat = false;
    githubPatRaw = "";
  }
  appEl.innerHTML = `
    <div class="app-shell">
      ${renderShellSidebar("profile")}
      <div class="shell-main">
        <div class="shell-content">
          ${renderTopbar(user, { title: T("accountTitle"), subtitle: T("accountSubtitle") })}
          <div class="shell-body profile-panel kb-detail-shell page-frame page-frame--wide">
            <div class="page-frame__inner">
              <div class="kb-detail-flow">
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title">${esc(T("navAccount"))}</h2>
                  <div class="profile-avatar-row">
                    <img id="profile-avatar-preview" class="profile-avatar-preview" src="${DEFAULT_AVATAR_URL}" alt="" />
                    <div class="profile-avatar-actions">
                      <p class="profile-username-line">${esc(user?.username || "")}</p>
                      <p class="muted small">${esc(T("avatarHint"))}</p>
                      <input type="file" id="profile-avatar-file" class="hidden-file-input" accept="image/png,image/jpeg,image/gif,image/webp,.png,.jpg,.jpeg,.gif,.webp" />
                      <div class="profile-actions-stack">
                        <button type="button" id="profile-avatar-upload-btn">${esc(T("changeAvatarBtn"))}</button>
                        <button type="button" class="secondary" id="profile-change-password">${esc(T("changePasswordNav"))}</button>
                      </div>
                      <p id="profile-msg" class="small muted profile-msg-line"></p>
                    </div>
                  </div>
                </div>
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title">${esc(T("preferencesTitle"))}</h2>
                  <div class="preferences-stack">
                    <div class="preference-row">
                      <span class="lang-select-label">${esc(T("interfaceLanguage"))}</span>
                      ${renderInterfaceLangSelect("profile-lang-select", true)}
                    </div>
                    <div class="preference-row">
                      <span class="lang-select-label">${esc(T("themeLabel"))}</span>
                      ${renderThemeSelect("profile-theme-select", true)}
                    </div>
                  </div>
                </div>
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title">${esc(T("apiKeysTitle"))}</h2>
                  <p class="muted small profile-apikey-hint">${esc(T("apiKeyMaxHint"))}</p>
                  <div id="api-key-list-wrap">${renderApiKeyList(apiKeys)}</div>
                  <div class="api-key-create">
                    <button type="button" id="api-key-create-btn">${esc(T("apiKeyCreate"))}</button>
                  </div>
                </div>
                <hr class="hr-soft hr-soft--kb-detail" />
                <div class="kb-detail-block">
                  <h2 class="kb-detail-block-title">${esc(T("repoPatSectionTitle"))}</h2>
                  <p class="muted small">${esc(T("repoPatSectionBlurb"))}</p>
                  <div class="repo-pat-subsection">
                    <h3 class="repo-pat-provider-title">${esc(T("gitlabPatSubTitle"))}</h3>
                    <p class="muted small">${esc(hasGitlabPat ? T("gitlabPatConfigured") : T("gitlabPatNotConfigured"))}</p>
                    <label><span>${esc(T("gitlabPatLabel"))}</span><input id="gitlab-pat-input" placeholder="${esc(T("gitlabPatPlaceholder"))}" value="${esc(maskSameLength(gitlabPatRaw))}" /></label>
                    <p class="form-actions" style="margin-top:0.75rem"><button type="button" id="gitlab-pat-save-btn">${esc(T("gitlabPatSave"))}</button></p>
                  </div>
                  <div class="repo-pat-subsection">
                    <h3 class="repo-pat-provider-title">${esc(T("githubPatSubTitle"))}</h3>
                    <p class="muted small">${esc(hasGithubPat ? T("githubPatConfigured") : T("githubPatNotConfigured"))}</p>
                    <label><span>${esc(T("githubPatLabel"))}</span><input id="github-pat-input" placeholder="${esc(T("githubPatPlaceholder"))}" value="${esc(maskSameLength(githubPatRaw))}" /></label>
                    <p class="form-actions" style="margin-top:0.75rem"><button type="button" id="github-pat-save-btn">${esc(T("githubPatSave"))}</button></p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  bindShellChrome(user);
  void refreshTopbarAvatar(user);
  await hydrateProfileAvatar(user);
  bindInterfaceLangSelect("profile-lang-select");
  bindThemeSelect("profile-theme-select");

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
    try {
      await fetchJSON("/api/user/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
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

  document.getElementById("gitlab-pat-save-btn")?.addEventListener("click", async () => {
    const patInputEl = document.getElementById("gitlab-pat-input");
    const entered = String(patInputEl?.value || "");
    const pat = entered === maskSameLength(gitlabPatRaw) ? gitlabPatRaw : entered.trim();
    try {
      await fetchJSON("/api/user/gitlab-pat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pat }),
      });
      setStatus(T("gitlabPatSaved"));
      await renderProfile(user);
    } catch (e) {
      setStatus(e.message, true);
    }
  });

  document.getElementById("github-pat-save-btn")?.addEventListener("click", async () => {
    const patInputEl = document.getElementById("github-pat-input");
    const entered = String(patInputEl?.value || "");
    const pat = entered === maskSameLength(githubPatRaw) ? githubPatRaw : entered.trim();
    try {
      await fetchJSON("/api/user/github-pat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pat }),
      });
      setStatus(T("githubPatSaved"));
      await renderProfile(user);
    } catch (e) {
      setStatus(e.message, true);
    }
  });
}

async function renderChangePassword(user) {
  appEl.innerHTML = `
    <div class="auth-screen" style="position:relative">
      <a href="#" class="auth-back-top link-button" id="cp-back">${esc(T("backToAccount"))}</a>
      <div class="auth-center-stack">
        <div class="auth-title-block">
          <h1 class="auth-title-heading">${esc(T("changePasswordTitle"))}</h1>
          <p class="muted small auth-title-sub">${esc(T("changePasswordSubtitle"))}</p>
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
      sessionStorage.setItem("ragret.flash", T("passwordChangedRelogin"));
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

function resetKbManageCorpusProgress() {
  const u = document.getElementById("kb-manage-corpus-upload-progress");
  const ut = document.getElementById("kb-manage-corpus-upload-text");
  if (u) u.value = 0;
  if (ut) ut.textContent = "0%";
}

function startManageCorpusUpload(file, kbName) {
  if (uploadXhr) uploadXhr.abort();
  manageCorpusUploadId = null;
  const uploadProgressEl = document.getElementById("kb-manage-corpus-upload-progress");
  const uploadProgressTextEl = document.getElementById("kb-manage-corpus-upload-text");
  if (uploadProgressEl) uploadProgressEl.value = 0;
  if (uploadProgressTextEl) uploadProgressTextEl.textContent = "0%";
  setStatus(T("uploadStart"));

  const fd = new FormData();
  fd.append("file", file);
  const xhr = new XMLHttpRequest();
  uploadXhr = xhr;
  xhr.open("POST", "/api/upload");
  xhr.setRequestHeader("Authorization", authHeaders().Authorization || "");
  xhr.upload.onprogress = (evt) => {
    if (!evt.lengthComputable || !uploadProgressEl || !uploadProgressTextEl) return;
    const p = Math.min(100, Math.round((evt.loaded / evt.total) * 100));
    uploadProgressEl.value = p;
    uploadProgressTextEl.textContent = `${p}%`;
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
      manageCorpusUploadId = data.upload_id;
      manageCorpusKbFor = kbName;
      if (uploadProgressEl) uploadProgressEl.value = 100;
      if (uploadProgressTextEl) uploadProgressTextEl.textContent = "100%";
      setStatus(T("uploadStagedManage"));
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
            <div class="shell-body page-frame">
              <div class="page-frame__inner page-frame__inner--narrow">
                <div class="page-stack__section">
                  <p class="error" style="margin:0 0 16px">${currentLang === "zh" ? "无法加载知识库" : "Knowledge base not found."}</p>
                  <button type="button" class="secondary" id="back-miss">${esc(T("backToPlaza"))}</button>
                </div>
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
          <div class="shell-body profile-panel kb-detail-shell page-frame page-frame--wide">
            <div class="page-frame__inner">
            <div class="kb-detail-flow">
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
            </div>
            </div>
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
            <div class="shell-body page-frame">
              <div class="page-frame__inner page-frame__inner--narrow">
                <div class="page-stack__section">
                  <p class="error" style="margin:0 0 16px">${currentLang === "zh" ? "无法加载知识库" : "Knowledge base not found."}</p>
                  <button type="button" class="secondary" id="back-miss">${esc(T("backToPlaza"))}</button>
                </div>
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
  const sourceType = String(meta?.source_type || "tar");
  const isWebhook = sourceType === "webhook";

  if (manageCorpusUploadId && manageCorpusKbFor && manageCorpusKbFor !== name) {
    manageCorpusUploadId = null;
  }

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

  const corpusUpdateSection =
    isOwner && !legacy && !isWebhook
      ? `<div class="kb-detail-block">
          <h2 class="kb-detail-block-title">${esc(T("kbUpdateCorpusTitle"))}</h2>
          <label><span>${esc(T("folderPushUrlLabel"))}</span><input id="kb-manage-folder-push-url" readonly value="${esc(String(meta?.folder_push_url || ""))}" /></label>
          <label><span>${esc(T("webhookSecretLabel"))}</span><input id="kb-manage-push-secret" readonly value="${esc(String(meta?.webhook_secret_masked || ""))}" /></label>
          <p class="form-actions" style="margin-top:0.75rem">
            <button type="button" class="secondary" id="kb-manage-push-secret-eye-btn">${esc(T("apiKeyEyeShow"))}</button>
            <button type="button" id="kb-manage-push-secret-regen-btn">${esc(T("webhookSecretRegenerate"))}</button>
          </p>
          <p class="muted small">${esc(T("folderPushHint"))}<br />${esc(T("folderPushHeaderHint"))}</p>
          <div class="archive-row manage-archive-row">
            <input type="file" id="kb-manage-archive" class="hidden-file-input" accept=".tar,.tgz,.tar.gz,.tar.bz2,.tar.xz,application/x-tar" />
            <button type="button" class="secondary" id="kb-manage-pick-archive">${esc(T("chooseFile"))}</button>
            <span class="picked-file muted small" id="kb-manage-picked-file-name">${esc(T("noFileChosen"))}</span>
          </div>
          <label class="progress-label"><span>${esc(T("uploadProgress"))}</span></label>
          <progress id="kb-manage-corpus-upload-progress" value="0" max="100"></progress>
          <span id="kb-manage-corpus-upload-text">0%</span>
          <p class="muted small">${esc(T("taskEnqueuedHint"))}</p>
          <p class="form-actions" style="margin-top:0.75rem"><button type="button" id="kb-manage-rebuild-btn">${esc(T("updateCorpusRebuild"))}</button></p>
        </div>`
      : "";
  const webhookManageSection =
    isOwner && !legacy && isWebhook
      ? `<div class="kb-detail-block">
          <h2 class="kb-detail-block-title">${esc(T("kbUpdateCorpusTitle"))}</h2>
          <p class="muted small">${esc(T("webhookProviderLabel"))}: ${esc(String(meta?.webhook_provider || "").toLowerCase() === "github" ? T("webhookProviderGithub") : T("webhookProviderGitlab"))}</p>
          <label><span>${esc(T("folderPushUrlLabel"))}</span><input id="kb-manage-folder-push-url" readonly value="${esc(String(meta?.folder_push_url || ""))}" /></label>
          <p class="muted small">${esc(T("folderPushHint"))}</p>
          <label><span>${esc(T("webhookUrlLabel"))}</span><input id="kb-manage-webhook-url" readonly value="${esc(String(meta?.webhook_url || ""))}" /></label>
          <label><span>${esc(T("webhookSecretLabel"))}</span><input id="kb-manage-webhook-secret" readonly value="${esc(String(meta?.webhook_secret_masked || ""))}" placeholder="${esc(String(meta?.webhook_provider || "").toLowerCase() === "github" ? T("webhookSecretPlaceholderGithub") : T("webhookSecretPlaceholderGitlab"))}" /></label>
          <p class="form-actions" style="margin-top:0.75rem">
            <button type="button" class="secondary" id="kb-manage-webhook-eye-btn">${esc(T("apiKeyEyeShow"))}</button>
            <button type="button" id="kb-manage-webhook-regen-btn">${esc(T("webhookSecretRegenerate"))}</button>
          </p>
          <label><span>${esc(T("webhookRepoUrlLabel"))}</span><input id="kb-manage-webhook-repo-url" value="${esc(String(meta?.webhook_repo_url || ""))}" placeholder="${esc(T("webhookRepoUrlPlaceholder"))}" /></label>
          <label><span>${esc(T("webhookBranchLabel"))}</span><input id="kb-manage-webhook-ref" value="${esc(String(meta?.webhook_ref || ""))}" placeholder="${esc(T("webhookBranchPlaceholder"))}" autocomplete="off" /></label>
          <p class="muted small" style="margin-top:0.5rem">${esc(T("webhookManualPullHint"))}</p>
          <p class="form-actions" style="margin-top:0.75rem">
            <button type="button" class="secondary" id="kb-manage-webhook-save-repo-btn">${esc(T("webhookSaveRepo"))}</button>
            <button type="button" id="kb-manage-webhook-pull-btn">${esc(T("webhookManualPull"))}</button>
          </p>
        </div>`
      : "";

  appEl.innerHTML = `
    <div class="app-shell">
      ${renderShellSidebar(null)}
      <div class="shell-main">
        <div class="shell-content">
          ${renderKbDetailTopbar(user, { name, subtitle: T("kbManageFixedSubtitle") })}
          <div class="shell-body profile-panel kb-detail-shell page-frame page-frame--wide">
            <div class="page-frame__inner">
            <div class="kb-detail-flow">
                ${nameSection ? `${nameSection}<hr class="hr-soft hr-soft--kb-detail" />` : ""}
                ${iconSection}
                ${iconSection ? `<hr class="hr-soft hr-soft--kb-detail" />` : ""}
                ${visibilitySection}
                ${!legacy ? `<hr class="hr-soft hr-soft--kb-detail" />` : ""}
                ${descSection}
                <hr class="hr-soft hr-soft--kb-detail" />
                ${readmeSection}
                ${membersSection ? `<hr class="hr-soft hr-soft--kb-detail" />${membersSection}` : ""}
                ${corpusUpdateSection ? `<hr class="hr-soft hr-soft--kb-detail" />${corpusUpdateSection}` : ""}
                ${webhookManageSection ? `<hr class="hr-soft hr-soft--kb-detail" />${webhookManageSection}` : ""}
                ${canDelete ? `<hr class="hr-soft hr-soft--kb-detail" /><div class="kb-detail-block kb-detail-block--danger"><button type="button" class="danger" id="del-kb-btn">${esc(T("deleteKb"))}</button></div>` : ""}
            </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  bindShellChrome(user);
  void refreshTopbarAvatar(user);
  void ensureKbIconOnImg(document.getElementById("kb-icon-edit-preview"), name);

  if (corpusUpdateSection) {
    let pushSecretRaw = "";
    let pushSecretVisible = false;
    const pushSecretInput = document.getElementById("kb-manage-push-secret");
    const pushSecretEyeBtn = document.getElementById("kb-manage-push-secret-eye-btn");
    const refreshPushSecret = () => {
      if (!pushSecretInput) return;
      pushSecretInput.value = pushSecretVisible
        ? pushSecretRaw
        : "*".repeat(String(pushSecretRaw || "").length || Number(meta?.webhook_secret_len || 0));
      if (pushSecretEyeBtn) pushSecretEyeBtn.textContent = pushSecretVisible ? T("apiKeyEyeHide") : T("apiKeyEyeShow");
    };
    pushSecretEyeBtn?.addEventListener("click", async () => {
      if (!pushSecretRaw) {
        try {
          const d = await fetchJSON(`/api/kb/${encodeURIComponent(name)}/webhook-secret`);
          pushSecretRaw = String(d?.secret || "");
        } catch (e) {
          setStatus(e.message, true);
          return;
        }
      }
      pushSecretVisible = !pushSecretVisible;
      refreshPushSecret();
    });
    document.getElementById("kb-manage-push-secret-regen-btn")?.addEventListener("click", async () => {
      try {
        await fetchJSON(`/api/kb/${encodeURIComponent(name)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ regenerate_webhook_secret: true }),
        });
        setStatus(T("saveDone"));
        await render();
      } catch (e) {
        setStatus(e.message, true);
      }
    });
    const pickedEl = document.getElementById("kb-manage-picked-file-name");
    if (manageCorpusUploadId && manageCorpusKbFor === name) {
      if (pickedEl) pickedEl.textContent = T("kbManageCorpusStaged");
      const u = document.getElementById("kb-manage-corpus-upload-progress");
      const ut = document.getElementById("kb-manage-corpus-upload-text");
      if (u) u.value = 100;
      if (ut) ut.textContent = "100%";
    } else {
      resetKbManageCorpusProgress();
    }
    document.getElementById("kb-manage-pick-archive")?.addEventListener("click", () => {
      document.getElementById("kb-manage-archive")?.click();
    });
    const archiveInput = document.getElementById("kb-manage-archive");
    archiveInput?.addEventListener("change", () => {
      const f = archiveInput.files?.[0] || null;
      if (pickedEl) pickedEl.textContent = f?.name || T("noFileChosen");
      if (f) startManageCorpusUpload(f, name);
      else {
        if (uploadXhr) uploadXhr.abort();
        manageCorpusUploadId = null;
        manageCorpusKbFor = null;
        resetKbManageCorpusProgress();
        if (pickedEl) pickedEl.textContent = T("noFileChosen");
      }
    });
    document.getElementById("kb-manage-rebuild-btn")?.addEventListener("click", async () => {
      if (!manageCorpusUploadId || manageCorpusKbFor !== name) {
        setStatus(T("requireStagedManage"), true);
        return;
      }
      const description =
        (document.getElementById("kb-desc-edit")?.value ?? "").trim() || String(meta.description || "").trim();
      if (!description) {
        setStatus(T("requireDescriptionForRebuild"), true);
        return;
      }
      const readmeMd = document.getElementById("kb-readme-edit")?.value ?? String(meta.readme_md || "");
      const btn = document.getElementById("kb-manage-rebuild-btn");
      btn.disabled = true;
      try {
        const start = await fetchJSON("/api/indexes/build", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name,
            description,
            readme_md: String(readmeMd).trim(),
            upload_id: manageCorpusUploadId,
            is_public: !!meta.is_public,
            icon: String(meta.icon || "book"),
          }),
        });
        manageCorpusUploadId = null;
        manageCorpusKbFor = null;
        archiveInput.value = "";
        if (pickedEl) pickedEl.textContent = T("noFileChosen");
        resetKbManageCorpusProgress();
        setStatus(T("taskEnqueued"));
        go(`/tasks/${encodeURIComponent(start.job_id)}`);
      } catch (err) {
        setStatus(err.message, true);
      } finally {
        btn.disabled = false;
      }
    });
  }
  if (webhookManageSection) {
    let webhookManageSecretRaw = "";
    let webhookManageSecretVisible = false;
    const secretInput = document.getElementById("kb-manage-webhook-secret");
    const eyeBtn = document.getElementById("kb-manage-webhook-eye-btn");
    const refreshSecret = () => {
      if (!secretInput) return;
      secretInput.value = webhookManageSecretVisible
        ? webhookManageSecretRaw
        : "*".repeat(String(webhookManageSecretRaw || "").length || Number(meta?.webhook_secret_len || 0));
      if (eyeBtn) eyeBtn.textContent = webhookManageSecretVisible ? T("apiKeyEyeHide") : T("apiKeyEyeShow");
    };
    eyeBtn?.addEventListener("click", async () => {
      if (!webhookManageSecretRaw) {
        try {
          const d = await fetchJSON(`/api/kb/${encodeURIComponent(name)}/webhook-secret`);
          webhookManageSecretRaw = String(d?.secret || "");
        } catch (e) {
          setStatus(e.message, true);
          return;
        }
      }
      webhookManageSecretVisible = !webhookManageSecretVisible;
      refreshSecret();
    });
    document.getElementById("kb-manage-webhook-regen-btn")?.addEventListener("click", async () => {
      try {
        await fetchJSON(`/api/kb/${encodeURIComponent(name)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ regenerate_webhook_secret: true }),
        });
        webhookManageSecretRaw = "";
        webhookManageSecretVisible = false;
        setStatus(T("saveDone"));
        await render();
      } catch (e) {
        setStatus(e.message, true);
      }
    });
    document.getElementById("kb-manage-webhook-save-repo-btn")?.addEventListener("click", async () => {
      const repo_url = String(document.getElementById("kb-manage-webhook-repo-url")?.value || "").trim();
      const ref = String(document.getElementById("kb-manage-webhook-ref")?.value || "").trim();
      const btn = document.getElementById("kb-manage-webhook-save-repo-btn");
      if (btn) btn.disabled = true;
      try {
        if (!ref) {
          setStatus(T("requireWebhookBranch"), true);
          return;
        }
        await fetchJSON(`/api/kb/${encodeURIComponent(name)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ repo_url, ref }),
        });
        setStatus(T("saveDone"));
        await render();
      } catch (e) {
        setStatus(e.message, true);
      } finally {
        if (btn) btn.disabled = false;
      }
    });
    document.getElementById("kb-manage-webhook-pull-btn")?.addEventListener("click", async () => {
      const btn = document.getElementById("kb-manage-webhook-pull-btn");
      if (btn) btn.disabled = true;
      try {
        const start = await fetchJSON(`/api/kb/${encodeURIComponent(name)}/webhook-pull`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        setStatus(T("taskEnqueued"));
        if (start?.job_id) go(`/tasks/${encodeURIComponent(start.job_id)}`);
        else await render();
      } catch (e) {
        setStatus(e.message, true);
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  }

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
      if (newName !== name && isWebhook) {
        if (!(await showConfirmDialog(T("renameKbWebhookWarning")))) return;
      }
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

async function renderTasksList(user) {
  let jobs = [];
  try {
    const data = await fetchJSON("/api/jobs");
    jobs = data.jobs || [];
  } catch (e) {
    setStatus(e.message, true);
  }

  const renderTasksGrid = (query) => {
    const q = String(query || "").trim().toLowerCase();
    const rows = !q
      ? jobs
      : jobs.filter((j) => {
          const n = String(j.kb_name || "").toLowerCase();
          const st = String(j.status || "").toLowerCase();
          const ph = String(j.phase || "").toLowerCase();
          if (n.includes(q) || st.includes(q) || ph.includes(q)) return true;
          if (currentLang === "zh") {
            if (q.includes("排队") && st === "queued") return true;
            if ((q.includes("运行") || q.includes("进行")) && st === "running") return true;
            if (q.includes("创建") && String(j.op) !== "update") return true;
            if (q.includes("更新") && String(j.op) === "update") return true;
          }
          return false;
        });
    return rows.length === 0
      ? `<p class="muted empty-hint">${currentLang === "zh" ? "没有匹配结果" : "No matches found."}</p>`
      : renderTaskCardsInnerHtml(rows);
  };

  appEl.innerHTML = `
    <div class="app-shell">
      ${renderShellSidebar("tasks")}
      <div class="shell-main">
        <div class="shell-content">
          ${renderTopbar(user, { title: T("tasksTitle"), subtitle: T("tasksSubtitle") })}
          <div class="shell-body">
            <label class="kb-list-search">
              <span>${esc(T("kbListSearch"))}</span>
              <input id="tasks-search-input" placeholder="${esc(T("tasksSearchPlaceholder"))}" />
            </label>
            <div class="kb-grid" id="task-grid">
              ${jobs.length === 0 ? `<p class="muted empty-hint">${esc(T("tasksEmpty"))}</p>` : renderTasksGrid("")}
            </div>
          </div>
        </div>
      </div>
    </div>`;
  bindShellChrome(user);
  void refreshTopbarAvatar(user);
  bindTaskGridNavigation();
  document.getElementById("tasks-search-input")?.addEventListener("input", (e) => {
    const grid = document.getElementById("task-grid");
    if (!grid) return;
    grid.innerHTML = renderTasksGrid(e.target.value);
    bindTaskGridNavigation();
  });
}

async function renderTaskDetail(user, jobId) {
  const jidEnc = encodeURIComponent(jobId);
  let headTitle = T("taskDetailTitle");
  let headSub = jobId;
  try {
    const jPeek = await fetchJSON(`/api/jobs/${jidEnc}`);
    if (jPeek?.kb_name) headTitle = String(jPeek.kb_name);
  } catch (e) {
    if (isJobGoneError(e)) {
      setStatus(T("taskNotFoundOrDone"), true);
      go("/tasks");
      return;
    }
    /* network/other: continue with default title */
  }

  appEl.innerHTML = `
    <div class="app-shell">
      ${renderShellSidebar("tasks")}
      <div class="shell-main">
        <div class="shell-content">
          ${renderKbDetailTopbar(user, { name: headTitle, subtitle: headSub })}
          <div class="shell-body profile-panel kb-detail-shell page-frame page-frame--wide">
            <div class="page-frame__inner">
            <div class="kb-detail-flow">
              <div class="kb-detail-block">
                <h2 class="kb-detail-block-title">${esc(T("taskStatusTitle"))}</h2>
                <p class="kb-detail-desc-text" id="task-status-line">…</p>
              </div>
              <hr class="hr-soft hr-soft--kb-detail" />
              <div class="kb-detail-block">
                <h2 class="kb-detail-block-title">${esc(T("taskProgressTitle"))}</h2>
                <label class="progress-label"><span>${esc(T("buildProgress"))}</span></label>
                <progress id="task-detail-progress" value="0" max="100"></progress>
                <p id="task-detail-detail" class="muted small kb-detail-detail-line"></p>
              </div>
              <hr class="hr-soft hr-soft--kb-detail" />
              <div class="kb-detail-block" id="task-error-block" hidden>
                <p id="task-detail-error" class="error small"></p>
              </div>
              <hr class="hr-soft hr-soft--kb-detail" id="task-error-hr" hidden />
              <div class="kb-detail-block kb-detail-actions">
                <button type="button" class="secondary" id="task-back-btn">${esc(T("taskBackToList"))}</button>
                <button type="button" class="danger" id="task-cancel-btn">${esc(T("taskCancel"))}</button>
              </div>
            </div>
            </div>
          </div>
        </div>
      </div>
    </div>`;
  bindShellChrome(user);
  void refreshTopbarAvatar(user);
  const prog = document.getElementById("task-detail-progress");
  const line = document.getElementById("task-status-line");
  const det = document.getElementById("task-detail-detail");
  const errEl = document.getElementById("task-detail-error");
  const errBlock = document.getElementById("task-error-block");
  const errHr = document.getElementById("task-error-hr");
  const backBtn = document.getElementById("task-back-btn");
  const cancelBtn = document.getElementById("task-cancel-btn");
  const topbarName = document.querySelector(".topbar--detail .topbar-text h1");
  let lastJobSig = "";
  let buildDoneToastShown = false;
  let lastKbName = "";

  const refresh = (j) => {
    if (!j) return;
    if (j.kb_name) lastKbName = String(j.kb_name);
    const sig = `${j.status}|${j.phase}|${j.percent}|${j.detail || ""}|${j.error || ""}`;
    if (sig === lastJobSig) return;
    lastJobSig = sig;
    if (prog) prog.value = Number(j.percent || 0);
    const opLabel = j.op === "update" ? T("taskOpUpdate") : T("taskOpCreate");
    if (topbarName && j.kb_name) topbarName.textContent = String(j.kb_name);
    if (line) {
      line.textContent = `${opLabel} · ${j.status} · ${phaseText(j.phase)} · ${j.percent || 0}%`;
    }
    if (det) det.textContent = j.detail || "";
    const done = ["done", "error", "cancelled"].includes(String(j.status));
    if (cancelBtn) cancelBtn.style.display = done ? "none" : "";
    const isErr = String(j.status) === "error";
    if (errEl && errBlock && errHr) {
      if (isErr && j.error) {
        errBlock.hidden = false;
        errHr.hidden = false;
        errEl.textContent = j.error;
      } else {
        errBlock.hidden = true;
        errHr.hidden = true;
        errEl.textContent = "";
      }
    }
    if (String(j.status) === "done" && !buildDoneToastShown) {
      buildDoneToastShown = true;
      setStatus(T("buildDone", j.result?.name || j.kb_name || ""));
    }
  };

  const leaveTaskDetailToList = (j) => {
    const st = String(j?.status || "");
    if (st === "error" && j?.error) setStatus(String(j.error), true);
    else if (st === "cancelled") setStatus(T("taskCancelledRemoved"));
    go("/tasks");
  };

  backBtn?.addEventListener("click", () => go("/tasks"));

  cancelBtn?.addEventListener("click", async () => {
    if (!(await showConfirmDialog(T("taskCancelConfirm")))) return;
    try {
      await fetchJSON(`/api/jobs/${jidEnc}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      try {
        const j = await fetchJSON(`/api/jobs/${jidEnc}`);
        refresh(j);
        if (String(j.status) === "cancelled") {
          setStatus(T("taskCancelledRemoved"));
          go("/tasks");
          return;
        }
      } catch {
        setStatus(T("taskCancelledRemoved"));
        go("/tasks");
      }
    } catch (e) {
      setStatus(e.message, true);
    }
  });

  try {
    let j0 = await fetchJSON(`/api/jobs/${jidEnc}`);
    refresh(j0);
    if (["done", "error", "cancelled"].includes(String(j0.status))) {
      leaveTaskDetailToList(j0);
      return;
    }
    try {
      const ended = await pollJobUntilTerminal(jobId, refresh);
      if (ended._jobRemoved) {
        const n = String(lastKbName || ended.result?.name || "").trim();
        let kbExists = false;
        if (n) {
          try {
            const kb = await fetchJSON(`/api/kb/${encodeURIComponent(n)}`);
            kbExists = !!kb?.ok;
          } catch {
            kbExists = false;
          }
        }
        if (!buildDoneToastShown) {
          buildDoneToastShown = true;
          if (kbExists && n) setStatus(T("buildDone", n));
          else setStatus(T("taskNotFoundOrDone"), true);
        }
        go("/tasks");
        return;
      }
      refresh(ended);
      if (["done", "error", "cancelled"].includes(String(ended.status))) {
        leaveTaskDetailToList(ended);
      }
    } catch (e) {
      if (isJobGoneError(e)) {
        setStatus(T("taskNotFoundOrDone"), true);
        go("/tasks");
        return;
      }
      throw e;
    }
  } catch (e) {
    if (isJobGoneError(e)) {
      setStatus(T("taskNotFoundOrDone"), true);
      go("/tasks");
      return;
    }
    if (errBlock && errHr && errEl) {
      errBlock.hidden = false;
      errHr.hidden = false;
      errEl.textContent = e.message;
    }
    if (cancelBtn) cancelBtn.style.display = "none";
  }
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
    sessionStorage.setItem("ragret.returnTo", location.pathname + location.search);
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

  if (route.type === "quickQa") {
    await renderQuickQa(user);
    return;
  }

  if (route.type === "addKb") {
    await renderAddKb(user);
    return;
  }

  if (route.type === "tasks") {
    await renderTasksList(user);
    return;
  }

  if (route.type === "taskDetail") {
    await renderTaskDetail(user, route.jobId);
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
if (restored?.theme === "light" || restored?.theme === "dark") {
  currentTheme = restored.theme;
} else {
  try {
    const ut = localStorage.getItem(UI_THEME_KEY);
    if (ut === "light" || ut === "dark") currentTheme = ut;
  } catch {
    /* ignore */
  }
}
try {
  localStorage.setItem(UI_LANG_KEY, currentLang);
  localStorage.setItem(UI_THEME_KEY, currentTheme);
} catch {
  /* ignore */
}
applyTheme();
if (restored?.stagedUploadId) stagedUploadId = restored.stagedUploadId;

loadUiConfig()
  .then(() => render())
  .catch((e) => setStatus(e.message, true));

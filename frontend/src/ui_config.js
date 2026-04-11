import yaml from "js-yaml";

export const UI_CONFIG_DEFAULT = {
  page_title: "RAGret Panel",
  favicon_url: "",
  login_title: "RAGret Panel",
  login_logo_url: "",
};

export const uiConfig = { ...UI_CONFIG_DEFAULT };

export function normalizeUiConfigRaw(raw) {
  const r = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  const login = r.login && typeof r.login === "object" && !Array.isArray(r.login) ? r.login : {};
  const page_title = String(r.page_title || "").trim() || UI_CONFIG_DEFAULT.page_title;
  const favicon_url = String(r.favicon_url || r.favicon || "").trim();
  const login_title = String(login.title || page_title).trim() || page_title;
  const login_logo_url = String(login.logo_url || "").trim();
  return {
    page_title,
    favicon_url,
    login_title,
    login_logo_url,
  };
}

export function applyUiConfigToDocument() {
  document.title = uiConfig.page_title;
  const href = uiConfig.favicon_url;
  let link = document.querySelector('link[rel="icon"]');
  if (!href) {
    link?.remove();
    return;
  }
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  link.href = href;
}

/** Call once at startup; reads /custom-config.yml from static root (frontend/public → build output). */
export async function loadUiConfig() {
  Object.assign(uiConfig, UI_CONFIG_DEFAULT);
  try {
    const res = await fetch("/custom-config.yml", { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    const text = await res.text();
    const raw = yaml.load(text);
    Object.assign(uiConfig, normalizeUiConfigRaw(raw));
  } catch {
    /* keep defaults */
  }
  applyUiConfigToDocument();
}

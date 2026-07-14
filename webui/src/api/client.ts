/* Typed fetch wrappers for the daemon's REST API. Same-origin only — the
   daemon binds 127.0.0.1 and nothing here may ever address another host. */

import type {
  ChatResponse,
  ChatTurn,
  ExportConfig,
  NoteMeta,
  SearchHit,
  Settings,
  Status,
  Template,
} from "./types";

/* Thrown for non-2xx responses; `status` lets callers branch on 409/413/503
   and `detail` carries the server's error message when it sent one. */
export class ApiError extends Error {
  status: number;
  detail: string | null;

  constructor(status: number, detail: string | null) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const resp = await fetch(path, init);
  if (!resp.ok) {
    let detail: string | null = null;
    try {
      detail = (await resp.json()).detail ?? null;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp;
}

async function getJson<T>(path: string): Promise<T> {
  return (await request(path)).json();
}

async function sendJson<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await request(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return resp.json();
}

const note = (name: string) => `/api/notes/${encodeURIComponent(name)}`;
const archived = (name: string) => `/api/archived/${encodeURIComponent(name)}`;

export const api = {
  // -- status / session -------------------------------------------------
  status: () => getJson<Status>("/api/status"),
  templates: () => getJson<Template[]>("/api/templates"),
  recordStart: (title: string | null, template: string | null) =>
    sendJson<void>("POST", "/api/record/start", { title, template }),
  recordStop: () => sendJson<void>("POST", "/api/record/stop", {}),
  watchStart: () => sendJson<void>("POST", "/api/watch/start", {}),
  watchStop: () => sendJson<void>("POST", "/api/watch/stop", {}),
  watchRespond: (accept: boolean) =>
    sendJson<void>("POST", "/api/watch/respond", { accept }),
  getScratchpad: () => getJson<{ content: string }>("/api/session/scratchpad"),
  putScratchpad: (content: string) =>
    sendJson<void>("PUT", "/api/session/scratchpad", { content }),

  // -- notes -------------------------------------------------------------
  notes: () => getJson<NoteMeta[]>("/api/notes"),
  noteContent: async (name: string) => (await request(note(name))).text(),
  putNote: (name: string, content: string) =>
    sendJson<{ ok: boolean; title: string }>("PUT", note(name), { content }),
  toggleTask: (name: string, taskIndex: number, checked: boolean) =>
    sendJson<void>("PATCH", note(name), { task_index: taskIndex, checked }),
  deleteNote: (name: string) => sendJson<void>("DELETE", note(name)),
  archiveNote: (name: string) => sendJson<void>("POST", `${note(name)}/archive`),
  archivedNotes: () => getJson<NoteMeta[]>("/api/archived"),
  restoreNote: (name: string) => sendJson<void>("POST", `${archived(name)}/restore`),
  deleteArchived: (name: string) => sendJson<void>("DELETE", archived(name)),
  search: (q: string) =>
    getJson<SearchHit[]>(`/api/search?q=${encodeURIComponent(q)}`),

  // -- chat / exports / settings ------------------------------------------
  chat: (question: string, history: ChatTurn[]) =>
    sendJson<ChatResponse>("POST", "/api/chat", { question, history }),
  exportConfig: () => getJson<ExportConfig>("/api/export/config"),
  copyToVault: (name: string) => sendJson<void>("POST", `${note(name)}/vault`),
  followup: (name: string) =>
    sendJson<{ draft: string }>("POST", `${note(name)}/followup`),
  /* The one action that sends data off this machine. Only WP8's confirmed
     "Push to Notion…" button may call this — never anything automatic. */
  pushToNotion: (name: string) =>
    sendJson<{ ok: boolean; url: string }>("POST", `${note(name)}/notion`),
  settings: () => getJson<Settings>("/api/settings"),
  connectObsidian: (vault: string) =>
    sendJson<Settings>("PUT", "/api/settings/obsidian", { vault }),
  disconnectObsidian: () => sendJson<Settings>("DELETE", "/api/settings/obsidian"),
  /* Pure disk write on the daemon side: connecting must never trigger any
     network call. Omit `token` to keep the one already on file. */
  connectNotion: (databaseId: string, token?: string) =>
    sendJson<Settings>(
      "PUT",
      "/api/settings/notion",
      token ? { token, database_id: databaseId } : { database_id: databaseId },
    ),
  disconnectNotion: () => sendJson<Settings>("DELETE", "/api/settings/notion"),
};

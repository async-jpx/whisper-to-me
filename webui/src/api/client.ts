/* Typed fetch wrappers for the daemon's REST API. Same-origin only — the
   daemon binds 127.0.0.1 and nothing here may ever address another host. */

import type {
  AudioPeaks,
  ExportConfig,
  NewTemplate,
  NoteLine,
  NoteMeta,
  SearchResult,
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

export async function getJson<T>(path: string): Promise<T> {
  return (await request(path)).json();
}

export async function sendJson<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await request(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return resp.json();
}

/* The note-list fields beyond name/title/modified arrived with the Hush
   redesign; an older daemon omits them, so they are defaulted here once and
   trusted everywhere else. */
type WireNoteMeta = Pick<NoteMeta, "name" | "title" | "modified"> & Partial<NoteMeta>;

function parseNoteMeta(w: WireNoteMeta): NoteMeta {
  return {
    name: w.name,
    title: w.title,
    modified: w.modified,
    date: w.date ?? null,
    app: w.app ?? null,
    has_audio: w.has_audio ?? false,
    duration_s: w.duration_s ?? null,
  };
}

type WireSettings = Omit<Settings, "recording" | "templates" | "detection"> &
  Partial<Pick<Settings, "recording" | "templates" | "detection">>;

function parseSettings(w: WireSettings): Settings {
  return {
    ...w,
    recording: w.recording ?? { keep_audio: false },
    templates: w.templates ?? { default: null },
    detection: w.detection ?? { ignored_apps: [] },
  };
}

export const note = (name: string) => `/api/notes/${encodeURIComponent(name)}`;
const archived = (name: string) => `/api/archived/${encodeURIComponent(name)}`;
const template = (name: string) => `/api/templates/${encodeURIComponent(name)}`;

export const api = {
  // -- status / session -------------------------------------------------
  status: () => getJson<Status>("/api/status"),
  templates: () => getJson<Template[]>("/api/templates"),
  createTemplate: (t: NewTemplate) => sendJson<Template>("POST", "/api/templates", t),
  deleteTemplate: async (name: string) => {
    await request(template(name), { method: "DELETE" }); // 204: no body to parse
  },
  favoriteTemplate: (name: string, favorite: boolean) =>
    sendJson<Template>("PUT", `${template(name)}/favorite`, { favorite }),
  setDefaultTemplate: async (name: string | null) =>
    parseSettings(
      await sendJson<WireSettings>("PUT", "/api/settings/default-template", { name }),
    ),
  recordStart: (title: string | null, template: string | null) =>
    sendJson<void>("POST", "/api/record/start", { title, template }),
  recordStop: () => sendJson<void>("POST", "/api/record/stop", {}),
  getScratchpad: () => getJson<{ content: string }>("/api/session/scratchpad"),
  putScratchpad: (content: string) =>
    sendJson<void>("PUT", "/api/session/scratchpad", { content }),

  // -- notes -------------------------------------------------------------
  notes: async () => (await getJson<WireNoteMeta[]>("/api/notes")).map(parseNoteMeta),
  noteContent: async (name: string) => (await request(note(name))).text(),
  putNote: (name: string, content: string) =>
    sendJson<{ ok: boolean; title: string }>("PUT", note(name), { content }),
  toggleTask: (name: string, taskIndex: number, checked: boolean) =>
    sendJson<void>("PATCH", note(name), { task_index: taskIndex, checked }),
  deleteNote: (name: string) => sendJson<void>("DELETE", note(name)),
  archiveNote: (name: string) => sendJson<void>("POST", `${note(name)}/archive`),
  archivedNotes: async () => (await getJson<WireNoteMeta[]>("/api/archived")).map(parseNoteMeta),
  restoreNote: (name: string) => sendJson<void>("POST", `${archived(name)}/restore`),
  deleteArchived: (name: string) => sendJson<void>("DELETE", archived(name)),
  transcript: async (name: string) =>
    (await getJson<{ lines: NoteLine[] }>(`${note(name)}/transcript`)).lines,
  /* For <audio src>: the daemon answers Range requests with 206, and every
     fetch counts as "played" for the 30-day retention. */
  audioUrl: (name: string) => `${note(name)}/audio`,
  audioPeaks: (name: string) => getJson<AudioPeaks>(`${note(name)}/audio/peaks`),
  deleteAudio: async (name: string) => {
    await request(`${note(name)}/audio`, { method: "DELETE" });
  },
  search: (q: string) =>
    getJson<SearchResult[]>(`/api/search?q=${encodeURIComponent(q)}`),

  // -- exports / settings ---------------------------------------------------
  // (chat streams over /api/chat/stream via the AI SDK transport in ChatView)
  exportConfig: () => getJson<ExportConfig>("/api/export/config"),
  copyToVault: (name: string) => sendJson<void>("POST", `${note(name)}/vault`),
  followup: (name: string) =>
    sendJson<{ draft: string }>("POST", `${note(name)}/followup`),
  /* The one action that sends data off this machine. Only WP8's confirmed
     "Push to Notion…" button may call this — never anything automatic. */
  pushToNotion: (name: string) =>
    sendJson<{ ok: boolean; url: string }>("POST", `${note(name)}/notion`),
  settings: async () => parseSettings(await getJson<WireSettings>("/api/settings")),
  /* The contract pins only the request body, so re-read the full state. */
  setKeepAudio: async (keepAudio: boolean) => {
    await request("/api/settings/recording", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keep_audio: keepAudio }),
    });
    return parseSettings(await getJson<WireSettings>("/api/settings"));
  },
  setIgnoredApps: async (ignoredApps: string[]) =>
    parseSettings(
      await sendJson<WireSettings>("PUT", "/api/settings/detection", {
        ignored_apps: ignoredApps,
      }),
    ),
  connectObsidian: async (vault: string) =>
    parseSettings(await sendJson<WireSettings>("PUT", "/api/settings/obsidian", { vault })),
  disconnectObsidian: async () =>
    parseSettings(await sendJson<WireSettings>("DELETE", "/api/settings/obsidian")),
  /* Pure disk write on the daemon side: connecting must never trigger any
     network call. Omit `token` to keep the one already on file. */
  connectNotion: async (databaseId: string, token?: string) =>
    parseSettings(
      await sendJson<WireSettings>(
        "PUT",
        "/api/settings/notion",
        token ? { token, database_id: databaseId } : { database_id: databaseId },
      ),
    ),
  disconnectNotion: async () =>
    parseSettings(await sendJson<WireSettings>("DELETE", "/api/settings/notion")),
};

/* Start/stop a recording from any surface (home composer, top bar). The
   daemon's status frame is the truth: recordPending only bridges the gap
   until it arrives, with a failsafe in case the socket is dead. */

import { api, ApiError } from "../api/client";
import { isActive } from "../api/types";
import { useStore } from "../store";
import { resyncStatus } from "../ws";

function armPending(): boolean {
  const s = useStore.getState();
  if (s.recordPending) return false;
  s.setRecordPending(true);
  setTimeout(() => {
    if (useStore.getState().recordPending) {
      useStore.getState().setRecordPending(false);
      void resyncStatus();
    }
  }, 5000);
  return true;
}

export async function startRecording(title: string | null, template: string | null): Promise<void> {
  if (!armPending()) return;
  const s = useStore.getState();
  s.clearTranscript();
  s.openLive();
  try {
    await api.recordStart(title, template);
  } catch (err) {
    s.setRecordPending(false);
    s.toast(
      err instanceof ApiError && err.status === 409
        ? "Another session is still running. Try again in a moment."
        : "Could not start recording.",
      "error",
    );
    await resyncStatus();
    if (!isActive(useStore.getState().status)) void useStore.getState().navigate("home");
  }
}

export async function stopRecording(): Promise<void> {
  if (!armPending()) return;
  try {
    await api.recordStop();
  } catch {
    useStore.getState().setRecordPending(false);
    useStore.getState().toast("Could not stop the recording.", "error");
    await resyncStatus();
  }
}

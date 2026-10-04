/* Main content area: top bar + the active view. Each view lives in its own
   file; add a view by extending store.View and the switch below. */

import { lazy, Suspense } from "react";
import { useStore, type View } from "../store";
import { HomeView } from "./HomeView";
import { LivePane } from "./LivePane";
import { NoteContainer } from "./NoteContainer";
import { SettingsPage } from "./SettingsPage";
import { TemplatesPage } from "./TemplatesPage";
import { TopBar } from "./TopBar";

// The AI SDK only loads when the Ask view first opens (keeps it out of the
// main bundle).
const ChatView = lazy(() => import("./ChatView").then((m) => ({ default: m.ChatView })));

function ViewBody({ view }: { view: View }) {
  switch (view) {
    case "home":
      return <HomeView />;
    case "live":
      return <LivePane />;
    case "note":
      return <NoteContainer />;
    case "chat":
      return (
        <Suspense fallback={null}>
          <ChatView />
        </Suspense>
      );
    case "templates":
      return <TemplatesPage />;
    case "settings":
      return <SettingsPage />;
    default: {
      const _exhaustive: never = view;
      return _exhaustive;
    }
  }
}

export function MainPane() {
  const view = useStore((s) => s.view);
  return (
    <main className="main">
      <TopBar />
      <div className={"content content-" + view}>
        <ViewBody view={view} />
      </div>
    </main>
  );
}

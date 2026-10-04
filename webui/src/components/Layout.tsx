import { useEffect } from "react";
import { useStore } from "../store";
import { ConfirmDialog } from "./ConfirmDialog";
import { MainPane } from "./MainPane";
import { Sidebar } from "./Sidebar";

export function Layout() {
  const drawerOpen = useStore((s) => s.drawerOpen);
  const setDrawerOpen = useStore((s) => s.setDrawerOpen);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (evt: KeyboardEvent) => {
      if (evt.key === "Escape") setDrawerOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [drawerOpen, setDrawerOpen]);

  return (
    <div className="app">
      <Sidebar />
      <MainPane />
      <ConfirmDialog />
    </div>
  );
}

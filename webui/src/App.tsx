import { useEffect } from "react";
import { DaemonDown } from "./components/DaemonDown";
import { Layout } from "./components/Layout";
import { Toasts } from "./components/Toasts";
import { useStore } from "./store";
import { startApp } from "./ws";

export function App() {
  const daemonUp = useStore((s) => s.daemonUp);

  useEffect(() => {
    startApp();
  }, []);

  return (
    <>
      {daemonUp ? <Layout /> : <DaemonDown />}
      <Toasts />
    </>
  );
}

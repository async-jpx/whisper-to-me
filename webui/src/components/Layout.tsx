import { ConfirmDialog } from "./ConfirmDialog";
import { MainPane } from "./MainPane";
import { Sidebar } from "./Sidebar";

export function Layout() {
  return (
    <div className="app">
      <Sidebar />
      <MainPane />
      <ConfirmDialog />
    </div>
  );
}

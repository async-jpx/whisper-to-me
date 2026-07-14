import { ConfirmDialog } from "./ConfirmDialog";
import { MainPane } from "./MainPane";
import { MeetingPrompt } from "./MeetingPrompt";
import { Sidebar } from "./Sidebar";

export function Layout() {
  return (
    <div className="app">
      <Sidebar />
      <MainPane />
      <MeetingPrompt />
      <ConfirmDialog />
    </div>
  );
}

import { Suspense } from "react";
import { WorkspaceShell } from "../../../components/workspace/shell";
export default function Workspace() {
  return (
    <Suspense fallback={<div className="loading">Opening workspace…</div>}>
      <WorkspaceShell />
    </Suspense>
  );
}

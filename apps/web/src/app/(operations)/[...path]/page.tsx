import { Suspense } from "react";
import { WorkspaceShell } from "../../../components/workspace/shell";
import { notFound } from "next/navigation";
export default async function Operations({
  params,
}: {
  params: Promise<{ path: string[] }>;
}) {
  const { path } = await params;
  if (
    ![
      "dashboard",
      "insureds",
      "submissions",
      "tasks",
      "settings",
      "onboarding",
      "profile",
      "agency-select",
      "access-pending",
    ].includes(path[0])
  )
    notFound();
  return (
    <Suspense fallback={<div className="loading">Opening workspace…</div>}>
      <WorkspaceShell />
    </Suspense>
  );
}

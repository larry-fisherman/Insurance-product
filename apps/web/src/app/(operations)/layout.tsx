import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
export const dynamic = "force-dynamic";
export default async function OperationsLayout({ children }: { children: ReactNode }) {
  const jar = await cookies();
  if (!jar.has("__Host-app_session") && !jar.has("cgl_session")) redirect("/login");
  return children;
}

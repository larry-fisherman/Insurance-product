import { redirect } from "next/navigation";
export default async function Legacy({
  params,
  searchParams,
}: {
  params: Promise<{ path?: string[] }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { path = [] } = await params;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(await searchParams))
    if (typeof value === "string") query.set(key, value);
  redirect("/" + (path.join("/") || "dashboard") + (query.size ? "?" + query : ""));
}

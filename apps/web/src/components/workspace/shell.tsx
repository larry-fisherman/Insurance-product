"use client";
import Link from "next/link";
import { useState } from "react";
import { usePathname, useSearchParams, useRouter } from "next/navigation";
import { send, type Me } from "../../lib/workspace-api";
import { useResource, Loading, Notice } from "./primitives";
import { AgencySettings, TaskQueue } from "./settings";
import {
  Dashboard,
  InsuredDirectory,
  InsuredDetail,
  SubmissionList,
  NewSubmission,
} from "./accounts";
import { SubmissionWorkspace } from "./submission";

export function WorkspaceShell() {
  const router = useRouter();
  const path = usePathname(),
    params = useSearchParams();
  const { data: me, error, refresh } = useResource<Me>("/me");
  const [signoutError, setSignoutError] = useState("");
  if (!me)
    return (
      <div className="app-boot">
        <Loading error={error} retry={refresh} />
      </div>
    );
  if (!me.mfa_verified)
    return (
      <div className="app-boot">
        <Notice>Complete authenticator enrollment to access your agency.</Notice>
        <Link href="/login">Return to sign in</Link>
      </div>
    );
  const base = path.split("/").filter(Boolean);
  const section = base[1] ?? "dashboard",
    id = base[2];
  const nav = [
    ["dashboard", "Overview", "◫", "agency.read"],
    ["insureds", "Insured accounts", "▦", "insureds.read"],
    ["submissions", "Submissions", "▤", "submissions.read"],
    ["tasks", "Follow-ups", "☑", "reviews.read"],
    ["settings", "Settings & team", "⚙", "members.read"],
  ];
  let content;
  if (section === "dashboard") content = <Dashboard me={me} />;
  else if (section === "insureds")
    content = id ? (
      <InsuredDetail key={id} id={id} me={me} />
    ) : (
      <InsuredDirectory me={me} />
    );
  else if (section === "submissions")
    content =
      id === "new" ? (
        <NewSubmission me={me} insuredId={params.get("insured") ?? ""} />
      ) : id ? (
        <SubmissionWorkspace key={id} id={id} me={me} />
      ) : (
        <SubmissionList me={me} />
      );
  else if (section === "tasks") content = <TaskQueue />;
  else if (section === "settings") content = <AgencySettings me={me} />;
  else
    content = (
      <Notice>Page not found. Choose a workspace page from the navigation.</Notice>
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link href="/workspace" className="brand">
          <span className="brand-symbol">▱</span>
          <span>
            CGL<span className="brand-light"> Workspace</span>
          </span>
        </Link>
        <div className="agency-switch">
          <small>YOUR AGENCY</small>
          {me.agencies.length > 1 ? (
            <select
              aria-label="Select agency"
              value={me.tenant_id}
              onChange={async (e) => {
                try {
                  await send("/auth/agency", { tenant_id: e.target.value });
                  await refresh();
                  router.push("/workspace");
                } catch (error) {
                  setSignoutError(
                    error instanceof Error ? error.message : "Unable to switch agency.",
                  );
                }
              }}
            >
              {me.agencies.map((a) => (
                <option value={a.id} key={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          ) : (
            <strong>{me.agencies[0]?.name ?? "Choose an agency"}</strong>
          )}
        </div>
        <nav aria-label="Main navigation">
          {nav
            .filter(([, , , permission]) => me.permissions.includes(permission))
            .map(([key, title, icon]) => (
              <Link
                key={key}
                className={section === key ? "active" : ""}
                href={"/workspace/" + (key === "dashboard" ? "" : key)}
              >
                <span aria-hidden="true">{icon}</span>
                {title}
                {key === "submissions" && <span className="nav-shortcut">CGL</span>}
              </Link>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="review-note">
            <span>◎</span>
            <p>
              Every fact has a source.
              <br />
              Every decision has a record.
            </p>
          </div>
          <Link href="/security">Security & data practices ↗</Link>
          <div className="user-card">
            <span className="avatar">
              {me.name
                .split(" ")
                .map((n) => n[0])
                .slice(0, 2)
                .join("")}
            </span>
            <div>
              <strong>{me.name}</strong>
              <small>
                {me.roles.includes("approver")
                  ? "Broker · approval authority"
                  : me.roles.join(", ")}
              </small>
            </div>
            <button
              className="text-button"
              aria-label="Sign out"
              onClick={async () => {
                try {
                  await send("/auth/logout", {});
                  router.push("/login");
                } catch (error) {
                  setSignoutError(
                    error instanceof Error ? error.message : "Unable to sign out.",
                  );
                }
              }}
            >
              ↪
            </button>
          </div>
        </div>
      </aside>
      <div className="main-column">
        <header className="app-topbar">
          <span>
            Broker operations <b>/</b> Commercial general liability
          </span>
          <span className="environment-label">
            <span className="status-dot" />
            Synthetic review
          </span>
        </header>
        <div className="synthetic-banner">
          Fictional data only ·{" "}
          {me.auth_method === "synthetic-demo-bypass"
            ? "Example access with MFA bypass · "
            : ""}
          Broker review required before export
        </div>
        <main className="app-main" key={me.tenant_id}>
          {signoutError && <Notice error>{signoutError}</Notice>}
          {content}
        </main>
        <footer className="app-footer">
          <span>CGL Workspace</span>
          <span>Source evidence → Broker judgment → Approved submission</span>
        </footer>
      </div>
    </div>
  );
}

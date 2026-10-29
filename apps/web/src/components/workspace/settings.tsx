"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { send, dateLabel, type Me, type Task } from "../../lib/workspace-api";
import {
  Heading,
  useResource,
  Loading,
  Form,
  Field,
  formText,
  Status,
  Notice,
  Empty,
} from "./primitives";
interface Member {
  id: string;
  display_name: string;
  roles: string[];
  status: string;
  row_version: number;
}
interface Agency {
  agency: { name: string };
  members: Member[];
  invitations: {
    id: string;
    email: string;
    status: string;
    expires_at: string;
    role_keys: string[];
  }[];
  usage: { kind: string; quantity: number }[];
}
const roles = ["viewer", "preparer", "approver", "tenant_admin"];
function RoleCheckboxes({ values = ["preparer"] }: { values?: string[] }) {
  return (
    <div className="role-choices">
      {roles.map((role) => (
        <label className="checkbox" key={role}>
          <input
            type="checkbox"
            name="roles"
            value={role}
            defaultChecked={values.includes(role)}
          />
          <span>{role.replaceAll("_", " ")}</span>
        </label>
      ))}
    </div>
  );
}
export function AgencySettings({ me }: { me: Me }) {
  const { data, error, refresh } = useResource<Agency>("/agency");
  const [invitation, setInvitation] = useState("");
  if (!data) return <Loading error={error} retry={refresh} />;
  const manage = me.permissions.includes("members.manage");
  return (
    <>
      <Heading
        eyebrow="Agency administration"
        title="Settings & team"
        description={data.agency.name}
      />
      <section className="panel padded">
        <h2>Team access</h2>
        <p className="muted">
          Preparer and approver are separate permissions. Agency administrators manage
          users without automatically gaining access to documents or approval.
        </p>
        {data.members.map((member) => (
          <div className="member" key={member.id}>
            <div>
              <h3>
                {member.display_name}
                {member.id === me.membership_id ? " (you)" : ""}
              </h3>
              <div className="actions">
                {member.roles.map((role) => (
                  <span className="badge" key={role}>
                    {role.replaceAll("_", " ")}
                  </span>
                ))}
                <Status value={member.status} />
              </div>
            </div>
            {manage && (
              <details>
                <summary>Edit access</summary>
                <Form
                  submit="Save membership"
                  action={async (form) => {
                    await send(
                      "/memberships/" + member.id,
                      {
                        expected_version: member.row_version,
                        status: formText(form, "status"),
                        roles: form.getAll("roles"),
                      },
                      "PATCH",
                    );
                    if (member.id === me.membership_id) window.location.reload();
                    else await refresh();
                  }}
                >
                  <RoleCheckboxes values={member.roles} />
                  <Field title="Membership status" name="status">
                    <select name="status" defaultValue={member.status}>
                      <option value="active">Active</option>
                      <option value="disabled">Disabled</option>
                    </select>
                  </Field>
                </Form>
              </details>
            )}
          </div>
        ))}
      </section>
      {manage && (
        <section className="panel padded">
          <h2>Invite a colleague</h2>
          <p className="muted">
            Create an expiring invitation. Share the one-time link yourself; email
            delivery is outside this MVP.
          </p>
          <Form
            submit="Create invitation"
            action={async (form) => {
              const result = await send<{ url: string }>("/invitations", {
                email: formText(form, "email"),
                roles: form.getAll("roles"),
              });
              setInvitation(result.url);
              await refresh();
            }}
          >
            <Field title="Colleague’s email" name="email" type="email" required />
            <RoleCheckboxes />
          </Form>
          {invitation && (
            <Notice>
              <b>Invitation created — copy this link now:</b>
              <input
                aria-label="Invitation link"
                value={invitation}
                readOnly
                onFocus={(e) => e.target.select()}
              />
            </Notice>
          )}
          {data.invitations.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Status</th>
                  <th>Expires</th>
                </tr>
              </thead>
              <tbody>
                {data.invitations.map((i) => (
                  <tr key={i.id}>
                    <td>{i.email}</td>
                    <td>
                      <Status value={i.status} />
                    </td>
                    <td>{dateLabel(i.expires_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
      {me.permissions.includes("usage.read") && (
        <section className="panel padded">
          <h2>Measured usage</h2>
          {data.usage.length ? (
            <div className="metrics">
              {data.usage.map((u) => (
                <div className="metric" key={u.kind}>
                  <span>{u.kind.replaceAll("_", " ")}</span>
                  <strong>{u.quantity}</strong>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">
              Usage is recorded when document processing and package generation complete.
            </p>
          )}
          <p className="muted">
            Provider costs are unknown until connected vendor usage and prices are
            reconciled. Time savings require a measured broker baseline.
          </p>
        </section>
      )}
      <section className="panel padded">
        <h2>Review environment</h2>
        <dl className="settings-list">
          <div>
            <dt>Data mode</dt>
            <dd>Fictional and synthetic documents only</dd>
          </div>
          <div>
            <dt>Authentication</dt>
            <dd>
              {me.auth_method === "synthetic-demo-bypass"
                ? "Synthetic example access (MFA bypass disclosed)"
                : "Password with authenticator MFA"}
            </dd>
          </div>
          <div>
            <dt>Distribution</dt>
            <dd>Manual export after broker approval</dd>
          </div>
          <div>
            <dt>Retention</dt>
            <dd>
              Local volumes persist until operator deletion. No automatic purge is
              enabled.
            </dd>
          </div>
        </dl>
        <Link href="/security">Security practices and launch requirements ↗</Link>
      </section>
    </>
  );
}
export function TaskQueue() {
  const r = useResource<{ items: Task[] }>("/tasks", true);
  return (
    <>
      <Heading
        eyebrow="Account management"
        title="Follow-up queue"
        description="Keep underwriting questions and client follow-ups connected to their submission."
      />
      {r.data ? (
        <section className="panel">
          {r.data.items.length ? (
            r.data.items.map((t) => (
              <div className="task padded" key={t.id}>
                <div>
                  <Link
                    className="row-link"
                    href={"/workspace/submissions/" + t.submission_id}
                  >
                    {t.title}
                  </Link>
                  <p>{t.description}</p>
                  <small className="muted">Due {dateLabel(t.due_date)}</small>
                </div>
                <Status value={t.status} />
              </div>
            ))
          ) : (
            <Empty title="No follow-ups yet">
              <p>Add a task from a submission workspace.</p>
            </Empty>
          )}
        </section>
      ) : (
        <Loading error={r.error} retry={r.refresh} />
      )}
    </>
  );
}
export function InvitationAccept() {
  const router = useRouter();
  return (
    <main className="public-page">
      <Heading
        title="Join your agency"
        description="Sign in with the email address on the invitation and complete MFA before accepting."
      />
      <div className="panel padded narrow">
        <Form
          submit="Accept invitation"
          action={async () => {
            const token = new URLSearchParams(window.location.search).get("token");
            if (!token) throw new Error("The invitation link is missing its token.");
            await send("/invitations/accept", { token });
            router.push("/workspace");
          }}
        >
          <p>
            The invitation will add your existing user to the brokerage with the assigned
            roles.
          </p>
          <Link href="/login">Sign in or register first ↗</Link>
        </Form>
      </div>
    </main>
  );
}

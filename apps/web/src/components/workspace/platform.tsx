"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { components } from "@cgl/contracts";
import { navigateNewSession, send, type Me } from "../../lib/workspace-api";
import {
  Field,
  Form,
  Heading,
  Loading,
  Notice,
  formText,
  useResource,
} from "./primitives";

type Setup = components["schemas"]["OnboardingResponse"];
type BillingData = components["schemas"]["BillingResponse"];

export function AgencySetup({ me }: { me: Me }) {
  const { data, error, refresh } = useResource<Setup>("/agency/onboarding");
  if (!data) return <Loading error={error} retry={refresh} />;
  return (
    <>
      <Heading
        eyebrow="Agency administration"
        title="Agency setup"
        description="Set up your agency and assign the people who prepare and approve submissions."
      />
      <section className="panel padded">
        <h2>
          {data.completed_at ? "Agency details saved" : "Complete your agency profile"}
        </h2>
        {me.permissions.includes("agency.manage") ? (
          <Form
            key={data.row_version}
            action={async (form) => {
              await send(
                "/agency/onboarding",
                {
                  name: formText(form, "name"),
                  timezone: formText(form, "timezone"),
                  contact_email: formText(form, "contact_email"),
                  expected_version: data.row_version,
                  acknowledge: form.get("acknowledge") === "on",
                },
                "PATCH",
              );
              await refresh();
            }}
          >
            <Field title="Agency name" name="name" value={data.name} required />
            <Field
              title="Agency contact email"
              name="contact_email"
              type="email"
              value={data.contact_email || me.email || ""}
              required
            />
            <Field
              title="Timezone"
              name="timezone"
              value={data.timezone}
              placeholder="America/Chicago"
              required
            />
            <label className="checkbox">
              <input type="checkbox" name="acknowledge" required />I am authorized to
              manage this agency and will use fictional documents only in this review
              environment.
            </label>
          </Form>
        ) : (
          <p>
            {data.name} · {data.timezone}
          </p>
        )}
      </section>
      <section className="panel padded">
        <h2>Your first submission</h2>
        <ol>
          <li>Save your agency details.</li>
          <li>
            <Link href="/settings/team">Assign preparer and approver roles</Link>.
            Administrators do not automatically have these permissions.
          </li>
          <li>Create an insured account and a CGL submission.</li>
          <li>
            Upload documents, resolve discrepancies, then approve the exact generated
            package.
          </li>
        </ol>
        <p>
          Preparer assigned: {data.has_preparer ? "Yes" : "Not yet"} · Approver assigned:{" "}
          {data.has_approver ? "Yes" : "Not yet"}
        </p>
        {me.permissions.includes("insureds.write") && (
          <Link className="button primary" href="/insureds">
            Open insured accounts →
          </Link>
        )}
      </section>
    </>
  );
}

export function Billing() {
  const { data, error, refresh } = useResource<BillingData>("/billing");
  if (!data) return <Loading error={error} retry={refresh} />;
  return (
    <>
      <Heading
        eyebrow="Agency administration"
        title="Subscription & billing"
        description="Review your agency’s access, limits, and subscription requests."
      />
      <section className="panel padded">
        <h2>{data.plan.replaceAll("-", " ")}</h2>
        <p>Status: {data.status.replaceAll("_", " ")}</p>
        <p>
          {data.active_seats} of {data.seat_limit} active employees ·{" "}
          {data.submissions_used} of {data.submission_limit} submissions
        </p>
        <Notice>
          This local review uses a manually administered subscription. Payment collection
          and invoices are not connected. Usage costs are unpriced.
        </Notice>
        <Form
          key={data.row_version}
          action={async (form) => {
            await send(
              "/billing",
              {
                billing_email: formText(form, "email"),
                expected_version: data.row_version,
              },
              "PATCH",
            );
            await refresh();
          }}
        >
          <Field
            title="Billing contact email"
            name="email"
            type="email"
            value={data.billing_email}
            required
          />
        </Form>
      </section>
      <section className="panel padded">
        <h2>Request a subscription change</h2>
        <Form
          submit="Record request"
          action={async (form) => {
            await send("/billing/requests", {
              kind: formText(form, "kind"),
              note: formText(form, "note"),
            });
            await refresh();
          }}
        >
          <Field title="Request" name="kind">
            <select name="kind">
              <option value="plan_change">Change plan or limits</option>
              <option value="cancellation">Cancel subscription</option>
              <option value="data_return">Request data return</option>
            </select>
          </Field>
          <Field
            title="Details (at least 10 characters)"
            name="note"
            type="textarea"
            required
          />
          <p className="muted">
            Recorded for administrator review. This does not change your plan or send a
            message.
          </p>
        </Form>
        <h3>Request history</h3>
        {data.requests.length ? (
          data.requests.map((item, i) => (
            <p key={i}>
              {String(item.kind).replaceAll("_", " ")} ·{" "}
              {String(item.created_at).slice(0, 10)} · {String(item.note)}
            </p>
          ))
        ) : (
          <p>No requests yet.</p>
        )}
      </section>
    </>
  );
}

export function Usage() {
  const { data, error, refresh } = useResource<{
    items: { kind: string; quantity: number }[];
    period: string;
  }>("/agency/usage");
  if (!data) return <Loading error={error} retry={refresh} />;
  return (
    <>
      <Heading
        title="Agency usage"
        description="Recorded activity across this agency. All-time quantities; costs are unpriced."
      />
      <section className="panel padded">
        {data.items.length ? (
          data.items.map((item) => (
            <p key={item.kind}>
              {item.kind.replaceAll("_", " ")}: <strong>{item.quantity}</strong>
            </p>
          ))
        ) : (
          <p>No usage recorded yet.</p>
        )}
      </section>
    </>
  );
}

export function Profile({ me }: { me: Me }) {
  const { data, error, refresh } = useResource<{
    items: {
      id: string;
      current: boolean;
      created_at: string;
      last_seen_at: string;
      method: string;
    }[];
  }>("/auth/sessions");
  return (
    <>
      <Heading title="Profile & security" description={me.name + " · " + me.email} />
      <section className="panel padded">
        <h2>Confirm your identity</h2>
        <p>Confirmation is required for sensitive actions after ten minutes.</p>
        {me.auth_method === "oidc" ? (
          <form action="/api/v1/auth/oidc/start" method="get">
            <input type="hidden" name="return_to" value="/profile/security" />
            <button className="button primary" type="submit">
              Confirm with identity provider
            </button>
          </form>
        ) : me.auth_method === "synthetic-demo-bypass" ? (
          <Notice>
            This fictional example uses a temporary MFA bypass. Managed and local accounts
            require identity confirmation.
          </Notice>
        ) : (
          <Form
            submit="Confirm identity"
            action={async (form) =>
              send("/auth/reauthenticate", {
                email: me.email,
                password: formText(form, "password"),
                code: formText(form, "code"),
              })
            }
          >
            <Field title="Password" type="password" name="password" required />
            <Field title="New authenticator code" name="code" required />
          </Form>
        )}
      </section>
      <section className="panel padded">
        <h2>Active sessions</h2>
        {!data ? (
          <Loading error={error} retry={refresh} />
        ) : (
          <>
            {data.items.map((session) => (
              <p key={session.id}>
                {session.current ? "This session" : "Another session"} · {session.method}{" "}
                · last active {new Date(session.last_seen_at).toLocaleString()}
              </p>
            ))}
            <Form
              submit="Sign out other sessions"
              action={async () => {
                await send("/auth/sessions/revoke-others", {});
                await refresh();
              }}
            >
              <p>Keep this browser session and revoke your other active sessions.</p>
            </Form>
          </>
        )}
      </section>
    </>
  );
}

export function AgencyChoice({ me }: { me: Me }) {
  return (
    <>
      <Heading
        title={me.agencies.length ? "Choose your agency" : "Agency access pending"}
        description="Access is granted through an agency invitation or an approved agency activation."
      />
      <section className="panel padded">
        {me.agencies.map((agency) => (
          <Form
            key={agency.id}
            submit={"Open " + agency.name}
            action={async () => {
              await send("/auth/agency", { tenant_id: agency.id });
              navigateNewSession("/dashboard");
            }}
          >
            <p>{agency.name}</p>
          </Form>
        ))}
        <p>
          <Link href="/activate">Activate an agency</Link> ·{" "}
          <Link href="/invite">Accept an invitation</Link> ·{" "}
          <Link href="/profile/security">Profile & security</Link>
        </p>
      </section>
    </>
  );
}

export function Activation() {
  const [token, setToken] = useState("");
  useEffect(() => {
    const value = new URLSearchParams(window.location.hash.slice(1)).get("token");
    if (value) {
      // The URL fragment is a browser-only, one-time credential source.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setToken(value);
      history.replaceState(null, "", window.location.pathname);
    }
  }, []);
  return (
    <main className="public-page">
      <Heading
        title="Activate your agency"
        description="Sign in with the verified email address assigned to the agency activation."
      />
      <section className="panel padded narrow">
        <Form
          submit="Activate agency"
          action={async (form) => {
            await send("/auth/activate", {
              token: token || formText(form, "token"),
              acknowledge: form.get("acknowledge") === "on",
              terms_version: "preview-1",
            });
            navigateNewSession("/onboarding");
          }}
        >
          {!token && <Field title="Activation code" name="token" required />}
          <label className="checkbox">
            <input name="acknowledge" type="checkbox" required />I am authorized to
            administer this agency and acknowledge the preview terms and fictional-data
            restriction.
          </label>
          <p>
            <Link
              href="/login?return_to=/activate"
              target="_blank"
              rel="noopener noreferrer"
            >
              Sign in in another tab
            </Link>
            . Keep your activation code to return after signing in.
          </p>
        </Form>
      </section>
    </main>
  );
}

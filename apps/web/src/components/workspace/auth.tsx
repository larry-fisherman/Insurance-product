"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { send } from "../../lib/workspace-api";
import { Field, Form, Notice, formText } from "./primitives";

export function LoginScreen() {
  const router = useRouter();
  const [mode, setMode] = useState("login"),
    [secret, setSecret] = useState(""),
    [codes, setCodes] = useState<string[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function demo() {
    setBusy(true);
    setError("");
    try {
      const result = await send<{ submission_id: string }>("/auth/demo", {});
      router.push("/workspace/submissions/" + result.submission_id);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Unable to start demo.");
      setBusy(false);
    }
  }
  return (
    <div className="auth-layout">
      <aside className="auth-story">
        <Link className="brand" href="/">
          ▱ CGL Workspace
        </Link>
        <div>
          <span className="eyebrow">Built around your judgment</span>
          <h1>
            Less preparation.
            <br />
            More perspective.
          </h1>
          <p>
            Bring every document, discrepancy, and decision into one focused submission
            workspace.
          </p>
          <div className="story-steps">
            <p>
              <b>01</b> Gather the evidence
            </p>
            <p>
              <b>02</b> Resolve the differences
            </p>
            <p>
              <b>03</b> Approve the exact package
            </p>
          </div>
        </div>
        <small>Commercial general liability · US brokerages</small>
      </aside>
      <main className="auth-main">
        <div className="auth-card">
          <span className="badge amber">Synthetic review environment</span>
          {codes.length ? (
            <>
              <h1>Save your recovery codes</h1>
              <p>
                Store these codes securely. Each code works once to reset your password
                and authenticator.
              </p>
              <div className="recovery-codes">
                {codes.map((code) => (
                  <code key={code}>{code}</code>
                ))}
              </div>
              <Link className="button primary" href="/workspace">
                Continue to workspace →
              </Link>
            </>
          ) : secret ? (
            <>
              <h1>Set up your authenticator</h1>
              <p>Add a time-based code using this setup key in your authenticator app.</p>
              <code className="setup-key">{secret}</code>
              <Form
                submit="Verify authenticator"
                action={async (form) => {
                  const data = await send<{ recovery_codes: string[] }>(
                    "/auth/mfa/confirm",
                    { code: formText(form, "code") },
                  );
                  setCodes(data.recovery_codes);
                }}
              >
                <Field
                  title="Six-digit authenticator code"
                  name="code"
                  required
                  placeholder="000000"
                />
              </Form>
            </>
          ) : (
            <>
              <h1>
                {mode === "register"
                  ? "Create your agency"
                  : mode === "recover"
                    ? "Recover access"
                    : "Welcome back"}
              </h1>
              <p>
                {mode === "register"
                  ? "Start a private workspace for fictional documents."
                  : mode === "recover"
                    ? "Use an unused recovery code to reset your credentials."
                    : "Sign in to continue preparing your submissions."}
              </p>
              <Form
                key={mode}
                submit={
                  mode === "register"
                    ? "Create account"
                    : mode === "recover"
                      ? "Reset and enroll authenticator"
                      : "Sign in"
                }
                action={async (form) => {
                  const value: Record<string, string> = {
                    email: formText(form, "email"),
                  };
                  if (mode === "recover") {
                    value.recovery_code = formText(form, "recovery_code");
                    value.new_password = formText(form, "password");
                  } else {
                    value.password = formText(form, "password");
                    value.code = formText(form, "code");
                  }
                  if (mode === "register") {
                    value.name = formText(form, "name");
                    value.agency = formText(form, "agency");
                  }
                  const result = await send<{
                    mfa_enrollment_required?: boolean;
                    secret?: string;
                  }>("/auth/" + mode, value);
                  if (result.secret) setSecret(result.secret);
                  else router.push("/workspace");
                }}
              >
                {mode === "register" && (
                  <>
                    <Field title="Your name" name="name" required />
                    <Field title="Agency name" name="agency" required />
                  </>
                )}
                <Field title="Work email" name="email" type="email" required />
                {mode === "recover" && (
                  <Field title="Recovery code" name="recovery_code" required />
                )}
                <Field
                  title={
                    mode === "recover"
                      ? "New password (at least 12 characters)"
                      : "Password"
                  }
                  name="password"
                  type="password"
                  required
                />
                {mode === "login" && (
                  <Field
                    title="Authenticator code"
                    name="code"
                    placeholder="Six-digit code"
                  />
                )}
              </Form>
              <div className="auth-links">
                <button
                  className="text-button"
                  onClick={() => setMode(mode === "login" ? "register" : "login")}
                >
                  {mode === "login" ? "Create a new agency" : "Back to sign in"}
                </button>
                {mode === "login" && (
                  <button className="text-button" onClick={() => setMode("recover")}>
                    Recover access
                  </button>
                )}
              </div>
              <div className="divider">or explore the workflow</div>
              {error && <Notice error>{error}</Notice>}
              <button className="demo-button" onClick={() => void demo()} disabled={busy}>
                {busy ? "Creating fictional workspace…" : "Open a fictional example →"}
              </button>
              <small className="muted block">
                Creates an isolated fictional agency. Example access bypasses MFA and
                expires after eight hours. Use synthetic documents only.
              </small>
            </>
          )}
        </div>
      </main>
    </div>
  );
}

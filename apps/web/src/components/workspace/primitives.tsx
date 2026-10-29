"use client";
import { useCallback, useEffect, useState, type ReactNode, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, label } from "../../lib/workspace-api";

export function useResource<T>(path: string, poll = false) {
  const router = useRouter();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    try {
      const result = await api<T>(path);
      setData(result);
      setError("");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        router.push("/login");
        return;
      }
      setError(error instanceof Error ? error.message : "Unable to load this page.");
    }
  }, [path, router]);
  useEffect(() => {
    // refresh awaits the network before updating state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
    if (poll) {
      const timer = setInterval(() => {
        if (!document.hidden) void refresh();
      }, 3000);
      return () => clearInterval(timer);
    }
  }, [refresh, poll]);
  return { data, error, refresh };
}
export function Notice({
  children,
  error = false,
}: {
  children: ReactNode;
  error?: boolean;
}) {
  return (
    <div className={error ? "notice danger" : "notice"} role={error ? "alert" : "status"}>
      {children}
    </div>
  );
}
export function Status({ value }: { value: string }) {
  return (
    <span
      className={
        "badge " +
        (["approved", "exported", "completed", "clean", "approve", "correct"].includes(
          value,
        )
          ? "green"
          : ["failed", "conflict", "quarantined", "unreviewed"].includes(value)
            ? "amber"
            : "")
      }
    >
      {label(value)}
    </span>
  );
}
export function Loading({ error, retry }: { error?: string; retry?: () => void }) {
  return error ? (
    <div className="empty">
      <Notice error>{error}</Notice>
      <button onClick={retry}>Try again</button>
    </div>
  ) : (
    <div className="loading" role="status">
      <span className="spinner" />
      Loading workspace…
    </div>
  );
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty-symbol">◇</span>
      <h3>{title}</h3>
      {children}
    </div>
  );
}
export function Field({
  title,
  name,
  required = false,
  type = "text",
  value,
  placeholder,
  children,
}: {
  title: string;
  name: string;
  required?: boolean;
  type?: string;
  value?: string | number;
  placeholder?: string;
  children?: ReactNode;
}) {
  return (
    <label className="field">
      <span>
        {title}
        {required && <small> *</small>}
      </span>
      {children ??
        (type === "textarea" ? (
          <textarea
            name={name}
            defaultValue={value}
            required={required}
            placeholder={placeholder}
            rows={3}
          />
        ) : (
          <input
            name={name}
            type={type}
            defaultValue={value}
            required={required}
            placeholder={placeholder}
          />
        ))}
    </label>
  );
}
export function Form({
  children,
  action,
  submit = "Save changes",
  onSuccess,
}: {
  children: ReactNode;
  action: (form: FormData) => Promise<unknown>;
  submit?: string;
  onSuccess?: (result: unknown) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false);
  async function handle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const result = await action(form);
      setSaved(true);
      onSuccess?.(result);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Unable to save.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={handle}>
      {children}
      {error && <Notice error>{error}</Notice>}
      <div className="form-actions">
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Saving…" : submit}
        </button>
        {saved && <span role="status">Saved successfully</span>}
      </div>
    </form>
  );
}
export function Heading({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="actions">{children}</div>
    </div>
  );
}
export const formText = (form: FormData, key: string) =>
  String(form.get(key) ?? "").trim();
export const optional = (form: FormData, key: string) => formText(form, key) || null;

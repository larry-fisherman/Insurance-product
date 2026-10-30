"use client";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type FormEvent,
} from "react";
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
    [saved, setSaved] = useState(false),
    [dirty, setDirty] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const dirtyRef = useRef(false);
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current) event.preventDefault();
    };
    const navigate = (event: MouseEvent) => {
      if (!dirtyRef.current || !formRef.current?.isConnected) return;
      const target =
        event.target instanceof Element
          ? event.target.closest("a[href], button, input[type=radio]")
          : null;
      if (!target || formRef.current?.contains(target)) return;
      const card = formRef.current.closest(".review-card");
      const discardsForm =
        (target.matches("a[href]") &&
          target.getAttribute("target") !== "_blank" &&
          !target.hasAttribute("download")) ||
        !!target.closest("nav, .review-toolbar") ||
        target.hasAttribute("data-navigation") ||
        (card &&
          card === target.closest(".review-card") &&
          target.matches("button, input[type=radio]"));
      if (!discardsForm) return;
      if (!window.confirm("Discard unsaved changes in this form?")) {
        event.preventDefault();
        event.stopImmediatePropagation();
      } else {
        dirtyRef.current = false;
        setDirty(false);
      }
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", navigate, true);
    return () => {
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", navigate, true);
    };
  }, [dirty]);
  async function handle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const result = await action(form);
      dirtyRef.current = false;
      setDirty(false);
      setSaved(true);
      onSuccess?.(result);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Unable to save.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      ref={formRef}
      onSubmit={handle}
      onChange={() => {
        dirtyRef.current = true;
        setDirty(true);
        setSaved(false);
      }}
    >
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

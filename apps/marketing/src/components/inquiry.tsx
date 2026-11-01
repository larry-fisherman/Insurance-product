"use client";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";

export function Inquiry({ purpose }: { purpose: "demo" | "pricing" | "contact" }) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const key = useRef<string>("");
  const router = useRouter();
  return (
    <form
      className="inquiry"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        setError("");
        const data = new FormData(event.currentTarget);
        key.current ||= crypto.randomUUID();
        try {
          const response = await fetch("/api/inquiries", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Idempotency-Key": key.current,
            },
            body: JSON.stringify({
              purpose,
              name: data.get("name"),
              email: data.get("email"),
              agency: data.get("agency"),
              team_size: data.get("team_size"),
              message: data.get("message"),
              website: data.get("website"),
              consent: data.get("consent") === "on",
            }),
          });
          if (!response.ok) {
            if (response.status < 500 && response.status !== 429) key.current = "";
            throw new Error(
              response.status === 429
                ? "Please wait a minute before trying again."
                : "Your inquiry could not be saved. Please check the fields and try again.",
            );
          }
          router.push("/thank-you");
        } catch (e) {
          setError(e instanceof Error ? e.message : "Unable to send. Please retry.");
          setBusy(false);
        }
      }}
    >
      <div className="form-grid">
        <label>
          Your name
          <input name="name" required maxLength={120} autoComplete="name" />
        </label>
        <label>
          Work email
          <input
            name="email"
            type="email"
            required
            maxLength={254}
            autoComplete="email"
          />
        </label>
      </div>
      <label>
        Agency name
        <input name="agency" required maxLength={160} autoComplete="organization" />
      </label>
      <label>
        Team size
        <select name="team_size" defaultValue="">
          <option value="">Prefer not to say</option>
          <option>1–5</option>
          <option>6–20</option>
          <option>21–100</option>
          <option>100+</option>
        </select>
      </label>
      <label>
        What would you like to explore?
        <textarea
          name="message"
          rows={4}
          maxLength={1500}
          placeholder="Tell us about your workflow using fictional examples."
        />
      </label>
      <label className="honeypot" aria-hidden="true">
        Website
        <input name="website" tabIndex={-1} autoComplete="off" />
      </label>
      <label className="checkbox">
        <input type="checkbox" name="consent" required />I understand this is a local
        preview and will use fictional contact details only.
      </label>
      <p className="fine">
        Please exclude insured details, claims information and documents. This local form
        saves the inquiry for review; it does not send an email.
      </p>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <button className="button" disabled={busy}>
        {busy
          ? "Saving inquiry…"
          : purpose === "demo"
            ? "Request a demo →"
            : "Send inquiry →"}
      </button>
    </form>
  );
}

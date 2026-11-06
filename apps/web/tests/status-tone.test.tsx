import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Status } from "../src/components/workspace/primitives";

const tones: Record<string, string> = {
  approved: "tone-approved",
  exported: "tone-approved",
  completed: "tone-done",
  clean: "tone-done",
  approve: "tone-done",
  correct: "tone-done",
  failed: "tone-error",
  rejected: "tone-error",
  conflict: "tone-conflict",
  quarantined: "tone-warning",
  unreviewed: "tone-warning",
  needs_review: "tone-warning",
  missing: "tone-missing",
  superseded: "",
  omit: "",
  reject: "",
  open: "",
  queued: "",
};

function toneOf(value: string): string {
  const { unmount } = render(<Status value={value} />);
  const badge = screen.getByText(value.replaceAll("_", " "), { exact: true });
  const classes = [...badge.classList];
  unmount();
  expect(classes).toContain("badge");
  return classes.filter((name) => name !== "badge").join(" ");
}

describe("Status", () => {
  it.each(Object.keys(tones))("keeps the text and picks the tone for %s", (value) => {
    expect(toneOf(value)).toBe(tones[value]);
  });

  it("keeps the exact label text, adding no glyph to the DOM", () => {
    render(<Status value="conflict" />);
    expect(screen.getByText("conflict", { exact: true })).toHaveTextContent(/^conflict$/);
  });

  it("gives each named status a distinct tone", () => {
    const named = [
      "approved",
      "completed",
      "failed",
      "conflict",
      "quarantined",
      "missing",
      "superseded",
    ];
    expect(new Set(named.map(toneOf)).size).toBe(named.length);
  });

  it("gives the approved tone only to approved and exported", () => {
    const approved = Object.keys(tones).filter((v) => toneOf(v) === "tone-approved");
    expect(approved.sort()).toEqual(["approved", "exported"]);
  });
});

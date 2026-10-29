"use client";
import { useState } from "react";
import { FactEditor, reviewedValue, reviewedContext } from "./fact-editor";
import {
  send,
  pretty,
  label,
  type Group,
  type WorkspaceData,
  type Me,
  type Source,
  type Json,
} from "../../lib/workspace-api";
import { Form, Field, formText, Notice, Status, Empty } from "./primitives";

function FactDisplay({ value }: { value: Json }) {
  return typeof value === "object" && value && !Array.isArray(value) ? (
    <dl className="claim-facts">
      {Object.entries(value)
        .filter(([, v]) => v !== null)
        .map(([key, v]) => (
          <div key={key}>
            <dt>{label(key)}</dt>
            <dd>{pretty(v)}</dd>
          </div>
        ))}
    </dl>
  ) : (
    <pre className="fact-value">{pretty(value)}</pre>
  );
}

export function Evidence({ source, data }: { source: Source; data: WorkspaceData }) {
  const doc = data.documents.find((d) => d.id === source.document_id);
  const loc = source.locator;
  return (
    <div className="evidence-block">
      <div>
        <b>{doc?.filename ?? "Source document"}</b>
        <span>
          {loc.kind === "pdf"
            ? `Page ${loc.page}, line ${loc.line}`
            : `${loc.sheet} · ${loc.range}`}
          {loc.method === "ocr" ? " · OCR" : ""}
        </span>
      </div>
      <blockquote>{source.text}</blockquote>
      <a
        href={"/api/v1/documents/" + source.document_id + "/content"}
        target="_blank"
        rel="noreferrer"
      >
        Open original document ↗
      </a>
    </div>
  );
}
function GroupReview({
  group,
  data,
  canReview,
  onSaved,
}: {
  group: Group;
  data: WorkspaceData;
  canReview: boolean;
  onSaved: () => Promise<void>;
}) {
  const [selected, setSelected] = useState(group.decision?.observation_id ?? ""),
    [mode, setMode] = useState("approve"),
    [open, setOpen] = useState(!group.verified);
  const choice = group.observations.find((o) => o.id === selected);
  const initialValue =
    group.decision?.value ?? choice?.value ?? group.observations[0]?.value;
  const initialContext = group.decision?.context ?? choice?.context ?? group.context;
  return (
    <article
      className={
        "review-card " + (group.conflicting && !group.verified ? "conflict-card" : "")
      }
    >
      <div className="review-card-header">
        <div>
          <span className="eyebrow">{group.field.split(".")[0]}</span>
          <h3>{group.label}</h3>
          <div className="context-line">
            {Object.entries(group.context)
              .filter(
                ([key, value]) =>
                  value &&
                  !(key === "scope" && value === "all operations") &&
                  value !== "unspecified",
              )
              .map(([key, value]) => (
                <span key={key}>
                  {label(key)}: {pretty(value)}
                </span>
              ))}
          </div>
        </div>
        <div className="actions">
          {group.conflicting && <Status value="conflict" />}
          <Status value={group.verified ? group.state : "unreviewed"} />
          <button className="text-button" onClick={() => setOpen(!open)}>
            {open ? "Collapse" : "Review"}
          </button>
        </div>
      </div>
      {!open && group.decision && (
        <div className="review-summary">
          <FactDisplay value={group.decision.value} />
          <p>{group.decision.reason}</p>
        </div>
      )}
      {open && (
        <>
          <div className="observation-grid">
            {group.observations.map((observation) => (
              <div
                className={
                  "observation " + (selected === observation.id ? "selected" : "")
                }
                key={observation.id}
              >
                <label className="observation-choice">
                  <input
                    type="radio"
                    name={"choice-" + group.key}
                    checked={selected === observation.id}
                    onChange={() => setSelected(observation.id)}
                  />
                  <span>Select this source value</span>
                </label>
                <FactDisplay value={observation.value} />
                <small className="muted">
                  {observation.method === "local-label-parser"
                    ? "Local parser · requires broker verification"
                    : "Provider extraction · requires broker verification"}
                </small>
                {observation.warnings.map((w) => (
                  <Notice key={w}>{w}</Notice>
                ))}
                {[observation.source_id, ...observation.supporting_source_ids].map(
                  (id) => {
                    const source = data.sources.find((s) => s.id === id);
                    return source ? (
                      <Evidence key={id} source={source} data={data} />
                    ) : (
                      <Notice error key={id}>
                        Source unavailable. Refresh before reviewing.
                      </Notice>
                    );
                  },
                )}
              </div>
            ))}
          </div>
          {canReview ? (
            <div className="review-controls">
              <div className="segmented">
                {[
                  ["approve", "Verify selected value"],
                  ["correct", "Correct value"],
                  ["omit", "Omit with reason"],
                  ["reject", "Reject extraction"],
                ].map(([key, title]) => (
                  <button
                    key={key}
                    className={mode === key ? "active" : ""}
                    onClick={() => setMode(key)}
                  >
                    {title}
                  </button>
                ))}
              </div>
              <Form
                key={mode + selected + group.fingerprint}
                submit={mode === "approve" ? "Save verified value" : "Save decision"}
                action={async (form) => {
                  if (mode === "approve" && !selected)
                    throw new Error("Select the source value you verified.");
                  let value = initialValue,
                    context = initialContext;
                  if (mode === "correct") {
                    value = reviewedValue(group.field, form);
                    context = reviewedContext(form);
                  }
                  await send("/submissions/" + data.submission.id + "/review-decisions", {
                    expected_version: data.submission.row_version,
                    decisions: [
                      {
                        group_key: group.key,
                        fingerprint: group.fingerprint,
                        field: group.field,
                        action: mode,
                        observation_id: selected || null,
                        value,
                        context,
                        reason: formText(form, "reason"),
                      },
                    ],
                  });
                  await onSaved();
                }}
              >
                {mode === "correct" && (
                  <FactEditor
                    field={group.field}
                    value={initialValue}
                    context={initialContext}
                  />
                )}
                <Field
                  title={
                    group.conflicting
                      ? "Resolution reason — explain which value is supported"
                      : "Review note / supporting reason"
                  }
                  name="reason"
                  required
                  type="textarea"
                  placeholder="Describe your verification or correction and any client confirmation."
                />
                {mode === "correct" && (
                  <p className="muted">
                    Your correction is recorded as a broker decision, with the original
                    extraction retained.
                  </p>
                )}
              </Form>
            </div>
          ) : (
            <Notice>
              Your role can read this evidence. A preparer or approver must save review
              decisions.
            </Notice>
          )}
        </>
      )}
    </article>
  );
}
export function ReviewPanel({
  data,
  me,
  refresh,
}: {
  data: WorkspaceData;
  me: Me;
  refresh: () => Promise<void>;
}) {
  const [filter, setFilter] = useState("pending"),
    [manual, setManual] = useState(false),
    [manualField, setManualField] = useState("insured.legal_name");
  const groups = data.readiness.groups.filter((g) =>
    filter === "all" || filter === "conflicts"
      ? filter === "all" || g.conflicting
      : !g.verified,
  );
  const canReview = me.permissions.includes("reviews.write");
  return (
    <>
      <div className="panel-heading standalone">
        <div>
          <h2>Verify the facts. Keep the evidence.</h2>
          <p>
            {data.readiness.verified_count} of {data.readiness.total_count} review groups
            verified. Every material conflict needs a recorded decision.
          </p>
        </div>
        {canReview && (
          <button onClick={() => setManual(!manual)}>＋ Add broker-attested fact</button>
        )}
      </div>
      {manual && (
        <section className="panel padded">
          <h3>Add a material fact</h3>
          <p className="muted">
            Use this when the information was confirmed outside the supplied documents.
            Record its origin and reason.
          </p>
          <Form
            submit="Save attested fact"
            action={async (form) => {
              const field = formText(form, "field");
              await send("/submissions/" + data.submission.id + "/review-decisions", {
                expected_version: data.submission.row_version,
                decisions: [
                  {
                    group_key: "manual:" + crypto.randomUUID(),
                    field,
                    action: "correct",
                    value: reviewedValue(field, form),
                    context: reviewedContext(form),
                    reason: formText(form, "reason"),
                  },
                ],
              });
              setManual(false);
              await refresh();
            }}
          >
            <Field title="CGL field" name="field">
              <select
                name="field"
                value={manualField}
                onChange={(e) => setManualField(e.target.value)}
              >
                {Object.entries(data.field_labels).map(([key, title]) => (
                  <option key={key} value={key}>
                    {title}
                  </option>
                ))}
              </select>
            </Field>
            <FactEditor key={manualField} field={manualField} />
            <Field
              title="Attestation origin and reason"
              name="reason"
              type="textarea"
              required
            />
          </Form>
        </section>
      )}
      <div className="review-toolbar">
        <div className="segmented">
          {[
            ["pending", "Needs review"],
            ["conflicts", "Conflicts"],
            ["all", "All facts"],
          ].map(([key, title]) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={key === filter ? "active" : ""}
            >
              {title}
            </button>
          ))}
        </div>
        <span className="muted">{groups.length} groups</span>
      </div>
      {groups.length ? (
        groups.map((g) => (
          <GroupReview
            key={g.key + g.fingerprint + String(g.verified)}
            group={g}
            data={data}
            canReview={canReview}
            onSaved={refresh}
          />
        ))
      ) : (
        <Empty
          title={
            data.readiness.total_count
              ? "All selected facts reviewed"
              : "No extracted facts yet"
          }
        >
          <p>
            {data.readiness.total_count
              ? "Continue to the readiness report to address any missing information."
              : "Upload documents and allow processing to complete. You can also add a broker-attested fact."}
          </p>
        </Empty>
      )}
    </>
  );
}

"use client";
import Link from "next/link";
import { useState } from "react";
import {
  api,
  send,
  pretty,
  dateLabel,
  label,
  type WorkspaceData,
  type Me,
  type Package,
  type Json,
} from "../../lib/workspace-api";
import {
  useResource,
  Loading,
  Heading,
  Form,
  Field,
  formText,
  optional,
  Notice,
  Status,
  Empty,
} from "./primitives";
import { ReviewPanel } from "./review";
import { NewSubmission } from "./accounts";

function Documents({
  data,
  me,
  refresh,
}: {
  data: WorkspaceData;
  me: Me;
  refresh: () => Promise<void>;
}) {
  const [replace, setReplace] = useState("");
  return (
    <>
      <section className="panel padded">
        <h2>Documents, organized around this submission</h2>
        <p className="muted">
          PDF and XLSX · Up to 50 MB per file, 30 active files and 100 MB per submission.
          Upload fictional documents only.
        </p>
        {me.permissions.includes("documents.write") && (
          <Form
            submit="Upload and process"
            action={async (form) => {
              const file = form.get("file");
              if (!(file instanceof File) || !file.size)
                throw new Error("Choose a PDF or XLSX document.");
              const body = new FormData();
              body.set("file", file);
              if (replace) body.set("supersedes_id", replace);
              await api("/submissions/" + data.submission.id + "/documents", {
                method: "POST",
                body,
              });
              setReplace("");
              await refresh();
            }}
          >
            <div className="upload-zone">
              <span>↑</span>
              <Field
                title={replace ? "Choose a replacement document" : "Choose a document"}
                name="file"
              >
                <input type="file" name="file" accept=".pdf,.xlsx" required />
              </Field>
            </div>
            {replace && (
              <Notice>
                Replacing {data.documents.find((d) => d.id === replace)?.filename}. The
                original and its review history will be retained.{" "}
                <button
                  className="text-button"
                  type="button"
                  onClick={() => setReplace("")}
                >
                  Cancel replacement
                </button>
              </Notice>
            )}
          </Form>
        )}
        <small className="muted block">
          Uploaded files remain quarantined until malware scanning succeeds. The built-in
          fictional documents are signed fixtures.
        </small>
      </section>
      {data.documents.length ? (
        <div className="document-list">
          {data.documents.map((doc) => (
            <article
              className={"panel document-card " + (doc.excluded_reason ? "dim" : "")}
              key={doc.id}
            >
              <div className="file-icon">
                {doc.media_type === "application/pdf" ? "PDF" : "XLSX"}
              </div>
              <div className="document-info">
                <h3>{doc.filename}</h3>
                <p>
                  {Math.ceil(doc.byte_size / 1024)} KB · {label(doc.document_type)} ·{" "}
                  {doc.page_count} pages / sheets
                </p>
                <div className="actions">
                  <Status value={doc.processing_status} />
                  <Status value={doc.scan_status} />
                  {doc.excluded_reason && <span className="badge">Excluded</span>}
                </div>
                {doc.excluded_reason && <p>Reason: {doc.excluded_reason}</p>}
                <details>
                  <summary>Integrity and version</summary>
                  <code className="hash">{doc.sha256}</code>
                  <small className="muted block">
                    Document {doc.id}
                    {doc.supersedes_id ? " · replaces " + doc.supersedes_id : ""}
                  </small>
                </details>
              </div>
              <div className="document-actions">
                {doc.scan_status === "clean" && (
                  <a
                    className="button"
                    href={"/api/v1/documents/" + doc.id + "/content"}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open original ↗
                  </a>
                )}
                {!doc.excluded_reason && me.permissions.includes("documents.write") && (
                  <>
                    <button onClick={() => setReplace(doc.id)}>Replace</button>
                    <details>
                      <summary>Exclude from package</summary>
                      <Form
                        submit="Exclude document"
                        action={async (f) => {
                          await send("/documents/" + doc.id + "/exclude", {
                            reason: formText(f, "reason"),
                          });
                          await refresh();
                        }}
                      >
                        <Field title="Reason for exclusion" name="reason" required />
                        <p className="muted">
                          This removes its facts from the active review and requires a new
                          approval. The excluded document stays in completeness
                          accounting; an authorized approver must accept any resulting
                          omission.
                        </p>
                      </Form>
                    </details>
                    {doc.processing_status === "completed" && (
                      <details>
                        <summary>Correct document classification</summary>
                        <Form
                          submit="Save classification"
                          action={async (form) => {
                            await send("/documents/" + doc.id + "/classification", {
                              document_type: formText(form, "document_type"),
                              reason: formText(form, "reason"),
                            });
                            await refresh();
                          }}
                        >
                          <Field title="Document type" name="document_type">
                            <select name="document_type" defaultValue={doc.document_type}>
                              {[
                                "application",
                                "policy",
                                "loss_runs",
                                "exposure_schedule",
                                "endorsement",
                                "mixed",
                                "unknown",
                              ].map((kind) => (
                                <option key={kind} value={kind}>
                                  {label(kind)}
                                </option>
                              ))}
                            </select>
                          </Field>
                          <Field
                            title="Reason for classification correction"
                            name="reason"
                            required
                          />
                        </Form>
                      </details>
                    )}
                  </>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty title="No documents uploaded">
          <p>
            Start with the expiring policy, current application, exposure schedule, and
            available loss runs.
          </p>
        </Empty>
      )}
      {data.jobs.length > 0 && (
        <section className="panel">
          <div className="panel-heading">
            <h2>Processing activity</h2>
          </div>
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Status</th>
                <th>Stage</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {data.jobs.map((job) => (
                <tr key={job.id}>
                  <td>
                    {job.document_id
                      ? data.documents.find((d) => d.id === job.document_id)?.filename
                      : "Submission package"}
                    <small className="muted block">Attempt {job.attempts}</small>
                  </td>
                  <td>
                    <Status value={job.status} />
                  </td>
                  <td>{label(job.stage)}</td>
                  <td>
                    {job.error_code && (
                      <>
                        <span className="error-text">{label(job.error_code)}</span>
                        {job.error_code === "SCAN_UNAVAILABLE" && (
                          <p className="muted">
                            The malware scanner is not available. Start it with make
                            scan-up and retry.
                          </p>
                        )}
                      </>
                    )}
                    {job.status === "failed" &&
                      me.permissions.includes("documents.write") && (
                        <Form
                          submit="Retry processing"
                          action={async () => {
                            await send("/jobs/" + job.id + "/retry", {});
                            await refresh();
                          }}
                        >
                          <span />
                        </Form>
                      )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
function Readiness({
  data,
  me,
  refresh,
}: {
  data: WorkspaceData;
  me: Me;
  refresh: () => Promise<void>;
}) {
  return (
    <section className="panel padded">
      <div className="panel-heading plain">
        <div>
          <h2>Submission readiness</h2>
          <p>
            {data.readiness.ready
              ? "Required review is complete. Generate a candidate package for final approval."
              : "Resolve these items before generating a package."}
          </p>
        </div>
        <Status value={data.readiness.ready ? "completed" : "needs_review"} />
      </div>
      {data.readiness.blockers.map((b) => (
        <div className="blocker" key={b.key}>
          <div className="blocker-icon">!</div>
          <div>
            <p>{b.message}</p>
            {b.waivable ? (
              <>
                <small className="muted">
                  An approver may accept this exception with a documented reason.
                </small>
                {me.permissions.includes("exceptions.accept") && (
                  <details>
                    <summary>Record an accepted exception</summary>
                    <Form
                      submit="Accept exception"
                      action={async (f) => {
                        await send("/submissions/" + data.submission.id + "/exceptions", {
                          key: b.key,
                          fingerprint: b.fingerprint,
                          expected_version: data.submission.row_version,
                          reason: formText(f, "reason"),
                        });
                        await refresh();
                      }}
                    >
                      <Field
                        title="Reason and planned follow-up"
                        name="reason"
                        type="textarea"
                        required
                      />
                    </Form>
                  </details>
                )}
              </>
            ) : (
              <small className="muted">
                Required · resolve through document review, correction, or additional
                evidence.
              </small>
            )}
          </div>
        </div>
      ))}
      {data.readiness.exceptions.length > 0 && (
        <>
          <h3>Accepted exceptions and omissions</h3>
          {data.readiness.exceptions.map((b) => (
            <div className="accepted" key={b.key}>
              <b>{b.message}</b>
              <p>{b.reason}</p>
            </div>
          ))}
        </>
      )}
    </section>
  );
}
function Candidate({
  p,
  data,
  me,
  refresh,
}: {
  p: Package;
  data: WorkspaceData;
  me: Me;
  refresh: () => Promise<void>;
}) {
  const detail = useResource<{
    package: Package;
    revision: { snapshot: Json; sha256: string; number: number };
    approval: { created_at: string; attestation: string } | null;
    superseded: boolean;
    artifacts: { format: string }[];
  }>("/packages/" + p.id, true);
  const current = detail.data;
  return (
    <article className="panel padded">
      <div className="panel-heading plain">
        <div>
          <h3>Package · {dateLabel(p.created_at)}</h3>
          <small className="muted">
            {p.template_version} · input version {p.input_version}
          </small>
        </div>
        <div className="actions">
          <Status
            value={
              current?.superseded
                ? "superseded"
                : current?.approval
                  ? "approved"
                  : (current?.package.status ?? p.status)
            }
          />
        </div>
      </div>
      {current?.package.manifest_hash && (
        <>
          <div className="artifact-actions">
            {["pdf", "xlsx", "zip"].map((format) => (
              <a
                className="button"
                key={format}
                target="_blank"
                rel="noreferrer"
                href={`/api/v1/packages/${p.id}/artifacts/${format}?preview=true`}
              >
                Preview {format.toUpperCase()} ↗
              </a>
            ))}
          </div>
          <details>
            <summary>Reviewed facts and exact package manifest</summary>
            <div className="snapshot-facts">
              {data.readiness.facts.map((f, i) => (
                <p key={i}>
                  <b>{label(f.field)}</b> · {pretty(f.value)}
                </p>
              ))}
            </div>
            <p className="muted">Immutable reviewed snapshot for this package:</p>
            <pre className="json-view">
              {JSON.stringify(current.revision.snapshot, null, 2)}
            </pre>
            <p>Revision SHA-256</p>
            <code className="hash">{current.revision.sha256}</code>
            <p>Manifest SHA-256</p>
            <code className="hash">{current.package.manifest_hash}</code>
            <pre className="json-view">
              {JSON.stringify(current.package.manifest, null, 2)}
            </pre>
          </details>
          {current.superseded ? (
            <Notice>
              This is a historical package. Material information or the reviewed revision
              changed. Generate and approve a current package.
            </Notice>
          ) : current.approval ? (
            <div className="approval-box">
              <h3>Approved {dateLabel(current.approval.created_at)}</h3>
              <p>{current.approval.attestation}</p>
              {me.permissions.includes("packages.export") && (
                <div className="artifact-actions">
                  {["pdf", "xlsx", "zip"].map((format) => (
                    <a
                      className="button primary"
                      key={format}
                      href={`/api/v1/packages/${p.id}/artifacts/${format}`}
                    >
                      Export {format.toUpperCase()} ↓
                    </a>
                  ))}
                </div>
              )}
              <small>Distribution to carriers remains manual.</small>
            </div>
          ) : me.permissions.includes("packages.approve") &&
            current.package.status === "ready" ? (
            <Form
              submit="Approve this exact package"
              action={async (f) => {
                if (!f.get("reviewed"))
                  throw new Error(
                    "Review the files and confirm the approval attestation.",
                  );
                await send("/packages/" + p.id + "/approvals", {
                  manifest_hash: current.package.manifest_hash,
                  attestation: formText(f, "attestation"),
                });
                await detail.refresh();
                await refresh();
              }}
            >
              <label className="checkbox">
                <input name="reviewed" type="checkbox" required />
                <span>
                  I reviewed the candidate files, source evidence, and documented
                  exceptions.
                </span>
              </label>
              <Field
                title="Final approval attestation"
                name="attestation"
                required
                type="textarea"
                placeholder="I approve this exact submission package and its stated exceptions for manual distribution."
              />
            </Form>
          ) : (
            <Notice>
              A user with approval authority must review and approve this package before
              export.
            </Notice>
          )}
        </>
      )}
      {p.status === "failed" && (
        <Notice error>
          Package generation failed. See processing activity to retry.
        </Notice>
      )}
      {!current && <Loading error={detail.error} retry={detail.refresh} />}
    </article>
  );
}
function Packages({
  data,
  me,
  refresh,
}: {
  data: WorkspaceData;
  me: Me;
  refresh: () => Promise<void>;
}) {
  return (
    <>
      <Readiness data={data} me={me} refresh={refresh} />
      {data.readiness.ready && me.permissions.includes("packages.generate") && (
        <section className="panel padded">
          <h2>Prepare the candidate package</h2>
          <p className="muted">
            An original PDF summary, exact-value spreadsheet, and ZIP with supporting
            documents. Each file is hashed and tied to the reviewed revision.
          </p>
          <Form
            submit="Generate submission package"
            action={async () => {
              await send("/submissions/" + data.submission.id + "/packages", {
                expected_version: data.submission.row_version,
              });
              await refresh();
            }}
          >
            <span />
          </Form>
        </section>
      )}
      {data.packages.length ? (
        data.packages.map((p) => (
          <Candidate p={p} data={data} me={me} refresh={refresh} key={p.id} />
        ))
      ) : (
        <Empty title="No candidate packages yet">
          <p>
            Finish review and resolve the readiness report to prepare the first package.
          </p>
        </Empty>
      )}
    </>
  );
}
export function Tasks({
  data,
  me,
  refresh,
}: {
  data: WorkspaceData;
  me: Me;
  refresh: () => Promise<void>;
}) {
  return (
    <>
      <section className="panel padded">
        <h2>Follow-up tasks</h2>
        {data.tasks.length ? (
          data.tasks.map((task) => (
            <div className="task" key={task.id}>
              <div>
                <h3>{task.title}</h3>
                <p>{task.description}</p>
                <small className="muted">Due {dateLabel(task.due_date)}</small>
              </div>
              <div>
                <Status value={task.status} />
                {me.permissions.includes("reviews.write") && (
                  <Form
                    submit="Save task"
                    action={async (form) => {
                      await send(
                        "/tasks/" + task.id,
                        {
                          status: formText(form, "status"),
                          expected_version: task.row_version,
                          resolution_reason: formText(form, "resolution_reason"),
                          due_date: optional(form, "due_date"),
                          owner_membership_id:
                            formText(form, "owner_membership_id") || me.membership_id,
                        },
                        "PATCH",
                      );
                      await refresh();
                    }}
                  >
                    <Field title="Task status" name="status">
                      <select name="status" defaultValue={task.status}>
                        <option value="open">Open</option>
                        <option value="completed">Completed</option>
                      </select>
                    </Field>
                    <Field
                      title="Due date"
                      name="due_date"
                      type="date"
                      value={task.due_date ?? ""}
                    />
                    <Field title="Assigned member" name="owner_membership_id">
                      <select
                        name="owner_membership_id"
                        defaultValue={
                          data.members.find((m) => m.user_id === task.owner_id)?.id ??
                          me.membership_id
                        }
                      >
                        {data.members.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.display_name}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field
                      title="Resolution / reopening reason"
                      name="resolution_reason"
                      required
                    />
                  </Form>
                )}
              </div>
            </div>
          ))
        ) : (
          <Empty title="No follow-up tasks" />
        )}
      </section>
      {me.permissions.includes("reviews.write") && (
        <section className="panel padded">
          <h3>Add a follow-up</h3>
          <Form
            submit="Create task"
            action={async (f) => {
              await send("/submissions/" + data.submission.id + "/tasks", {
                title: formText(f, "title"),
                description: formText(f, "description"),
                due_date: optional(f, "due_date"),
              });
              await refresh();
            }}
          >
            <div className="form-grid">
              <Field title="Task title" name="title" required />
              <Field title="Due date" name="due_date" type="date" />
            </div>
            <Field
              title="Details / suggested client question"
              name="description"
              type="textarea"
            />
          </Form>
        </section>
      )}
    </>
  );
}
function Settings({
  data,
  me,
  refresh,
}: {
  data: WorkspaceData;
  me: Me;
  refresh: () => Promise<void>;
}) {
  const s = data.submission;
  const members = useResource<{
    members: { id: string; display_name: string; status: string }[];
  }>("/agency");
  return (
    <section className="panel padded narrow">
      <h2>Submission details</h2>
      <Form
        submit="Save and require fresh approval"
        action={async (f) => {
          await send(
            "/submissions/" + s.id,
            {
              insured_id: s.insured_id,
              title: formText(f, "title"),
              kind: s.kind,
              intended_start: formText(f, "intended_start"),
              intended_end: formText(f, "intended_end"),
              due_date: optional(f, "due_date"),
              loss_required_start: optional(f, "loss_required_start"),
              loss_required_end: optional(f, "loss_required_end"),
              assigned_membership_id: formText(f, "assigned_membership_id"),
              prior_submission_id: s.prior_submission_id,
              expected_version: s.row_version,
              loss_freshness_days: Number(formText(f, "loss_freshness_days")),
            },
            "PATCH",
          );
          await refresh();
        }}
      >
        <Field title="Title" name="title" required value={s.title} />
        <Field title="Assigned broker" name="assigned_membership_id">
          <select name="assigned_membership_id" defaultValue={s.assigned_membership_id}>
            {members.data?.members
              .filter((m) => m.status === "active")
              .map((m) => (
                <option value={m.id} key={m.id}>
                  {m.display_name}
                </option>
              )) ?? (
              <option value={s.assigned_membership_id}>
                {s.assigned_membership_id === me.membership_id
                  ? me.name
                  : "Current assignee"}
              </option>
            )}
          </select>
        </Field>
        <div className="form-grid">
          <Field
            title="Policy effective"
            name="intended_start"
            type="date"
            value={s.intended_start}
            required
          />
          <Field
            title="Policy expiration"
            name="intended_end"
            type="date"
            value={s.intended_end}
            required
          />
          <Field
            title="Submission due"
            name="due_date"
            type="date"
            value={s.due_date ?? ""}
          />
          <Field
            title="Loss valuation maximum age (days)"
            name="loss_freshness_days"
            type="number"
            value={s.requirements.loss_freshness_days ?? 60}
          />
          <Field
            title="Loss history requested from"
            name="loss_required_start"
            type="date"
            value={s.loss_required_start ?? ""}
          />
          <Field
            title="Loss history requested through"
            name="loss_required_end"
            type="date"
            value={s.loss_required_end ?? ""}
          />
        </div>
        <Notice>
          Changing material submission details requires a new package and approval.
        </Notice>
      </Form>
    </section>
  );
}
function Audit({ id }: { id: string }) {
  const r = useResource<{
    items: {
      id: string;
      action: string;
      created_at: string;
      object_type: string;
      change_summary: Json;
    }[];
  }>("/submissions/" + id + "/audit", true);
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>Review and approval history</h2>
        <span className="muted">Latest 200 events</span>
      </div>
      {r.data ? (
        <div className="timeline">
          {r.data.items.map((e) => (
            <div key={e.id}>
              <span className="timeline-dot" />
              <div>
                <strong>{label(e.action)}</strong>
                <p>
                  {new Date(e.created_at).toLocaleString()} · {label(e.object_type)}
                </p>
                {Object.keys(e.change_summary ?? {}).length > 0 && (
                  <code>{pretty(e.change_summary)}</code>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Loading error={r.error} retry={r.refresh} />
      )}
    </section>
  );
}
export function SubmissionWorkspace({ id, me }: { id: string; me: Me }) {
  const { data, error, refresh } = useResource<WorkspaceData>("/submissions/" + id, true);
  const [tab, setTab] = useState("overview"),
    [renew, setRenew] = useState(false);
  if (!data) return <Loading error={error} retry={refresh} />;
  if (renew)
    return (
      <>
        <button onClick={() => setRenew(false)}>← Back to submission</button>
        <NewSubmission me={me} prior={data.submission} />
      </>
    );
  const s = data.submission;
  const terminal = ["closed", "cancelled"].includes(s.status);
  const workspaceMe = terminal
    ? {
        ...me,
        permissions: me.permissions.filter(
          (p) =>
            !p.endsWith(".write") &&
            !["exceptions.accept", "packages.generate", "packages.approve"].includes(p),
        ),
      }
    : me;
  const pending = data.jobs.filter((j) =>
    ["queued", "running"].includes(j.status),
  ).length;
  return (
    <>
      <Link className="breadcrumb" href="/workspace/submissions">
        ← All submissions
      </Link>
      <Heading
        eyebrow={s.kind === "renewal" ? "CGL renewal" : "CGL submission"}
        title={data.insured.name}
        description={s.title}
      >
        <Status value={s.status} />
        {me.permissions.includes("submissions.write") && (
          <button data-navigation onClick={() => setRenew(true)}>
            Create next renewal
          </button>
        )}
      </Heading>
      <div className="workspace-meta">
        <span>
          <small>Policy period</small>
          {dateLabel(s.intended_start)} — {dateLabel(s.intended_end)}
        </span>
        <span>
          <small>Submission due</small>
          {dateLabel(s.due_date)}
        </span>
        <span>
          <small>Review progress</small>
          {data.readiness.verified_count} / {data.readiness.total_count} verified
        </span>
        <span>
          <small>Documents</small>
          {data.documents.filter((d) => !d.excluded_reason).length} active{" "}
          {pending ? `· ${pending} processing` : ""}
        </span>
      </div>
      {terminal && (
        <Notice>
          This submission is {s.status}. Its evidence and history remain available.
        </Notice>
      )}
      {!terminal && me.permissions.includes("submissions.write") && (
        <details className="panel padded">
          <summary>Change submission status</summary>
          <Form
            submit="Update status"
            action={async (form) => {
              await send("/submissions/" + s.id + "/status", {
                expected_version: s.row_version,
                status: formText(form, "status"),
                reason: formText(form, "reason"),
              });
              await refresh();
            }}
          >
            <Field title="New status" name="status">
              <select name="status">
                <option value="collecting">Collecting documents</option>
                <option value="in_review">In review</option>
                <option value="ready_for_package">Ready for package</option>
                <option value="closed">Closed</option>
                <option value="cancelled">Cancelled</option>
              </select>
            </Field>
            <Field title="Reason" name="reason" required />
          </Form>
        </details>
      )}
      {error && <Notice error>{error} The values below may be out of date.</Notice>}
      <nav className="tabs" aria-label="Submission workspace">
        {[
          ["overview", "Overview"],
          ["documents", "Documents"],
          ["review", "Data & discrepancies"],
          ["tasks", "Follow-ups"],
          ["packages", "Package & approval"],
          ["audit", "History"],
          ...(me.permissions.includes("submissions.write")
            ? [["settings", "Details"]]
            : []),
        ].map(([key, title]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={tab === key ? "active" : ""}
            aria-current={tab === key ? "page" : undefined}
          >
            {title}
            {key === "review" &&
              data.readiness.total_count - data.readiness.verified_count > 0 && (
                <span className="count">
                  {data.readiness.total_count - data.readiness.verified_count}
                </span>
              )}
          </button>
        ))}
      </nav>
      {tab === "overview" && (
        <>
          <div className="overview-grid">
            <section className="panel padded">
              <span className="eyebrow">Next action</span>
              <h2>
                {pending
                  ? "Your documents are processing"
                  : !data.readiness.total_count
                    ? "Gather the supporting documents"
                    : data.readiness.verified_count < data.readiness.total_count
                      ? "Review the extracted information"
                      : data.readiness.ready
                        ? "Prepare your submission package"
                        : "Resolve the missing information"}
              </h2>
              <p className="muted">
                {pending
                  ? "This page updates automatically. You can continue working while processing runs."
                  : "Use the evidence alongside each fact to verify values and explain discrepancies before approval."}
              </p>
              <button
                className="primary"
                onClick={() =>
                  setTab(
                    pending || !data.documents.length
                      ? "documents"
                      : data.readiness.verified_count < data.readiness.total_count
                        ? "review"
                        : "packages",
                  )
                }
              >
                Continue workflow →
              </button>
            </section>
            <section className="panel padded">
              <span className="eyebrow">Account context</span>
              <h3>{data.insured.name}</h3>
              <p>{data.insured.operations ?? "Business operations not supplied."}</p>
              <Link className="text-link" href={"/workspace/insureds/" + s.insured_id}>
                View insured account ↗
              </Link>
            </section>
          </div>
          <Readiness data={data} me={workspaceMe} refresh={refresh} />
          <section className="panel padded">
            <h3>How this review works</h3>
            <p className="muted">
              Source documents remain unchanged. Extracted observations are proposals.
              Your decisions create versioned, canonical facts. Only an authorized broker
              can approve the final files.
            </p>
          </section>
          {me.permissions.includes("reviews.write") && (
            <section className="panel padded">
              <h3>Measure preparation time</h3>
              <p className="muted">
                Record a comparable manual baseline and the total active
                preparation/review time. These are broker-reported measurements, not
                automatic estimates.
              </p>
              <Form
                submit="Record time measurement"
                action={async (form) => {
                  await send("/submissions/" + id + "/measurements", {
                    baseline_minutes: Number(formText(form, "baseline_minutes")),
                    actual_minutes: Number(formText(form, "actual_minutes")),
                    note: formText(form, "note"),
                  });
                }}
              >
                <div className="form-grid">
                  <Field
                    title="Comparable manual baseline (minutes)"
                    name="baseline_minutes"
                    type="number"
                    required
                  />
                  <Field
                    title="Actual active preparation time (minutes)"
                    name="actual_minutes"
                    type="number"
                    required
                  />
                </div>
                <Field
                  title="Measurement method / comparable scope"
                  name="note"
                  type="textarea"
                  required
                />
              </Form>
            </section>
          )}
        </>
      )}
      {tab === "documents" && (
        <Documents data={data} me={workspaceMe} refresh={refresh} />
      )}{" "}
      {tab === "review" && <ReviewPanel data={data} me={workspaceMe} refresh={refresh} />}{" "}
      {tab === "tasks" && <Tasks data={data} me={workspaceMe} refresh={refresh} />}{" "}
      {tab === "packages" && <Packages data={data} me={workspaceMe} refresh={refresh} />}{" "}
      {tab === "audit" && <Audit id={id} />}{" "}
      {tab === "settings" && <Settings data={data} me={workspaceMe} refresh={refresh} />}
    </>
  );
}

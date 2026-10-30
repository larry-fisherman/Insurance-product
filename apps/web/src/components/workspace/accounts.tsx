"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  send,
  dateLabel,
  type Insured,
  type Submission,
  type Me,
} from "../../lib/workspace-api";
import {
  useResource,
  Heading,
  Loading,
  Empty,
  Form,
  Field,
  formText,
  optional,
  Status,
} from "./primitives";

export function SubmissionTable({ items }: { items: Submission[] }) {
  return items.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Insured / submission</th>
            <th>Policy period</th>
            <th>Due date</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id}>
              <td>
                <Link className="row-link" href={"/workspace/submissions/" + s.id}>
                  {s.insured_name ?? s.title}
                </Link>
                {s.insured_name && <span className="muted block">{s.title}</span>}
              </td>
              <td>
                {dateLabel(s.intended_start)}
                <span className="muted block">to {dateLabel(s.intended_end)}</span>
              </td>
              <td>{dateLabel(s.due_date)}</td>
              <td>
                <Status value={s.status} />
              </td>
              <td>
                <Link
                  aria-label={"Open " + s.title}
                  href={"/workspace/submissions/" + s.id}
                >
                  ↗
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty title="No submissions yet">
      <p>Create an insured account, then start a CGL submission.</p>
    </Empty>
  );
}
export function Dashboard({ me }: { me: Me }) {
  const [mine, setMine] = useState(false);
  const { data, error, refresh } = useResource<{
    agency: { name: string };
    metrics: Record<string, number>;
    submissions: Submission[];
    blocking_queue: {
      key: string;
      message: string;
      submission_id: string;
      title: string;
    }[];
  }>("/dashboard" + (mine ? "?owner_membership_id=" + me.membership_id : ""), true);
  if (!data) return <Loading error={error} retry={refresh} />;
  return (
    <>
      <Heading
        eyebrow="Your agency at a glance"
        title="Submission overview"
        description="A clear path from collected documents to approved submissions."
      >
        {me.permissions.includes("submissions.write") && (
          <Link className="button primary" href="/workspace/submissions/new">
            ＋ New submission
          </Link>
        )}
      </Heading>
      <label>
        <input
          type="checkbox"
          checked={mine}
          onChange={(event) => setMine(event.target.checked)}
        />{" "}
        Show my assigned submissions
      </label>
      <div className="metrics">
        {[
          ["active_submissions", "Active submissions"],
          ["open_discrepancies", "Discrepancies to resolve"],
          ["approved_packages", "Approved / exported"],
          ["processing_failures", "Processing failures"],
        ].map(([key, title]) => (
          <div className="metric" key={key}>
            <span>{title}</span>
            <strong>{data.metrics[key]}</strong>
            <small>
              {key === "open_discrepancies"
                ? "Current readiness blockers"
                : "Current workspace records"}
            </small>
          </div>
        ))}
      </div>
      {!!data.blocking_queue.length && (
        <section className="panel padded">
          <h2>Work requiring attention</h2>
          <ul>
            {data.blocking_queue.map((item) => (
              <li key={item.submission_id + item.key}>
                <Link href={"/workspace/submissions/" + item.submission_id}>
                  {item.title}
                </Link>
                : {item.message}
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Your submission pipeline</h2>
            <p>Ordered by upcoming due date</p>
          </div>
          <span className="badge">Commercial general liability</span>
        </div>
        <SubmissionTable items={data.submissions} />
      </section>
      <div className="trust-strip">
        <span>◎ Source-linked evidence</span>
        <span>☑ Broker-controlled decisions</span>
        <span>▱ Versioned approvals</span>
      </div>
    </>
  );
}
export function InsuredDirectory({ me }: { me: Me }) {
  const router = useRouter();
  const [query, setQuery] = useState(""),
    [create, setCreate] = useState(false);
  const resource = useResource<{ items: Insured[]; next: string | null }>(
    "/insureds?limit=100&q=" + encodeURIComponent(query),
  );
  return (
    <>
      <Heading
        eyebrow="Your book of business"
        title="Insured accounts"
        description="Keep each insured’s submissions and renewal history together."
      >
        {me.permissions.includes("insureds.write") && (
          <button className="primary" onClick={() => setCreate(!create)}>
            ＋ Add insured
          </button>
        )}
      </Heading>
      {create && (
        <section className="panel padded">
          <h2>New insured account</h2>
          <InsuredForm onSaved={(id) => router.push("/workspace/insureds/" + id)} />
        </section>
      )}
      <div className="search">
        <input
          aria-label="Search insured accounts"
          placeholder="Search insured name…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      {!resource.data ? (
        <Loading error={resource.error} retry={resource.refresh} />
      ) : (
        <section className="panel">
          {resource.data.items.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Legal insured name</th>
                    <th>Entity</th>
                    <th>Operations</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {resource.data.items.map((i) => (
                    <tr key={i.id}>
                      <td>
                        <Link className="row-link" href={"/workspace/insureds/" + i.id}>
                          {i.name}
                        </Link>
                        {i.dba && <small className="muted block">DBA {i.dba}</small>}
                      </td>
                      <td>{i.entity_type ?? "Not supplied"}</td>
                      <td className="truncate">{i.operations ?? "Not supplied"}</td>
                      <td>
                        <Status value={i.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="No insured accounts found">
              <p>Add an insured to start preparing submissions.</p>
            </Empty>
          )}
          {resource.data.next && (
            <p className="muted padded">
              Showing the first 100 matches. Refine your search to find other accounts.
            </p>
          )}
        </section>
      )}
    </>
  );
}
function InsuredForm({
  insured,
  onSaved,
}: {
  insured?: Insured;
  onSaved: (id: string) => void;
}) {
  return (
    <Form
      submit={insured ? "Save account" : "Create insured"}
      action={async (form) => {
        const value = {
          name: formText(form, "name"),
          dba: optional(form, "dba"),
          entity_type: optional(form, "entity_type"),
          mailing_address: optional(form, "mailing_address"),
          operations: optional(form, "operations"),
          external_reference: optional(form, "external_reference"),
          ...(insured
            ? { expected_version: insured.row_version, status: insured.status }
            : {}),
        };
        const result = await send<Insured>(
          "/insureds" + (insured ? "/" + insured.id : ""),
          value,
          insured ? "PATCH" : "POST",
        );
        onSaved(result.id);
      }}
    >
      <div className="form-grid">
        <Field title="Legal insured name" name="name" required value={insured?.name} />
        <Field title="DBA" name="dba" value={insured?.dba ?? ""} />
        <Field
          title="Entity type"
          name="entity_type"
          value={insured?.entity_type ?? ""}
        />
        <Field
          title="AMS / external reference"
          name="external_reference"
          value={insured?.external_reference ?? ""}
        />
      </div>
      <Field
        title="Mailing address"
        name="mailing_address"
        value={insured?.mailing_address ?? ""}
      />
      <Field
        title="Business operations"
        name="operations"
        type="textarea"
        value={insured?.operations ?? ""}
      />
    </Form>
  );
}
export function InsuredDetail({ id, me }: { id: string; me: Me }) {
  const { data, error, refresh } = useResource<{
    insured: Insured;
    submissions: Submission[];
  }>("/insureds/" + id);
  const [editing, setEditing] = useState(false);
  if (!data) return <Loading error={error} retry={refresh} />;
  return (
    <>
      <Heading
        eyebrow="Insured account"
        title={data.insured.name}
        description={
          data.insured.operations ??
          "Add a business description to help organize this account."
        }
      >
        {me.permissions.includes("insureds.write") && (
          <button onClick={() => setEditing(!editing)}>Edit account</button>
        )}
        {me.permissions.includes("submissions.write") && (
          <Link
            className="button primary"
            href={"/workspace/submissions/new?insured=" + id}
          >
            ＋ New submission
          </Link>
        )}
      </Heading>
      {editing && (
        <section className="panel padded">
          <InsuredForm
            insured={data.insured}
            onSaved={() => {
              setEditing(false);
              void refresh();
            }}
          />
        </section>
      )}
      <div className="detail-grid">
        <div className="panel padded">
          <h3>Entity</h3>
          <p>{data.insured.entity_type ?? "Not supplied"}</p>
        </div>
        <div className="panel padded">
          <h3>Mailing address</h3>
          <p>{data.insured.mailing_address ?? "Not supplied"}</p>
        </div>
        <div className="panel padded">
          <h3>External reference</h3>
          <p>{data.insured.external_reference ?? "Not supplied"}</p>
        </div>
      </div>
      <section className="panel">
        <div className="panel-heading">
          <h2>Submissions and renewals</h2>
        </div>
        <SubmissionTable items={data.submissions} />
      </section>
    </>
  );
}
export function SubmissionList({ me }: { me: Me }) {
  const { data, error, refresh } = useResource<{ items: Submission[] }>(
    "/submissions?limit=100",
    true,
  );
  return (
    <>
      <Heading
        eyebrow="Placement operations"
        title="Submissions & renewals"
        description="Every CGL submission, from intake through final approval."
      >
        {me.permissions.includes("submissions.write") && (
          <Link className="button primary" href="/workspace/submissions/new">
            ＋ New submission
          </Link>
        )}
      </Heading>
      <section className="panel">
        {data ? (
          <SubmissionTable items={data.items} />
        ) : (
          <Loading error={error} retry={refresh} />
        )}
      </section>
    </>
  );
}
export function NewSubmission({
  me,
  insuredId = "",
  prior,
}: {
  me: Me;
  insuredId?: string;
  prior?: Submission;
}) {
  const router = useRouter();
  const { data, error, refresh } = useResource<{ items: Insured[] }>(
    "/insureds?limit=100",
  );
  if (!data) return <Loading error={error} retry={refresh} />;
  return (
    <>
      <Heading
        eyebrow="Start a submission"
        title={prior ? "Create the next renewal" : "New CGL submission"}
        description="Set the policy dates and the loss-history period your placement requires."
      />
      <section className="panel padded narrow">
        {!data.items.length ? (
          <Empty title="Create an insured account first">
            <Link className="button primary" href="/workspace/insureds">
              Open insured accounts
            </Link>
          </Empty>
        ) : (
          <Form
            submit="Create submission"
            action={async (form) => {
              const value = {
                insured_id: formText(form, "insured_id"),
                title: formText(form, "title"),
                kind: formText(form, "kind"),
                intended_start: formText(form, "intended_start"),
                intended_end: formText(form, "intended_end"),
                due_date: optional(form, "due_date"),
                loss_required_start: optional(form, "loss_required_start"),
                loss_required_end: optional(form, "loss_required_end"),
                assigned_membership_id: me.membership_id,
                prior_submission_id: prior?.id ?? null,
              };
              const s = await send<Submission>("/submissions", value);
              router.push("/workspace/submissions/" + s.id);
            }}
          >
            <Field title="Insured account" name="insured_id" required>
              <select
                name="insured_id"
                defaultValue={prior?.insured_id ?? insuredId}
                required
              >
                <option value="" disabled>
                  Select an insured
                </option>
                {data.items.map((i) => (
                  <option value={i.id} key={i.id}>
                    {i.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              title="Submission title"
              name="title"
              required
              value={prior ? `Renewal · ${prior.title}` : ""}
              placeholder="2027 CGL renewal"
            />
            <Field title="Type" name="kind">
              <select name="kind" defaultValue={prior ? "renewal" : "new_business"}>
                <option value="new_business">New business</option>
                <option value="renewal">Renewal</option>
              </select>
            </Field>
            <div className="form-grid">
              <Field
                title="Policy effective date"
                type="date"
                name="intended_start"
                required
                value={prior?.intended_end}
              />
              <Field
                title="Policy expiration date"
                type="date"
                name="intended_end"
                required
              />
              <Field title="Submission due date" type="date" name="due_date" />
              <div />
              <Field
                title="Loss history requested from"
                type="date"
                name="loss_required_start"
              />
              <Field
                title="Loss history requested through"
                type="date"
                name="loss_required_end"
              />
            </div>
            <p className="muted">
              Requirements vary by operation and placement. Prior-year approvals are
              retained as history and are not applied to a new renewal.
            </p>
          </Form>
        )}
      </section>
    </>
  );
}

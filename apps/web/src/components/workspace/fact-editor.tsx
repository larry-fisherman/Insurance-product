"use client";
import { type Json, pretty } from "../../lib/workspace-api";
import { Field, formText } from "./primitives";

export function reviewedValue(field: string, form: FormData): Json {
  if (field !== "loss.claim")
    return field === "loss.period" ? "Loss report supplied" : formText(form, "value");
  const value: Record<string, Json> = {};
  for (const key of [
    "claim_id",
    "carrier",
    "policy",
    "loss_date",
    "report_date",
    "description",
    "status",
    "paid",
    "reserves",
    "incurred",
    "valuation",
  ]) {
    const raw = formText(form, "claim_" + key);
    if (raw) value[key] = raw;
  }
  return value;
}
export function reviewedContext(form: FormData): Record<string, Json> {
  const context: Record<string, Json> = {};
  for (const key of [
    "scope",
    "role",
    "basis",
    "unit",
    "currency",
    "period_start",
    "period_end",
    "as_of",
    "classification_system",
    "policy_reference",
    "entity_id",
    "location_id",
  ]) {
    const value = formText(form, "context_" + key);
    if (value) context[key] = value;
  }
  return context;
}
export function FactEditor({
  field,
  value,
  context = {},
}: {
  field: string;
  value?: Json;
  context?: Record<string, Json>;
}) {
  const exposure = field === "exposure.amount",
    lossPeriod = ["loss.period", "loss.no_losses"].includes(field),
    claim = field === "loss.claim";
  const coverageAmount =
    field.startsWith("coverage.") &&
    !["coverage.endorsement", "coverage.trigger", "coverage.additional_insured"].includes(
      field,
    );
  const currency = exposure || claim || coverageAmount || !!context.currency;
  const period = exposure || lossPeriod || !!context.period_start || !!context.period_end;
  const visible = new Set([
    "scope",
    "role",
    ...(field === "insured.dba" || field.startsWith("location.") || exposure
      ? ["entity_id", "location_id"]
      : []),
    ...(currency ? ["currency"] : []),
    ...(period ? ["period_start", "period_end"] : []),
    ...(exposure ? ["basis", "unit"] : []),
    ...(field === "loss.period" ? ["as_of"] : []),
    ...(field === "operations.class_code" ? ["classification_system"] : []),
    ...(field.startsWith("coverage.") || field.startsWith("policy.")
      ? ["policy_reference"]
      : []),
  ]);
  const claimValue =
    typeof value === "object" && value && !Array.isArray(value) ? value : {};
  return (
    <div className="fact-editor">
      {field === "loss.period" ? (
        <p className="muted">
          Enter the report period and valuation date shown on the loss report.
        </p>
      ) : claim ? (
        <>
          <h3>Claim valuation</h3>
          <div className="form-grid">
            {[
              ["claim_id", "Claim identifier"],
              ["carrier", "Carrier"],
              ["policy", "Policy identifier"],
              ["loss_date", "Date of loss"],
              ["report_date", "Date reported"],
              ["valuation", "Valuation / as-of date"],
              ["paid", "Paid amount"],
              ["reserves", "Outstanding reserves"],
              ["incurred", "Reported total incurred"],
            ].map(([key, title]) => (
              <Field
                key={key}
                title={title}
                name={"claim_" + key}
                value={claimValue[key] ? pretty(claimValue[key]) : ""}
                type={key.includes("date") || key === "valuation" ? "date" : "text"}
                required={["claim_id", "carrier", "policy", "valuation"].includes(key)}
              />
            ))}
            <Field title="Claim status" name="claim_status">
              <select
                name="claim_status"
                defaultValue={String(claimValue.status ?? "unknown")}
              >
                {["unknown", "open", "closed", "reopened"].map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field
            title="Claim description"
            name="claim_description"
            type="textarea"
            value={claimValue.description ? pretty(claimValue.description) : ""}
          />
          <p className="muted">
            Leave missing amounts blank. A blank value is not a zero. Preserve reported
            incurred rather than recalculating it.
          </p>
        </>
      ) : (
        <Field
          title={
            exposure
              ? "Exposure amount"
              : coverageAmount
                ? "Coverage amount"
                : "Verified value"
          }
          name="value"
          type={
            field.startsWith("policy.") &&
            ["policy.effective", "policy.expiration"].includes(field)
              ? "date"
              : "textarea"
          }
          value={value === undefined ? "" : pretty(value)}
          required
        />
      )}
      <h3>Underwriting context</h3>
      <div className="form-grid">
        {visible.has("entity_id") && (
          <Field
            title="Entity / DBA row identifier (if supplied)"
            name="context_entity_id"
            value={String(context.entity_id ?? "")}
          />
        )}
        {visible.has("location_id") && (
          <Field
            title="Location row identifier (if supplied)"
            name="context_location_id"
            value={String(context.location_id ?? "")}
          />
        )}
        <Field
          title="Operation / location / entity scope"
          name="context_scope"
          value={String(context.scope ?? "all operations")}
          required
        />
        <Field title="Information describes" name="context_role">
          <select
            name="context_role"
            defaultValue={String(context.role ?? "unspecified")}
          >
            {[
              ["unspecified", "Not yet specified"],
              ["existing", "Existing coverage / policy"],
              ["requested", "Requested coverage"],
              ["historical", "Historical information"],
              ["actual", "Actual exposure"],
              ["estimate", "Estimated exposure"],
            ].map(([key, title]) => (
              <option key={key} value={key}>
                {title}
              </option>
            ))}
          </select>
        </Field>
        {currency && (
          <Field
            title="Currency (three-letter code)"
            name="context_currency"
            value={String(context.currency ?? "")}
            required={claim || coverageAmount}
          />
        )}{" "}
        {exposure && (
          <>
            <Field title="Exposure rating basis" name="context_basis">
              <select
                name="context_basis"
                defaultValue={String(context.basis ?? "")}
                required
              >
                <option value="" disabled>
                  Select the supplied exposure basis
                </option>
                {[
                  ["sales", "Gross sales"],
                  ["receipts", "Receipts"],
                  ["payroll", "Payroll"],
                  ["subcontract_cost", "Subcontractor costs"],
                  ["area", "Area"],
                  ["admissions", "Admissions"],
                  ["units", "Units"],
                  ["other", "Other"],
                  ["unknown", "Unknown — requires follow-up"],
                ].map(([key, title]) => (
                  <option key={key} value={key}>
                    {title}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              title="Exposure unit"
              name="context_unit"
              value={String(context.unit ?? "")}
              placeholder="USD, square feet, admissions…"
              required
            />
          </>
        )}
        {period && (
          <>
            <Field
              title="Period begins"
              name="context_period_start"
              type="date"
              value={String(context.period_start ?? "")}
              required={exposure || lossPeriod}
            />
            <Field
              title="Period ends (through)"
              name="context_period_end"
              type="date"
              value={String(context.period_end ?? "")}
              required={exposure || lossPeriod}
            />
          </>
        )}
        {field === "loss.period" && (
          <Field
            title="Report valuation / as-of date"
            name="context_as_of"
            type="date"
            value={String(context.as_of ?? "")}
            required
          />
        )}
        {field === "operations.class_code" && (
          <Field
            title="Carrier / rating classification system"
            name="context_classification_system"
            value={String(context.classification_system ?? "")}
            placeholder="Record the supplied system; do not cross-map codes"
          />
        )}
        {(field.startsWith("coverage.") || field.startsWith("policy.")) && (
          <Field
            title="Policy reference (if supplied)"
            name="context_policy_reference"
            value={String(context.policy_reference ?? "")}
          />
        )}
      </div>
      {Object.entries(context)
        .filter(([key]) => !visible.has(key))
        .map(([key, value]) => (
          <input
            type="hidden"
            key={key}
            name={"context_" + key}
            value={value === null ? "" : String(value)}
          />
        ))}
    </div>
  );
}

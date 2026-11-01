"use client";
import { useState } from "react";
const stages = [
  {
    title: "Gather the documents",
    tag: "01 / Intake",
    detail:
      "An application, exposure schedule, expiring policy and loss history belong to one submission.",
    status: "4 fictional source documents",
    rows: [
      ["Application", "PDF · received"],
      ["Exposure schedule", "XLSX · received"],
      ["Expiring policy", "PDF · received"],
    ],
  },
  {
    title: "See the difference",
    tag: "02 / Reconciliation",
    detail:
      "Two sales estimates describe the same period. Both remain visible until a broker reviews the evidence.",
    status: "Broker review required",
    rows: [
      ["Application estimate", "$1,200,000"],
      ["Schedule estimate", "$1,350,000"],
      ["Exposure basis / period", "Sales / 2027"],
    ],
  },
  {
    title: "Record the decision",
    tag: "03 / Review",
    detail:
      "A broker confirms the supported value and records the reason. The alternative and original sources remain in history.",
    status: "Illustrative broker decision",
    rows: [
      ["Selected estimate", "$1,350,000"],
      ["Support", "Schedule · Sheet 1, B4"],
      ["Decision", "Confirmed against source"],
    ],
  },
  {
    title: "Approve the package",
    tag: "04 / Package",
    detail:
      "Review the actual output, approve its exact version, then export it for manual submission.",
    status: "Approval is a separate step",
    rows: [
      ["Submission summary", "Original PDF"],
      ["Exposure schedules", "XLSX"],
      ["Sources and manifest", "ZIP"],
    ],
  },
];
export function Demo() {
  const [stage, setStage] = useState(1);
  const item = stages[stage];
  return (
    <section className="demo" aria-label="Fictional workflow demonstration">
      <div className="demo-header">
        <span>FICTIONAL WORKFLOW EXAMPLE</span>
        <span>GL / 2027</span>
      </div>
      <h3>Lakeshore Design Services</h3>
      <p className="demo-subtitle">Commercial general liability · Renewal</p>
      <div className="demo-body" aria-live="polite">
        <span className="eyebrow">{item.tag}</span>
        <h4>{item.title}</h4>
        {item.rows.map(([key, value]) => (
          <div className="demo-row" key={key}>
            <span>{key}</span>
            <strong>{value}</strong>
          </div>
        ))}
        <div className="demo-note">
          <b>{item.status}</b>
          <p>{item.detail}</p>
        </div>
      </div>
      <div className="demo-controls" aria-label="Demonstration stages">
        {stages.map((s, i) => (
          <button key={s.tag} aria-pressed={i === stage} onClick={() => setStage(i)}>
            <span>0{i + 1}</span>
            {["Upload", "Compare", "Review", "Package"][i]}
          </button>
        ))}
      </div>
    </section>
  );
}

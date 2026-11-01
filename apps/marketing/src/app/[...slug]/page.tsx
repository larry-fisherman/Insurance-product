import Link from "next/link";
import { notFound } from "next/navigation";
import { Demo } from "../../components/demo";
import { Inquiry } from "../../components/inquiry";

const pages: Record<
  string,
  { title: string; intro: string; sections: [string, string][] }
> = {
  product: {
    title: "The submission, connected.",
    intro:
      "A shared workspace for the documents, facts, questions and decisions behind a CGL submission.",
    sections: [
      [
        "Insured accounts and renewals",
        "Organize submissions by insured, assign ownership and track intended policy periods. Renewal candidates retain prior lineage and require fresh review.",
      ],
      [
        "Document intelligence with evidence",
        "Inspect parsed source text and proposed facts alongside original documents. Processing limitations and unsupported content stay visible. The local build uses a bounded parser; optional AI-provider interpretation needs separate validation.",
      ],
      [
        "Reconciliation in context",
        "Compare exposures, coverage and loss information within compatible periods, roles, bases, currencies and scopes. Material disagreement requires an attributable decision.",
      ],
      [
        "A package with a history",
        "Generate original PDF and XLSX summaries and a ZIP of supporting materials. Preview, approve and export the same files with source references and disclosed limitations.",
      ],
    ],
  },
  "how-it-works": {
    title: "A clear path through the paperwork.",
    intro: "Every stage has a purpose. Every material assertion needs support.",
    sections: [
      [
        "1. Upload and process",
        "Add supported PDF and XLSX documents to a CGL submission. Normal uploads remain in quarantine until scanning succeeds. Partial processing and unreadable content become visible follow-ups.",
      ],
      [
        "2. Reconcile and investigate",
        "Inspect differences across policy, exposure and loss information. Follow a candidate back to its exact source version, PDF page or spreadsheet cells.",
      ],
      [
        "3. Review and resolve",
        "Confirm evidence, correct a fact with fresh support or an attributed attestation, and assign missing-information tasks. Permitted exceptions need authorized acceptance; mandatory blockers stay blocked.",
      ],
      [
        "4. Generate, approve and export",
        "Freeze a reviewed revision and generate the candidate package. An authorized approver reviews the actual files and records approval. Export is for the broker’s manual submission; it does not establish carrier acceptance.",
      ],
    ],
  },
  "solutions/general-liability": {
    title: "Focused on general liability.",
    intro:
      "Commercial General Liability is the first supported domain. The initial workflow focuses on submission and renewal preparation.",
    sections: [
      [
        "Policies and coverage",
        "Keep existing, requested and historical coverage distinct. Preserve policy periods, limits, deductibles and supplied wording without inferring absent endorsements.",
      ],
      [
        "Exposures and operations",
        "Review supplied sales, payroll and subcontractor figures with their basis, units, currency and periods. Do not treat every exposure as revenue.",
      ],
      [
        "Loss history",
        "Retain distinct claims and valuation dates. Missing reports remain missing; an empty extraction does not mean no losses.",
      ],
      [
        "A defined scope",
        "Original summaries and schedules support broker preparation. Carrier transmission, rating, binding, licensed third-party forms and additional insurance lines are outside this release. Domain validation and customer-data acceptance remain open.",
      ],
    ],
  },
  security: {
    title: "Clear boundaries. Visible responsibility.",
    intro:
      "This is a local, synthetic-data application. The controls described here are engineering features, not a certification or approval for customer documents.",
    sections: [
      [
        "Agency access",
        "FastAPI resolves active membership and permissions for operations. PostgreSQL row security constrains business access through a non-owner runtime. Administrative access does not automatically grant package approval.",
      ],
      [
        "Private evidence and review history",
        "Private storage retains source versions and generated artifacts. Authorized file access, immutable revisions and exact-package hashes support attributable review and approval.",
      ],
      [
        "Uploaded content is untrusted",
        "Normal documents stay quarantined if scanning is unavailable. Parsing is bounded. Extraction produces candidates, and uploaded text cannot choose tools, endpoints or permissions.",
      ],
      [
        "AI and data handling",
        "Provider invocation happens server-side only when explicitly configured. Vendor terms, retention, residency and cost must be approved before data transmission. No public analytics receives document content.",
      ],
      [
        "Before customer-data use",
        "Managed identity deployment, independent parser isolation, hosted IAM/encryption, retention/deletion, recovery drills, representative domain evaluation and independent security assessment remain release gates. No SOC 2 or legal compliance claim is made.",
      ],
    ],
  },
  privacy: {
    title: "Data practices for this preview.",
    intro:
      "This local preview is for fictional documents and contact information. This page is not a production privacy notice.",
    sections: [
      [
        "Public inquiries",
        "The form stores the name, email, agency and context you enter in a separate local inquiry store. Do not include real insured or claims information. No marketing email or CRM delivery is connected.",
      ],
      [
        "Agency information",
        "Authenticated workspace records are scoped to the selected agency and current permissions. Source documents, review decisions and package history remain in private local storage.",
      ],
      [
        "Retention and access",
        "Local records persist until an authorized operator removes them. A production retention, deletion, legal-hold and customer data-return policy must be approved and exercised before real-data use.",
      ],
    ],
  },
  terms: {
    title: "Preview scope and responsibilities.",
    intro:
      "Use fictional information only. Commercial service terms require separate professional review before launch.",
    sections: [
      [
        "Broker judgment",
        "Extraction and reconciliation assist preparation. A broker must review material facts, resolve blockers and approve the exact output before normal export.",
      ],
      [
        "Submission-ready",
        "A reviewed package is prepared for manual broker submission. It does not guarantee carrier acceptance or replace carrier-required applications.",
      ],
      [
        "Scope",
        "This preview does not make coverage decisions, rate or bind insurance, transmit submissions, or reproduce licensed forms. No payment or commercial service agreement is created by using a local demo.",
      ],
    ],
  },
};
export function generateStaticParams() {
  return [...Object.keys(pages), "pricing", "request-demo", "contact", "thank-you"].map(
    (key) => ({ slug: key.split("/") }),
  );
}
export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string[] }>;
}) {
  const key = (await params).slug.join("/");
  return {
    title:
      pages[key]?.title ??
      {
        "request-demo": "Request a demo",
        pricing: "Pricing",
        contact: "Contact",
        "thank-you": "Inquiry received",
      }[key] ??
      "Page not found",
  };
}
export default async function Page({ params }: { params: Promise<{ slug: string[] }> }) {
  const key = (await params).slug.join("/");
  if (key === "thank-you")
    return (
      <section className="page-intro">
        <span className="eyebrow">Inquiry received</span>
        <h1>
          Thank you.
          <br />
          <em>Your inquiry is saved.</em>
        </h1>
        <p>
          The fictional inquiry is available for local operator review. No email has been
          sent.
        </p>
        <Link className="button" href="/how-it-works">
          Explore the workflow →
        </Link>
      </section>
    );
  if (["request-demo", "contact", "pricing"].includes(key))
    return (
      <section className="contact-layout">
        <div className="page-intro">
          <span className="eyebrow">
            {key === "pricing"
              ? "Agency subscriptions"
              : "Let’s talk through the workflow"}
          </span>
          <h1>
            {key === "pricing"
              ? "A plan for your agency."
              : key === "contact"
                ? "Start a conversation."
                : "See the submission take shape."}
          </h1>
          <p>
            {key === "pricing"
              ? "Pricing and commercial terms are being validated with agencies. Request pricing for your team and expected workflow."
              : "Explore the document-to-submission workflow using a fictional CGL example."}
          </p>
          <ul className="plain-list">
            <li>Agency workspaces with explicit team roles</li>
            <li>Evidence-linked reconciliation and review</li>
            <li>Original packages and recorded approval</li>
          </ul>
          <p className="fine">Local preview · Fictional inquiry details only</p>
        </div>
        <Inquiry
          purpose={key === "pricing" ? "pricing" : key === "contact" ? "contact" : "demo"}
        />
      </section>
    );
  const page = pages[key];
  if (!page) notFound();
  return (
    <>
      <section className="page-intro">
        <span className="eyebrow">
          CGL Workspace / {key.replaceAll("-", " ").replaceAll("/", " / ")}
        </span>
        <h1>{page.title}</h1>
        <p>{page.intro}</p>
      </section>
      {key === "how-it-works" && (
        <div className="demo-wide">
          <Demo />
        </div>
      )}
      <section className="editorial">
        {page.sections.map(([title, body], i) => (
          <article key={title}>
            <span className="section-number">0{i + 1}</span>
            <h2>{title}</h2>
            <p>{body}</p>
          </article>
        ))}
      </section>
      {!["privacy", "terms"].includes(key) && (
        <section className="cta">
          <h2>Make the workflow concrete.</h2>
          <Link className="button" href="/request-demo">
            Request a demo →
          </Link>
        </section>
      )}
    </>
  );
}

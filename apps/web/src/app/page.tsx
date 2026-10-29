import Link from "next/link";
export default function Home() {
  return (
    <div className="public-page">
      <nav className="public-nav">
        <Link className="brand" href="/">
          ▱ CGL Workspace
        </Link>
        <div className="actions">
          <Link className="text-link" href="/security">
            Security
          </Link>
          <Link className="button" href="/login">
            Sign in →
          </Link>
        </div>
      </nav>
      <main style={{ padding: 0 }}>
        <section className="public-hero">
          <div>
            <span className="eyebrow">
              Commercial general liability · Broker operations
            </span>
            <h1>
              From scattered
              <br />
              documents to a<br />
              clear submission.
            </h1>
            <p>
              Prepare accurate, reconciled, broker-approved underwriting packages. Bring
              your documents together, review the evidence, and keep control of every
              material decision.
            </p>
            <div className="actions">
              <Link className="button primary" href="/login">
                Explore the review workspace →
              </Link>
            </div>
            <small className="muted">
              Local review build · Fictional documents only · No carrier distribution
            </small>
          </div>
          <div className="preview-paper">
            <span>FICTIONAL WORKFLOW EXAMPLE</span>
            <h3>Lakeshore Design Services LLC</h3>
            <p>Commercial general liability · Renewal</p>
            <div className="preview-row">
              <span>Application estimate</span>
              <b>$1,200,000</b>
            </div>
            <div className="preview-row">
              <span>Sales schedule estimate</span>
              <b>$1,350,000</b>
            </div>
            <div className="preview-conflict">
              <span className="badge amber">Broker review required</span>
              <p>
                Different sales estimates for the same period. Compare the source
                documents and record the confirmed value.
              </p>
            </div>
            <div className="preview-row">
              <span>Final package</span>
              <span className="badge">Approval pending</span>
            </div>
          </div>
        </section>
        <section className="marketing-section">
          <span className="eyebrow">A focused path to placement</span>
          <h2>Your workflow. With the preparation connected.</h2>
          <div className="marketing-grid">
            <article>
              <span>01</span>
              <h3>Gather the underwriting evidence</h3>
              <p>
                Keep expiring policies, applications, loss runs, and exposure schedules in
                one submission. Track what is processed and what still needs attention.
              </p>
            </article>
            <article>
              <span>02</span>
              <h3>Resolve discrepancies with context</h3>
              <p>
                Compare amounts within their exposure basis and period. See source text
                beside proposed facts and record the reason behind each review decision.
              </p>
            </article>
            <article>
              <span>03</span>
              <h3>Approve the package you reviewed</h3>
              <p>
                Generate an original submission summary with supporting files. Approve a
                specific version, export it, and retain a record of what was approved.
              </p>
            </article>
          </div>
        </section>
        <section className="marketing-section">
          <span className="eyebrow">Professional oversight, by design</span>
          <h2>Built to support the broker’s judgment.</h2>
          <p>
            The workspace prepares information for your review. It does not make coverage
            decisions, bind insurance, or act as a licensed insurance professional.
            Missing loss runs remain missing, and material conflicts remain open until
            resolved.
          </p>
          <p>
            This review build uses a local document parser by default. A structured
            provider interface is available for approved AI services; connected-provider
            performance must be evaluated separately.
          </p>
          <Link className="text-link" href="/security">
            Read about data handling and launch requirements ↗
          </Link>
        </section>
      </main>
      <footer style={{ marginTop: 60 }}>
        <span>CGL Workspace · Commercial insurance operations</span>
        <span>Fictional review environment</span>
      </footer>
    </div>
  );
}

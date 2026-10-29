import Link from "next/link";
export default function Security() {
  return (
    <main className="public-page security-copy">
      <nav className="public-nav">
        <Link className="brand" href="/">
          ▱ CGL Workspace
        </Link>
        <Link className="button" href="/login">
          Open workspace →
        </Link>
      </nav>
      <span className="eyebrow">Security and data practices</span>
      <h1>
        Trust starts with
        <br />
        clear boundaries.
      </h1>
      <p>
        This is a local, synthetic-data review build. It is not approved for customer
        documents, paid pilots, or production use. The controls below are implemented
        software features, not a claim of legal compliance or SOC 2 certification.
      </p>
      <h2>Agency separation and access</h2>
      <p>
        Tenant-scoped database transactions and forced PostgreSQL row security separate
        agency records. Sessions select an agency through active membership. User roles
        control preparation, review, administration, approval, and export. Registered
        users enroll authenticator MFA; fictional example sessions explicitly bypass it.
      </p>
      <h2>Document handling</h2>
      <p>
        Private storage holds immutable document bytes. Normal uploads stay quarantined
        until malware scanning succeeds. PDF and spreadsheet parsing have file, expansion,
        page, and cell limits. The built-in example documents are server-signed synthetic
        fixtures. Source downloads check authorization and file integrity on each request.
      </p>
      <h2>Extraction and broker review</h2>
      <p>
        The default local parser handles a bounded set of explicit labels and abstains
        when it cannot validate a value. An optional structured extraction endpoint
        receives document text only when configured by the operator. Provider output is
        schema-validated and checked against cited source blocks. All material facts still
        require broker review.
      </p>
      <h2>Approvals and retention</h2>
      <p>
        Review decisions, canonical revisions, artifacts, and approvals retain their
        history. Approval binds the exact manifest and file hashes. Material changes
        require a new package. Local database and file volumes persist until the operator
        removes them; this build has no automatic retention purge.
      </p>
      <h2>Before real customer data</h2>
      <p>
        Production identity and MFA, HTTPS, managed encryption and keys, vendor data
        terms, retention and deletion policy, tested backup recovery, access reviews,
        incident response, and independent security testing must be completed. Qualified
        counsel and insurance professionals must review document-sharing authority,
        producer licensing boundaries, state privacy rules, contractual liability,
        E&amp;O, and any template licenses.
      </p>
      <p>
        No ACORD forms are reproduced. Templates in the workspace are original. The
        roadmap and outstanding launch gates are recorded in the repository documentation.
      </p>
    </main>
  );
}

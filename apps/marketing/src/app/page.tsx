import Link from "next/link";
import { Demo } from "../components/demo";
import "./home.css";
export default function Home() {
  return (
    <div className="home">
      <section className="hero">
        <div className="hero-media" aria-hidden="true" />
        <div className="hero-copy">
          <span className="eyebrow">
            Commercial general liability / Broker operations
          </span>
          <h1>
            Bring clarity to
            <br />
            your next <em>submission.</em>
          </h1>
          <p>
            Upload the documents. Find missing or conflicting information. Review the
            facts. Prepare a package your team can stand behind.
          </p>
          <div className="actions">
            <Link href="/request-demo" className="button">
              See the workflow <span className="hero-cta-arrow">→</span>
            </Link>
            <Link href="/how-it-works" className="text-link">
              How it works ↗
            </Link>
          </div>
          <small>Evidence stays connected. Brokers keep the final say.</small>
        </div>
        <a className="hero-scroll" href="#statement">
          Scroll down
        </a>
      </section>
      <section className="statement" id="statement">
        <span className="eyebrow">One shared submission workspace</span>
        <h2>
          The application says one thing.
          <br />
          The schedule says <span className="statement-pill" aria-hidden="true" />{" "}
          another.
          <br />
          <em>Make the difference visible.</em>
        </h2>
        <p>
          Keep policies, applications, exposure schedules and loss runs connected to the
          facts they support. Review amounts in context, follow up on gaps, and preserve
          the reason behind every material decision.
        </p>
      </section>
      <section className="promise">
        <div className="promise-copy">
          <h2>From documents to a reviewed submission</h2>
          <ol className="promise-list">
            <li>Upload the documents.</li>
            <li>Identify missing or conflicting underwriting information.</li>
            <li>Review the facts.</li>
            <li>Generate a submission-ready package.</li>
          </ol>
          <p className="promise-caption">
            A reviewed package is prepared for manual broker submission. It does not
            guarantee carrier acceptance or replace carrier-required applications.
          </p>
        </div>
        <div className="promise-demo">
          <Demo />
          <span className="annotation">
            From scattered sources to a recorded decision.
          </span>
        </div>
      </section>
      <section className="workflow-section">
        <div>
          <span className="eyebrow">A deliberate path to preparation</span>
          <h2>
            Evidence first.
            <br />
            Approval last.
          </h2>
          <Link className="text-link" href="/how-it-works">
            Explore each step ↗
          </Link>
        </div>
        <ol className="workflow-list">
          <li>
            <span>01</span>
            <div>
              <h3>Gather & understand</h3>
              <p>
                Keep original documents, processing outcomes and proposed facts together.
                See what is missing or unreadable.
              </p>
            </div>
          </li>
          <li>
            <span>02</span>
            <div>
              <h3>Compare & reconcile</h3>
              <p>
                Compare compatible periods, exposure bases and coverage roles. Inspect the
                original source before deciding.
              </p>
            </div>
          </li>
          <li>
            <span>03</span>
            <div>
              <h3>Review & approve</h3>
              <p>
                Record supported decisions, review the actual package and approve the
                exact files intended for export.
              </p>
            </div>
          </li>
        </ol>
      </section>
      <section className="security-band">
        <span className="eyebrow">Built for professional oversight</span>
        <h2>
          Your agency. Your evidence.
          <br />
          Your judgment.
        </h2>
        <p>
          Private document access, agency permissions and versioned approvals are part of
          the workflow. Read the implemented controls and the requirements still open
          before customer-data use.
        </p>
        <Link className="button light" href="/security">
          Read our data practices ↗
        </Link>
      </section>
      <section className="cta">
        <div>
          <span className="eyebrow">Start with your workflow</span>
          <h2>See how the pieces fit.</h2>
          <p>
            Walk through a fictional CGL submission, from conflicting estimates to a
            reviewed package.
          </p>
        </div>
        <Link className="button" href="/request-demo">
          Request a demo →
        </Link>
      </section>
    </div>
  );
}

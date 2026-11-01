import Link from "next/link";
export default function NotFound() {
  return (
    <section className="page-intro">
      <span className="eyebrow">404</span>
      <h1>This page isn’t here.</h1>
      <p>Explore the product or return to the homepage.</p>
      <Link className="button" href="/">
        Back to home →
      </Link>
    </section>
  );
}

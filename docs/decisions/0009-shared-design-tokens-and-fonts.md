# ADR 0009: Shared design tokens, build-time fonts and status colors

Date: November 4, 2026. Status: accepted for the local synthetic build.

## Context

The application and marketing builds each held their own colors in `globals.css`, mostly as hard-coded values that bypassed their few custom properties. A palette change meant editing hundreds of lines in two files. Green was both the brand direction and the color for every positive status, so "approved" looked the same as "scan clean" or "job completed".

## Decisions

**One token file, shared by both builds.** `packages/web-config/tokens.css` defines every color, scrim, shadow and status token once: a raw palette layer, then a semantic layer that stylesheets use. Both root layouts import it before `globals.css`, and both Dockerfiles already copy `packages/web-config`. It is plain CSS and carries no operations configuration across the public/app build boundary. `apps/web/tests/design-tokens.test.ts` recomputes the contrast of each allowed pairing, records the pairings that must not be used, and fails if a stylesheet holds a stray color.

**Fonts are fetched at build time.** Nunito and DM Sans load through `next/font/google` in each root layout. The files are served from `/_next/static/media`, so pages make no runtime request to Google and the application CSP does not change. The cost is that `npm run build` and the Docker builds need network access. If builds must work offline, switch to `next/font/local` with committed font files. The fonts are visual matches for a reference, not confirmed names. Numeric values shown in rows or columns use Nunito (`--font-display`) with `font-variant-numeric: tabular-nums`, because DM Sans digits vary in width even with that setting while Nunito's measured the same width. That scope is `.metric strong`, `.fact-value`, `.review-summary > strong`, `.claim-facts dd`, `.demo-row strong` and the raw spreadsheet value column (`.raw-value`). Facts inside running text, such as `.snapshot-facts`, stay in DM Sans, as do body and nav text.

**Approved is blue.** Green now belongs to the brand, so it can no longer carry status meaning. Approved and exported are the only values with the solid blue tone, so that styling only ever means the exact package was approved. Completed, clean, approve and correct take a neutral "done" tone. Each `Status` badge keeps its exact text label. The approved, done, error, conflict, warning and missing tones also carry a CSS pseudo-element glyph (✔, ✔, ✕, ⇄, ▲ and ?), so badge text stays exact. Neutral badges have no glyph and rely on their text label. The blocker `!` is page text in `submission.tsx`, not a pseudo-element. Some treatments outside `Status` are color or border only: the dashboard's discrepancies metric, which takes the conflict color from `.dashboard-metrics` and is identified by its label, `.error-text` and `.conflict-card`.

**Home layout is separate.** `apps/marketing/src/app/home.css` is imported only by the home page, with every rule scoped to `.home`. Signed-in pages and login take the palette, fonts and status colors with no layout change.

**The hero has a swappable image slot.** `--hero-media-image` defaults to a palette gradient and uses no asset. A licensed local image can replace it by changing that one variable. The scrim stays on top at 0.65, so hero text stays legible over any image; the test checks the worst case of a white pixel. 0.65 is the lowest step that passes there (0.60 fails), chosen so more of the green shows when there is no photo.

## Limitations

- `missing` has a status tone, but no current `Status` call receives that value. The backend uses it as a discrepancy kind, which is not rendered through `Status`.
- Sections of the home page below the statement follow an inferred order.
- Text sizes of 8-10px in signed-in pages are unchanged.
- Nav links stay hidden at 1000px and below, and marketing still has no CSP.

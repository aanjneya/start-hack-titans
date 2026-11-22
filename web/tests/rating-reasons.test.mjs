import assert from "node:assert/strict";
import test from "node:test";
import { reasonsForRating } from "../app/observability/risk-explanations.ts";

const names = { product: {}, part: {}, supplier: {} };
const proof = (path, quote = "", locator = "") => ({ path, quote, locator, sha256: "" });
const market = (slug, title, yes, extra) => ({
  provider: "polymarket", live: true, title, evidence: proof(`https://polymarket.com/event/${slug}`),
  market: { yes_pct: yes, disruption_outcome: "Yes" }, ...extra,
});

test("the market reason shows only its retained inputs and their recorded outcome prices", () => {
  const a = market("exports", "Will export rules tighten?", 72, { live: false });
  const b = market("normal", "Will shipping return to normal?", 8.5, {
    last_known_at: "2026-11-21T12:00:00Z", market: { yes_pct: 8.5, disruption_outcome: "No" },
  });
  const [reason] = reasonsForRating({reasons: [
    { factor: "external_politics", text: "Polymarket", evidence: [a.evidence], signal_inputs: {
      external_politics: [a, b], regulatory_change: [market("unrelated", "Another market?", 99)],
    } },
  ]}, names);
  assert.deepEqual(reason.sources.map(s => [s.detail, s.mode, s.href]), [
    ["Will export rules tighten? · Yes 72%", "Offline example", undefined],
    ["Will shipping return to normal? · Yes 8.5% · disruption side No 91.5%", "Last known reading · 2026-11-21", b.evidence.path],
  ]);
});

test("inherited citations name the event and never turn a risk score into a quoted price", () => {
  const path = "https://polymarket.com/event/red-sea-rerouting";
  const rows = reasonsForRating({reasons: [
    { factor: "supplier_risk", text: "Supplier exposure", evidence: [proof(path, "Yes=68% (offline Polymarket snapshot)")] },
    { factor: "supplier_risk", text: "Supplier exposure", points: 88, evidence: [proof(path, "Risk score 88/100")] },
    { factor: "internal_politics", text: "An internal approval is open.", evidence: [proof("issue://31")] },
  ]}, names);
  assert.equal(rows[0].sources[0].detail, "Event · red sea rerouting · Yes 68%");
  assert.equal(rows[0].sources[0].href, undefined);
  assert.equal(rows[1].sources[0].detail, "Event · red sea rerouting · Price unavailable");
  assert.equal(rows[2].sources[0].findingId, 31);
});

test("a source named on the product card supports the displayed export claim", () => {
  const [reason] = reasonsForRating({reasons: [{
    factor: "supplier_health", text: "Vendor: supply suspension/export-licence notice",
    evidence: [proof("suppliers/SUP-0001_Vendor/profile.csv", "status=at risk"),
      proof("suppliers/SUP-0001_Vendor/notices/Export_licence.pdf", "Export licence required", "page 2")],
  }]}, names);
  assert.equal(reason.sources[0].label, "Export licence");
  assert.equal(reason.sources[0].detail, "page 2");
  assert.equal(reason.sources[1].label, "Vendor · supplier profile");
});

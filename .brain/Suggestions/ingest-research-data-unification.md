# Research: how companies turn messy data into one truth (for agent 2, Ingest & Normalizer)

> Web research Sat 21 Nov ~19:20 (daksh). Mostly from vendor docs: describes what they say, not independently verified.
> Applied as tasks T2.11–T2.18 in `.brain/tasks/2-ingest-normalizer.md`.

## How the leaders do it
- **Rossum** (order confirmations/invoices): sort documents by type into queues → template-free field extraction → validation (business rules, duplicates) → "to review" UI → export to ERP. Confidence 0–1 per field; default auto-pass threshold 97.5% (per queue/field). Learns **only from human corrections**, not its own earlier guesses. [how it works](https://rossum.ai/blog/how-rossum-works/) · [thresholds](https://knowledge-base.rossum.ai/docs/using-ai-confidence-thresholds-for-automation-in-rossum)
- **Hyperscience / Instabase / ABBYY:** low-confidence results become a human "supervision" task (thresholds per step/field). Instabase builds confidence from **checks**: format regexes, arithmetic (total = subtotal + tax), business rules, ERP lookup that the entity exists. Microsoft Content Understanding grounds every value to its source location. [Hyperscience](https://help.hyperscience.ai/docs/v38-what-is-supervision) · [Instabase](https://www.instabase.com/blog/overcoming-the-limitations-of-llms-data-validation-and-human-review) · [Microsoft](https://devblogs.microsoft.com/foundry/content-understanding-confidence-grounding-hitl/)
- **Tamr** (entity resolution): blocking (narrow candidates) → pairwise match model → clustering → golden record; unsure pairs go to an expert (active learning). [Tamr](https://cloud.docs.tamr.com/docs/about-tamrs-ai-native-approach)
- **Informatica MDM** (survivorship): trust score per source and field; ties broken by source rank, then last update; all source records kept as cross-references; golden record is a view. [Informatica](https://onlinehelp.informatica.com/IICS/prod/mde/en/cg-c360-bvt-records/Establish_the_best_version_of_the_truth.html)
- **Palantir Foundry Ontology:** one clean dataset per object type with a unique key, links by shared keys (join tables for many-to-many), full lineage. [Palantir](https://palantir.com/docs/foundry/ontology/core-concepts/)
- **project44 / FourKites:** normalize every carrier's feed into one event/status model, fill gaps from history, fix bad timestamps, score each carrier's data quality. (Exact status lists not public.) [project44](https://www.project44.com/platform/tracking-etas) · [FourKites](https://www.fourkites.com/blogs/the-new-standard-in-carrier-performance/)
- **Conexiom** (orders from email/PDF/Excel): extract → normalize units, currency, part numbers → rules & lookups → ERP; failures to manual review. [Conexiom](https://conexiom.com/platform/ai-and-automation)
- **LLM research:** schema matching works best as *candidates first, LLM decides* (Magneto, SCHEMORA); give column name + description + sample values, not too little or too much context. Entity resolution: blocking → LLM picks among candidates in one prompt. [Magneto](https://arxiv.org/html/2412.08194v2) · [SCHEMORA](https://arxiv.org/html/2507.14376v1) · [context study](https://arxiv.org/pdf/2407.11852)

## Top 8 techniques for us (value / effort)
1. **Learn each file format once:** hash the normalized headers; first time Claude maps headers + 5 sample rows + our tables + SAP glossary; save the mapping; same headers next time = plain code, no AI call. (Caching by header signature: common practice, no authoritative source found.)
2. **Plain-code parsing after mapping:** sniff delimiter; mapping decides decimal comma and date format; reject unparseable values, don't guess.
3. **Classify PDFs first** (order confirmation / notice / contract / other), then a type-specific extraction schema.
4. **Grounding:** every extracted value comes with the exact quote + page; code checks the quote is really in the PDF text.
5. **Confidence from checks:** PO exists, qty × price ≈ total, dates plausible, quote found. Below threshold → `human_check` issue (review queue).
6. **Supplier/part matching:** saved aliases first → strip legal suffixes → fuzzy top-5 → Claude picks one or "none" with reason → confirmed matches saved (learn from confirmations).
7. **Survivorship:** current value by source trust, then confidence, then newest; disagreements kept and shown.
8. **One canonical shipment status list:** map ERP `arrived_port` / tracker `in transit` / carrier words to one event list.

**Pitch angle:** "Rossum-style document reading + Tamr-style matching, but as agents that act on the result, set up per company in hours."

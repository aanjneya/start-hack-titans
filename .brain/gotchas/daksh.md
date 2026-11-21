# Gotchas (daksh)

- **`\b(SUP-\d{4})\b` never matches supplier folders** (2026-11-21): in `suppliers/SUP-0028_Karton_Nowak/…` the `_` after the digits is a word character, so there is no word boundary → supplier id silently empty. Fix: `(SUP-\d{4})(?!\d)` (`backend/agents/ingest.py: SUP_RE`).
- **Timezone-aware datetimes come back from SQLite as naive UTC** (2026-11-21): SQLModel stores `10:00+01:00` as `09:00`, so comparing with the original made every rerun look like a change. Fix: compare aware values in UTC without tzinfo (`store._as_text`); also SQLModel rejects naive datetimes → `parsing.parse_datetime` attaches Europe/Berlin.
- **Two sources for the same row flip values back and forth on every rerun** (2026-11-21): e.g. `launch_plan` vs `launch_status`, `profile.csv` vs `profile.json`, `components.csv` vs the change file. Fix: a source that already lost with the same value can't overwrite again (`IngestNormalizer._drop_stale`); test `test_same_drop_twice_changes_nothing`.
- **Line endings** (2026-11-21, see Tim's gotcha): Windows commits turned brain files into CRLF → whole-file diffs and merge conflicts. Fixed by `.gitattributes` (`* text=auto eol=lf`, portal data mirror excluded), on main since 21:19.
- **AI column mapping invents matches** (2026-11-21, live OpenAI run): asked to map `ports.csv`/`sites.csv`, the model put ports into `shipments` and the Hamburg DC into `suppliers`. Fix: `ingest_mapping.validate_mapping` (required fields, value types, key shapes like existing ids) rejects such mappings.
- **OCR misreads European thousands** (2026-11-21): "125.000" came back as 125. Fix: model copies `quantity_as_printed`, our `parse_number` converts; hand corrections in `quantity_corrected_by_hand`.
- **Windows backslashes in stored paths** (2026-11-21, from Tim): `str(Path)` → `soony\erp\…`; use `.as_posix()` for every path that goes into the DB or evidence.


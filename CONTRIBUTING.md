# Contributing

Use Python 3.11 or newer. The runtime uses only the standard library.

```powershell
python -m pip install --no-deps -e .
python -m unittest discover -s tests -v
python -m model_lifecycle_radar --help
```

Tests must be offline and deterministic. Inject the clock and fetcher rather than calling live endpoints from tests. Keep official-source fixtures small, add their source URL and capture date to `tests/fixtures/provenance.json`, and do not commit full vendor pages. Every parser must require the provider's labeled fields or table structure, reject empty/shape-drifted results, preserve exact IDs, and keep replacement/date claims tied to source rows.

Collection changes must preserve last-good state on failure, avoid turning disappearance into retirement, and keep public documentation coverage separate from account access. Changes to external schemas are review reminders; do not label them breaking without explicit evidence.

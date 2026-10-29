# Project notes

Working notes from building openPIP 2.0: how the migration was planned, what
the data contained, and design documents for larger features. They record how
decisions were reached and are kept as written, so some describe plans that
later changed. The current behaviour is in the documentation site (`docs/`)
and the open issues and decisions in `docs/BUGS.md`.

| Note | Contents |
|---|---|
| `MIGRATION_STRATEGY.md` | Phase 1 (parity) and Phase 2 plan, and the parity testing protocol |
| `DATA_MODEL.md` | The legacy schema and the MySQL to PostgreSQL type translation |
| `SCHEMA_IMPROVEMENTS.md` | Schema changes considered for after Phase 1 |
| `REFERENCES.md` | Standards and external services the project relies on |
| `DATA_PROVENANCE_QUESTIONS.md` | Open questions about where the bundled data came from |
| `dataset-audit-2026-06-14.md` | Audit of the datasets loaded from the original openPIP |
| `psicquic-registry-enquiry.md` | Draft enquiry about listing openPIP in the PSICQUIC registry |
| `plans/`, `specs/` | Design specs and implementation plans: 3D structure viewer, profile and network sharing |
| `generalization/` | Fork-only project (MilesGithub/openpip-2.0): audit, design and plan for typed entities, relationships and dataset browsers |

The Python CLI and SDK that were started and then shelved (August 2026) are not
in the repository. They remain in git history before this note was added.

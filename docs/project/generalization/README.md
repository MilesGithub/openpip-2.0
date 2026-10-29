# Generalization project

Working documents for turning openPIP 2.0 into a typed, evidence-centred
biological relationship platform: a unified explorer, a gallery of
dataset-specific browsers, and independent lab portals, all from one codebase.
Development happens only in the fork `MilesGithub/openpip-2.0`.

**Status:** Phase 0 is complete, and the owner's decisions of 2026-10-29 are
applied, followed by a consistency review of the updated documents
(architecture §14). Research-only due diligence on the five priority datasets
is recorded, and the questions it raised are decided. T1 (CI) is merged
into the fork's `main` (PR #1). The legacy reset warning is open as PR #2.
G1, the first implementation PR, comes next; nothing else described here as
proposed has been implemented yet.

| File | Contents |
|---|---|
| [PROJECT_BRIEF.md](PROJECT_BRIEF.md) | The owner's brief: requirements and direction |
| [CURRENT_ARCHITECTURE_ASSESSMENT.md](CURRENT_ARCHITECTURE_ASSESSMENT.md) | What the code does today, the baseline test results, and the assumptions that block generalization |
| [GENERALIZATION_ARCHITECTURE.md](GENERALIZATION_ARCHITECTURE.md) | The proposed data model, decisions and alternatives, migration strategy, and API and interface contracts |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Phases, the dependency-ordered issue breakdown, the first slice and the first PR |
| [TEST_PLAN.md](TEST_PLAN.md) | Regression guards for existing behaviour and tests for the new behaviour |
| [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | Unresolved scientific, licensing, identity and product questions, each with a safe default, and the owner's decisions |
| [DATASET_DUE_DILIGENCE.md](DATASET_DUE_DILIGENCE.md) | Research-only due diligence on the first candidate datasets and the reference sources they need: scientific fit, formats, identifiers, mapping needs, licensing and difficulty |

These follow the convention of the rest of `docs/project/`: they record how
decisions were reached, and the published documentation describes behaviour
only once it is implemented.

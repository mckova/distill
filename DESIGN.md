# Distill design language

Reading this as: an operations-intelligence workspace for ops leads and analysts who come back daily,
in the visual language of a written analyst's brief. Dials: ENERGY 1 / RHYTHM 2 / MOTION 1.
Density standard, data complexity 2, consequence 2 (reversible edits; deletes are confirmed).

## Decisions and why

| Decision | Reason |
|---|---|
| Bone paper ground, forest off-black ink | A report you read, not a console you stare at; warm-cool neutral keeps long reading calm. |
| One accent: amber | Marks "you are here" and "look at this" (active nav, selected row, focus ring, selection). Nowhere else. |
| Forest and brick as the only state colours | Good outcome / needs attention. They also form the two chart series, so colour means the same thing everywhere. |
| IBM Plex Sans + IBM Plex Mono | Drawn for technical documentation, so findings read like an engineering report. Mono is used only for figures, identifiers and code, where alignment matters. |
| Rows and hairlines instead of cards | Findings, automations, sources and skills are records to scan and compare, so they are rows with dividers. Panels appear only where a region floats beside the work (agent review, process map, skill reader). |
| A rule under every section title | The identity motif: each section is a ledger heading, the same gesture on every screen. |
| List-detail for findings and skills | The job on those screens is "pick one, read it, decide". The list stays visible while the decision is made. |
| Figures in a single ruled line | The numbers support the findings, so they sit inside the section they explain, not as a row of tiles at the top. |
| Radius 4 / 6 / 10 | Small for controls, larger only for floating surfaces. |
| Shadow only on dialogs, toasts and the composer | Elevation means "this floats above the page" and nothing else. |
| Light or dark follows the system | Both themes are designed and tested; contrast is checked in each. |

## Motion

Press feedback (scale 0.97, 140 ms), dialog and toast entrance (opacity plus 3% scale or 8 px rise, 200 to 240 ms,
ease-out `cubic-bezier(0.23, 1, 0.32, 1)`), new chat messages rise 6 px. Nothing loops except the loading
skeleton, and reduced-motion removes all movement. Hover effects are gated to fine pointers.

## Copy

Controls name their action ("Turn into automation", "Dismiss finding"). Empty states say why they are empty and
offer the one action that fills them. No em dashes, no emoji, no badges that restate a heading.

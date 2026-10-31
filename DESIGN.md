# Distill design language

Reading this as: an operations-intelligence workspace for ops leads and analysts who open it every morning to
find out what needs a decision. The home page answers that question first; everything else is one click away.
Dials: ENERGY 1 / RHYTHM 2 / MOTION 1. Density standard, data complexity 2, consequence 2 (reversible edits;
deletes are confirmed).

## Decisions and why

| Decision | Reason |
|---|---|
| Home page leads with the three things that need a decision | The job on opening the app is "what do I deal with today". Each card carries its weekly cost and one action, so a decision takes one click. |
| Findings as a ranked list with a detail panel beside it | Pick one, read the evidence, act, move to the next. The list stays visible so triage is fast. |
| Cost has its own column in the findings list | Rank alone says nothing about money. Hours or revenue per week lets the list be scanned by what each finding costs. |
| Frosted-glass panels over a soft warm-to-cool ground | Translucency gives depth without heavy borders or shadows, and the content stays the focus. |
| Palette: lagoon, clay, with sand and mist as washes | Lagoon is the single action colour (buttons, selection, good outcomes). Clay is reserved for money and risk. Washes never carry meaning. |
| One primary button per card or panel | The main action is obvious. Secondary actions are quiet glass buttons. |
| Mona Sans, weights 350 to 600 | A refined grotesque with a light weight for large figures and a steady medium for labels. Code uses the system monospace. |
| Text status instead of badges and icons | Status words ("Draft ready", "New") read faster than coloured chips, and the interface stays quiet. |
| Radius 12 / 18 / 26 | Controls, rows and panels each get a step, so nesting always reads. |
| Light or dark follows the system | Both themes are designed and checked for contrast. |

## Motion

Press feedback (scale 0.97, 140 ms), dialog and toast entrance (opacity plus 3% scale or 8 px rise, 200 to 240 ms,
ease-out `cubic-bezier(0.23, 1, 0.32, 1)`), new chat messages rise 6 px. Nothing loops except the loading
skeleton, and reduced-motion removes all movement. Hover effects are gated to fine pointers.

## Copy

Controls name their action ("Draft automation", "Dismiss finding"). Empty states say why they are empty and
offer the one action that fills them. No em dashes, no emoji, no badges that restate a heading.

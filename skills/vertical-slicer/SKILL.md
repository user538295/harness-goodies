---
name: vertical-slicer
description: Decompose a feature into an ordered set of vertical slices — each delivering one user-observable behavior end-to-end, the first a walking skeleton — and never drift into horizontal or hybrid slicing. Use whenever the user wants to break a feature, story, epic, or initiative into increments, slice work for delivery, order delivery phases, or asks how to avoid layer-by-layer (horizontal) slicing or a "set up the foundation first" plan. Every slice is self-checked against a four-part verticality test and re-sliced if it fails. Architecture-agnostic and fully standalone.
license: MIT
---

# Vertical Slicer

Turn a feature into an ordered list of **vertical slices** — each delivering one user-observable behavior end-to-end, the first being a walking skeleton — and keep every slice vertical by testing it and re-slicing any that drift horizontal or hybrid. Works with whatever architecture or layering the project already uses. Self-contained.

## Procedure

1. **Load the rules.** Read `references/vertical-slicing.md` — the four-part verticality test, the horizontal/hybrid smell list, the behavior-first order, and the cut-thinner toolkit. Apply it throughout; do not restate it.
2. **List behaviors, not components.** From the feature, enumerate the user-observable behaviors ("the user can now X" / Given-When-Then). Do **not** list layers, services, screens, or files — that is the horizontal trap.
3. **Pick the walking skeleton (slice 1).** Choose the thinnest behavior that still runs end-to-end and produces something a user sees. It carries the minimal data/model foundation **inside** that behavior — never as a standalone setup phase.
4. **Order the rest** by user value and hard dependency, thinnest-viable first.
5. **Map each behavior down into tasks** across the layers it actually needs — only now, after the slices exist — to confirm each slice is genuinely end-to-end.
6. **Self-check and re-slice (loop).** Test every slice against the verticality test **and** the smell list (passing the four tests is not enough — slices can pass them *in costume*; the smell list is the backstop). Re-slice any failure with the cut-thinner toolkit (SPIDR / reduce one source of variation) — **never** by splitting along layers — and re-test. Then run the **plan-level check** (Section 3, step 6) over the whole list. Consult `references/exemplars.md` when a slice is borderline. Loop until every slice passes.

## Output

An ordered slice list, walking skeleton first. For each slice emit:
- **Title** — the outcome (verb + object).
- **Behavior** — a Given/When/Then.
- **Layers touched** — the end-to-end path through the project's actual structure.
- **Verticality** — the four tests **and** the smell check (Sections 1–2) marked pass; ship a slice only when all pass.

No "setup/foundation" slice and no terminal "integrate" slice may appear.

## References

- `references/vertical-slicing.md` — the verticality test, smell list, behavior-first order, and cut-thinner toolkit. Read at step 1 and at every self-check.
- `references/exemplars.md` — contrasting good/bad slicings across three domains. Consult when a slice is borderline or fails.

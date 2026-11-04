# Vertical slicing — the test, the smells, the order

The shared core of this skill. A **vertical slice** delivers one user-observable behavior end-to-end; a **horizontal slice** delivers one architectural layer (and no usable behavior on its own). Agents drift horizontal because layered code is what they have seen most and because splitting by layer is mechanically easy. This card is the forcing function that keeps every slice vertical.

**Architecture-agnostic.** "Layer" here means *whatever structural units this project actually uses* — Clean Architecture layers, MVC tiers, feature folders, client/server, services. Do not impose a layering; read the project's own and slice across it.

**Altitude — read this first.** Verticality is a property of the **slice**, not of the individual **task**. A single task is normally one role × one layer — that is correct and expected. The *slice* is vertical because its tasks **together** cross the layers to deliver one demoable behavior. Do not try to make each task vertical (that produces mush); make each **slice** vertical.

---

## 1. The verticality test — a slice is vertical only if ALL four hold

1. **Demoable as a real instance of *this* feature** — you can write a Given/When/Then that a non-developer recognizes as the feature's *intended user* doing a thin but genuine instance of the feature. An internal admin / maintenance / diagnostic action that merely exercises new infrastructure does **not** count, even if it shows a toast.
2. **End-to-end** — it *exercises* every layer the behavior needs (each layer does its real job and its output is consumed downstream), not just one. A layer that is present but stubbed out does not count as exercised.
3. **Independently shippable in principle** — delete every other slice and this one still delivers value **to the feature's intended user** (INVEST *Independent* + *Valuable*) — not merely to an operator who can now run a maintenance task.
4. **Named as an outcome** — the title is verb + object ("Reserve a seat", "Reject an expired code"), never a layer or tech noun.

If any of the four fails, the slice is horizontal or hybrid → re-slice it (Section 4). **Passing all four is necessary, not sufficient:** a slice must also trip none of the Section 2 smells — those are the backstop for slices that pass the four tests *in costume*.

---

## 2. Horizontal / hybrid smells — any ONE present ⇒ re-slice

- **Layer/tech-noun title:** *Database, Schema, Models, Backend, API, Endpoints, UI, Frontend, Infrastructure, Setup, Scaffold, Plumbing, Integration, Wiring.*
- **Single-layer slice:** every task in the slice is one role or one layer.
- **Foundation-first trap:** a first "set up the database / scaffolding / shared infra" phase that delivers no demoable behavior. *This is the most common hybrid failure.*
- **Infrastructure-in-costume:** the slice's only visible outcome is an admin / maintenance / reindex / health-check / diagnostic action that exercises a newly-built layer but lets the feature's *intended user* do nothing new — e.g. "Reindex documents from the admin panel" as slice 1 of a user-search feature. It is the foundation-first trap with a demoable wrapper bolted on. Re-slice so slice 1 lets the intended user do the thinnest real instance of the feature (e.g. "Find a document by exact title").
- **Spike-as-deliverable:** the slice's real purpose is to *learn / de-risk / prove a technology*, not to leave a capability the user keeps — tells: the actor is a developer or QA; the outcome is a test-mode charge, a sample payload, or a proof in a dashboard/log; the artifact is thrown away once the answer is known. Spikes are legitimate but are **not** shippable slices: bank the learning, then cut the real slice.
- **Proves nothing:** a slice with no behavior/scenario attached — you cannot demo it.
- **Terminal integrate phase:** a final "integrate / wire it all up" phase. Its existence proves the earlier phases were horizontal; a vertical plan integrates by construction.
- **Layer-ordered sequence:** the slice order reads "all repositories → all services → all controllers".
- **Sacrificial-proxy ordering:** the leading slice(s) deliver a *different, lower-value* feature whose main justification is forcing shared infrastructure to exist before the requested feature. Each proxy slice may itself look vertical — the tell is at the plan level (Section 3, step 6).
- **Stub-floor / Potemkin layer:** a layer named in *layers touched* is a hardcoded return, canned response, or no-op that never does its real work — e.g. a repository that is imported but never queried, a payment call that always returns success. The slice looks end-to-end but is not. Re-slice to a behavior thin enough that every layer it lists does real work, or honestly drop the unexercised layer from the path.
- **Over-built skeleton:** the slice smuggles in reusable infrastructure sized for many future behaviors (a full search cluster, a generic rules engine) behind one thin behavior. Tell: removing the later slices would not shrink this slice. Keep slice 1 to the infrastructure *this one behavior* needs; let later slices add their own.

---

## 3. Behavior-first order — the generation guardrail

Produce slices in this order. It makes horizontal slicing structurally hard, because behaviors (not components) are the unit:

1. **List user-observable behaviors** — scenarios, "the intended user can now X". *Not* components, *not* layers.
2. **Pick the thinnest end-to-end behavior → the walking skeleton (slice 1).** It exercises all the layers with near-zero business logic and **carries the data/model foundation inside that behavior** — never as a standalone setup phase.
3. **Order the remaining behaviors** by user value and hard dependency.
4. **Only now** map each behavior down into its per-layer tasks.
5. **Self-check each slice** against Section 1 + Section 2; re-slice any failure (Section 4); repeat until all pass.
6. **Plan-level check** (over the whole list, not slice-by-slice): every slice must advance the feature the user *actually requested*, for its intended user. If a leading slice — or a run of them — belongs to a *different, lower-value* feature whose main justification is forcing shared infrastructure to exist earlier, that is foundation-first laundered through a proxy. Reorder so slice 1 is the thinnest real instance of the *requested* feature, carrying its own foundation inside it (step 2).

---

## 4. Cut thinner without going horizontal

When a behavior is too big to be one slice, do **not** split it by layer to shrink it. Split it while keeping it vertical.

**Meta-rule (the one heuristic to remember):** find the biggest *source of variation* in the behavior and reduce it to one. Every pattern below is an instance of that.

**SPIDR** — almost any behavior splits one of five ways:
- **Spike** — a time-boxed investigation, only to *learn*. Never a shippable slice; it removes uncertainty so the real slices can be cut.
- **Paths** — support one path through the behavior now, others later (pay by card now; Apple Pay later).
- **Interfaces** — one client / format / device now, others later (web now; mobile later).
- **Data** — one data subset or type now, more later (domestic addresses now; international later).
- **Rules** — the core business rule now, the edge rules later (happy-path validation now; the exotic discount rules later).

Each resulting piece must still pass the Section 1 test — demoable, end-to-end, independently valuable, outcome-named.

---

*Grounding: vertical/horizontal slicing and INVEST (Bill Wake, 2003); the walking skeleton and Elephant Carpaccio (Alistair Cockburn; Henrik Kniberg's facilitation guide); SPIDR (Mike Cohn, Mountain Goat Software); the "reduce a source of variation" meta-pattern (Richard Lawrence / Humanizing Work story-splitting flowchart).*

# TypeSpec tooling (contracts)

How to **detect** TypeSpec, **author** `.tsp` contract files, **validate** them, and — for HTTP/API seams — **emit an `openapi.yaml`** from them. Consult the web **only** if a command fails and you are genuinely stuck (last section) — never as the first move.

Contracts come in two kinds; they are authored differently:

- **Internal logical seam** (e.g. an in-process store interface) → a standalone **core-construct** `.tsp`, validated with `--no-emit`. No OpenAPI.
- **HTTP/API seam** (client↔server, REST) → a TypeSpec **HTTP service** `.tsp` that additionally **emits an `openapi.yaml`** beside it via the `@typespec/openapi3` emitter.

## Detect (Step 1)

Run `tsp --version`. Exit code `0` with a version printed → TypeSpec is **available**: author contracts as `.tsp` and tell the user. Command not found / non-zero exit → **not available**: use the built-in inline fallback (rule 5) and tell the user.

A failed detection is **not** "stuck" — it has a defined answer (the built-in fallback). Do not web-research it.

OpenAPI emission needs two more libraries (`@typespec/http`, `@typespec/openapi3`). These are **not** part of a base `tsp` install; the project setup below installs them. If they cannot be installed (e.g. offline), author HTTP contracts as core-construct `.tsp` like an internal seam, skip the `openapi.yaml`, and **tell the user** no OpenAPI was produced.

## Author — internal logical seam (core constructs only)

A logical contract states *what crosses a seam*, logically. Use only **core** TypeSpec: `namespace`, `model`, `enum`, `union`, `interface` / `op`, and built-in scalars (`string`, `boolean`, `int32`, `utcDateTime`, …).

Do **not** `import "@typespec/http"` / `"@typespec/rest"` / `"@typespec/openapi"`, and do **not** use routing decorators here. Core constructs need no project and validate on their own.

Minimal logical contract (`<contract-name>.tsp`, written beside the plan):

```tsp
namespace SelectionContract;

model Selection {
  itemId: string;
  selectedAt: utcDateTime;
}

interface SelectionStore {
  select(itemId: string): Selection;
  current(): Selection | void;
}
```

Validate from the plan's folder:

```
tsp compile <contract-name>.tsp --no-emit
```

Exit `0` and "Compilation completed successfully" → valid.

## Author + emit — HTTP/API seam (OpenAPI)

For a client↔server / REST contract, author a TypeSpec **HTTP service** and emit an `openapi.yaml` from it. Routing decorators (`@route`, `@get`, `@post`, `@body`, …) and the `@typespec/http` import are **allowed and required** here — they are how OpenAPI paths are produced.

Keep it to *what crosses the wire*: the operations, their inputs, and their response models. No handler bodies, no SQL — the emitter only needs the shapes.

### 1. Set up the contracts project once

To resolve the library imports, the HTTP `.tsp` files need a project with the libraries installed. Put them in an **`api-contracts/` subfolder beside the plan** so `node_modules` stays contained. Create `api-contracts/package.json`:

```json
{
  "name": "api-contracts",
  "private": true,
  "dependencies": {
    "@typespec/compiler": "latest",
    "@typespec/http": "latest",
    "@typespec/openapi3": "latest"
  }
}
```

Then install (once):

```
cd api-contracts && npm install
```

### 2. Author each HTTP contract (`api-contracts/<contract-name>.tsp`)

```tsp
import "@typespec/http";
using Http;

@service(#{ title: "Selection API" })
namespace SelectionApi;

model Selection {
  itemId: string;
  selectedAt: utcDateTime;
}

@route("/selection")
interface Selections {
  @get current(): Selection | NoContentResponse;
  @post select(@body body: Selection): Selection;
}
```

### 3. Emit `<contract-name>.openapi.yaml`

Compile each contract from inside `api-contracts/`; this both validates it and writes the YAML beside the `.tsp`:

```
tsp compile <contract-name>.tsp \
  --emit @typespec/openapi3 \
  --option "@typespec/openapi3.emitter-output-dir={cwd}" \
  --option "@typespec/openapi3.file-type=yaml" \
  --option "@typespec/openapi3.output-file=<contract-name>.openapi.yaml"
```

Exit `0` and "Compilation completed successfully" → both the `.tsp` is valid and `<contract-name>.openapi.yaml` is written. Repeat per HTTP contract (the shared `node_modules` serves them all). A compile error cites `file:line` and an error code — fix the `.tsp` and recompile until clean. Optionally tidy with `tsp format <contract-name>.tsp`.

In the plan's Contracts section, give the logical summary and link **both** artefacts: `api-contracts/<contract-name>.tsp` and `api-contracts/<contract-name>.openapi.yaml`.

## If a command fails and you are stuck

Escalate in order; stop at the first step that resolves it:

1. Read the diagnostic text — it names the error code and the exact location.
2. `tsp compile --help` / `tsp --help` — confirm the flags and subcommands on the installed version.
3. Only then consult the docs: https://typespec.io (language basics + guides) or https://github.com/microsoft/typespec.

The web is the **last** resort, and only for an authoring/compile error — never for detection.

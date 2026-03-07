# pants-plugin-pkl

A [Pants](https://www.pantsbuild.org) plugin for the [PKL configuration language](https://pkl-lang.org).

It brings `.pkl` files into the Pants build graph as first-class targets, enabling
dependency inference, validation, formatting, testing, and packaging — all with
standard Pants commands.

---

## Table of contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Architecture](#architecture)
- [Backends](#backends)
- [Configuration](#configuration)
- [Target types](#target-types)
- [Goals](#goals)
- [Dependency inference](#dependency-inference)
- [External packages](#external-packages)
- [PKL binary resolution](#pkl-binary-resolution)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Requirement | Details |
|---|---|
| **Pants** | 2.31.x (uses the call-by-name rule API introduced in 2.31) |
| **Python** | 3.11 (for the plugin resolve — set via `[python.resolves_to_interpreter_constraints]`) |
| **PKL** | >= 0.27.0 for core features; >= 0.30.0 for `pants fmt` (see [version table](#pkl-version-requirements)) |
| **Network** | Required on first run to download the pkl binary (when not using a system binary) and for remote package downloads |

### PKL version requirements

| Feature | Minimum PKL version | Introduced in |
|---|---|---|
| `pants lint` (eval-check), `pants test`, `pants package`, dependency inference | 0.27.0 | `pkl analyze imports`, `--color never` |
| `pants fmt` (`pkl format`) | 0.30.0 | `pkl format` subcommand |
| `pkl download-package` (remote deps) | 0.25.0 | `pkl download-package` subcommand |
| Default / recommended | **0.31.0** | Shipped with pre-computed checksums |

The plugin ships with SHA-256 checksums for PKL **0.31.0** on four platforms
(macOS arm64, macOS x86_64, Linux arm64, Linux x86_64). To use a different
version, provide checksums via `[pkl].known_versions` in `pants.toml`.

---

## Installation

### 1. Copy the plugin

Copy the `pants-plugins/pkl/` directory into your repository:

```
your-repo/
  pants-plugins/
    pkl/
      __init__.py
      subsystem.py
      target_types.py
      ...
```

### 2. Configure `pants.toml`

```toml
[GLOBAL]
pants_version = "2.31.0"
pythonpath = ["%(buildroot)s/pants-plugins"]
backend_packages = [
  "pants.backend.plugin_development",
  "pkl",                    # core: target types + dep inference
  "pkl.goals",              # pants test + pants package + pants tailor
  "pkl.lint.eval_check",    # pants lint  (pkl eval validation)
  "pkl.lint.fmt",           # pants fmt   (pkl format)
]
```

All four backends are independent — register only the ones you need. The `pkl`
core backend must always be listed first because the others depend on the target
types it defines.

### 3. Add source roots

```toml
[source]
root_patterns = ["pants-plugins", "/"]
```

### 4. Add the plugin resolve

The plugin requires `pantsbuild.pants` and `pantsbuild.pants.testutil` as
dependencies. Set up a dedicated resolve:

```toml
[python]
enable_resolves = true

[python.resolves]
pants-plugins = "pants-plugins/lock.txt"

[python.resolves_to_interpreter_constraints]
pants-plugins = ["==3.11.*"]
```

### 5. Add a `pants_requirements` BUILD target

In `pants-plugins/BUILD`:

```python
pants_requirements(name="pants")
```

### 6. Generate the lock file

```bash
pants generate-lockfiles --resolve=pants-plugins
```

---

## Architecture

### Module layout

```
pants-plugins/pkl/
├── register.py                  # Core backend registration
├── subsystem.py                 # PklTool subsystem + PklBinary resolution
├── target_types.py              # All target types and fields
├── pkl_process.py               # Shared argv builder (build_pkl_argv)
├── pkl_dependencies.py          # Remote package resolution (PklResolvedPackages)
├── dependency_inference.py      # Automatic import-based dep inference
├── goals/
│   ├── register.py              # Goals backend registration
│   ├── package.py               # pants package (pkl eval → dist/)
│   ├── test.py                  # pants test (pkl test)
│   └── tailor.py                # pants tailor (auto-generate BUILD)
├── lint/
│   ├── eval_check/
│   │   ├── register.py          # Eval-check backend registration
│   │   ├── subsystem.py         # PklEvalCheck options
│   │   └── rules.py             # pkl eval validation rule
│   └── fmt/
│       ├── register.py          # Formatter backend registration
│       ├── subsystem.py         # PklFmt options
│       └── rules.py             # pkl format rule + version gate
├── binary_test.py               # Tests: version parsing, PklBinary
├── pkl_dependencies_test.py     # Tests: deps.json parser
├── dependency_inference_test.py # Tests: import inference
├── integration_test.py          # Tests: end-to-end all goals
└── ...                          # Additional unit tests per module
```

### Data flow

Every goal follows the same pattern:

1. **Resolve the pkl binary** — `resolve_pkl_binary(PklBinaryRequest())` returns
   a `PklBinary` with the executable path, a `Digest` (empty for system
   binaries, populated for downloaded ones), the version string, and a boolean
   indicating whether it came from `$PATH` or was downloaded.

2. **Resolve external packages** — `resolve_pkl_packages(PklResolvedPackagesRequest())`
   returns a `PklResolvedPackages` containing a `Digest` with the `pkl-packages/`
   directory tree. Empty digest if no external packages are needed.

3. **Gather sources and project files** — Source files, transitive dependencies,
   `PklProject`, and `PklProject.deps.json` files are merged into a single
   sandbox digest.

4. **Build argv and execute** — `build_pkl_argv()` constructs the pkl CLI
   invocation with sandbox containment flags (`--root-dir .`, `--no-cache`,
   `--color never`, `--allowed-modules`, `--allowed-resources`). The process
   runs in the Pants sandbox.

### Rule graph (call-by-name)

The plugin uses the Pants 2.31 call-by-name API. Consumer rules call
`resolve_pkl_binary()` and `resolve_pkl_packages()` directly by function name.
Pants injects `PklTool` and `Platform` implicitly from the rule graph:

```
Consumer rule (e.g. package_pkl)
  └─ await resolve_pkl_binary(PklBinaryRequest())
  │    ├─ find_binary(BinaryPathRequest(...))   [system path search]
  │    └─ download_external_tool(...)           [fallback download]
  └─ await resolve_pkl_packages(PklResolvedPackagesRequest())
       ├─ path_globs_to_digest(pkl-packages/**)  [vendored check]
       └─ execute_process(pkl download-package)  [remote download]
```

### Sandbox containment

All pkl processes run with restricted permissions:

| Flag | Purpose |
|---|---|
| `--root-dir .` | Restricts file access to the sandbox root |
| `--no-cache` | Prevents reading/writing `~/.pkl/cache` (except when `use_cache=True`) |
| `--color never` | Suppresses ANSI escape codes in captured output |
| `--allowed-modules pkl:,file:,modulepath:,projectpackage:,repl:` | Blocks `https:` module imports |
| `--allowed-resources env:,prop:,projectpackage:` | Restricts resource access (adds `https:` when cache is enabled) |

When `use_cache=True` (eval-check, test, package, dependency inference), the
`--cache-dir pkl-packages` flag is used instead of `--no-cache`, and `https:`
is added to `--allowed-resources` so PKL can validate package checksums.

`pkl format` does **not** accept any of these flags — it only supports `--write`,
`--diff-name-only`, `--silent`, and `--grammar-version`.

---

## Backends

The plugin is split into four independently-registerable backends:

| Backend | What it provides | Pants goals |
|---|---|---|
| `pkl` | Target types, dependency inference, subsystem, package resolution | `pants dependencies`, `pants list` |
| `pkl.goals` | Test runner, packager, tailor | `pants test`, `pants package`, `pants tailor` |
| `pkl.lint.eval_check` | Evaluation validation linter | `pants lint` |
| `pkl.lint.fmt` | Code formatter | `pants fmt`, `pants lint` |

Register all four for the full feature set:

```toml
backend_packages = [
  "pkl",
  "pkl.goals",
  "pkl.lint.eval_check",
  "pkl.lint.fmt",
]
```

Or register only what you need — for example, just `pkl` and `pkl.goals` to
skip formatting and lint-checking.

---

## Configuration

### `[pkl]` — Tool subsystem

Controls pkl binary resolution and package management.

```toml
[pkl]
version = "0.31.0"
use_system_binary = true
minimum_version = "0.27.0"
# search_path = ["<PATH>"]
# package_resolve_mode = "auto"
```

| Option | Default | Description |
|---|---|---|
| `version` | `"0.31.0"` | PKL version to download when a suitable system binary is not found |
| `known_versions` | *(built-in for 0.31.0)* | SHA-256 checksums and sizes for each platform binary |
| `use_system_binary` | `true` | Search `$PATH` for pkl before downloading. Set to `false` for fully hermetic builds |
| `minimum_version` | `"0.27.0"` | Minimum acceptable system pkl version. If the system binary is older, falls back to download *(advanced)* |
| `search_path` | `["<PATH>"]` | Directories to search for `pkl`. `<PATH>` expands to `$PATH` *(advanced)* |
| `package_resolve_mode` | `"auto"` | How to resolve external packages: `auto`, `vendored`, or `download` (see [External packages](#external-packages)) |

### `[pkl-eval-check]` — Eval-check linter

```toml
[pkl-eval-check]
skip = false
args = []
```

| Option | Default | Description |
|---|---|---|
| `skip` | `false` | Skip the eval-check linter entirely |
| `args` | `[]` | Extra arguments forwarded to `pkl eval` (e.g. `["--no-project"]`) |

### `[pkl-fmt]` — Formatter

```toml
[pkl-fmt]
skip = false
args = []
```

| Option | Default | Description |
|---|---|---|
| `skip` | `false` | Skip formatting |
| `args` | `[]` | Extra arguments forwarded to `pkl format` (e.g. `["--grammar-version", "1"]`) |

### `[pkl-test-runner]` — Test runner

```toml
[pkl-test-runner]
skip = false
args = []
timeout_default = 0
overwrite = false
```

| Option | Default | Description |
|---|---|---|
| `skip` | `false` | Skip all PKL tests |
| `args` | `[]` | Extra arguments forwarded to `pkl test` |
| `timeout_default` | `0` | Default timeout in seconds (0 = no timeout); overridden per-target by the `timeout` field |
| `overwrite` | `false` | Pass `--overwrite` to regenerate `.pkl-expected.pcf` snapshot files |

---

## Target types

### `pkl_source`

A single PKL source file.

```python
pkl_source(
    name = "config",
    source = "config.pkl",
    dependencies = [],          # usually inferred automatically
    project_dir = None,         # path to the PklProject directory
    skip_eval_check = False,    # opt out of eval-check lint
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `source` | `str` | *(required)* | Path to the `.pkl` file |
| `dependencies` | `list[str]` | `[]` | Explicit dependencies (rarely needed — use dep inference) |
| `project_dir` | `str \| None` | `None` | Path to the directory containing `PklProject` |
| `skip_eval_check` | `bool` | `False` | Skip eval-check lint for this target |

### `pkl_sources`

File generator — creates one `pkl_source` per matching file. Excludes test
files and `PklProject` by default.

```python
pkl_sources(
    name = "src",
    sources = ["*.pkl", "!*_test.pkl", "!*Test.pkl", "!test_*.pkl", "!PklProject"],
    project_dir = "config/pkl",
)
```

The default `sources` pattern automatically excludes:
- `*_test.pkl` — test modules (use `pkl_tests` instead)
- `*Test.pkl` — test modules (PascalCase convention)
- `test_*.pkl` — test modules (prefix convention)
- `PklProject` — not a source file (has no `.pkl` extension, so it is
  never matched by `*.pkl`)

### `pkl_test`

A single PKL test module (must contain `amends "pkl:test"`).

```python
pkl_test(
    name = "math-test",
    source = "math_test.pkl",
    timeout = 30,               # seconds; overrides [pkl-test-runner].timeout_default
    junit_reports = False,       # produce JUnit XML in .junit/
    skip_test = False,
    project_dir = None,
    extra_args = [],
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `source` | `str` | *(required)* | Path to the `.pkl` test file |
| `dependencies` | `list[str]` | `[]` | Explicit dependencies |
| `skip_test` | `bool` | `False` | Skip testing this target |
| `timeout` | `int \| None` | `None` | Per-target timeout in seconds (overrides subsystem default) |
| `project_dir` | `str \| None` | `None` | Path to the PklProject directory |
| `junit_reports` | `bool` | `False` | Generate JUnit XML reports in `.junit/` |
| `extra_args` | `list[str]` | `[]` | Extra arguments passed to `pkl test` |

### `pkl_tests`

File generator — creates one `pkl_test` per matching test file.

```python
pkl_tests(
    name = "tests",
    sources = ["*_test.pkl", "*Test.pkl", "test_*.pkl"],
    project_dir = "config/pkl",
)
```

### `pkl_package`

Evaluates a PKL module and writes output to `dist/`. Supports three modes:

- **Single-file** (default): `pkl eval --format <fmt> -o <path> <source>`
- **Multi-file**: `pkl eval -m <base_dir> <source>` — uses PKL's `output.files`
- **Expression**: adds `-x <expr>` to single-file mode

```python
pkl_package(
    name = "config-json",
    source = "config.pkl",
    output_format = "json",            # json|yaml|plist|properties|pcf|textproto|xml|jsonnet
    output_path = None,                # defaults to <stem>.<ext> (e.g. config.json)
    multiple_outputs = False,          # enable multi-file output
    multiple_output_path = ".",        # base directory for multi-file output
    expression = None,                 # evaluate a sub-expression
    project_dir = None,
    module_path = None,                # directories/archives for modulepath: URIs
    extra_args = [],
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `source` | `str` | *(required)* | Path to the `.pkl` file to evaluate |
| `output_format` | `str` | `"json"` | One of: `json`, `yaml`, `plist`, `properties`, `pcf`, `textproto`, `xml`, `jsonnet` |
| `output_path` | `str \| None` | `None` | Custom output file path. Defaults to `<module_stem>.<ext>` |
| `multiple_outputs` | `bool` | `False` | Enable PKL's multi-file output (`output.files`) |
| `multiple_output_path` | `str` | `"."` | Base directory for multi-file output |
| `expression` | `str \| None` | `None` | Evaluate a sub-expression instead of the full module |
| `project_dir` | `str \| None` | `None` | Path to the PklProject directory |
| `module_path` | `str \| None` | `None` | Directories/archives to search for `modulepath:` URIs |
| `extra_args` | `list[str]` | `[]` | Extra arguments passed to `pkl eval` |

---

## Goals

### `pants lint`

Runs two independent checks (when both backends are registered):

**pkl-eval-check** validates that each `pkl_source` evaluates without errors:

```bash
pants lint src::                         # both checks
pants lint --only=pkl-eval-check src::   # eval-check only
pants lint --only=pkl-fmt src::          # format check only
```

The eval-check runs `pkl eval --format json -o /dev/null <source>`. JSON format
is used instead of the default PCF renderer because PCF cannot serialize `Map`
values or certain PKL-typed objects. JSON handles these cases while still
propagating all real evaluation errors (type mismatches, constraint violations,
unresolved imports, etc.).

Targets with `skip_eval_check=True` are excluded.

**pkl-fmt** checks whether files are already formatted (compares `pkl format`
output to the original).

### `pants fmt`

Formats all `pkl_source` files in-place using `pkl format --write`:

```bash
pants fmt src::
```

Requires PKL >= 0.30.0. If the resolved pkl binary is older, the rule raises a
`ValueError` with a clear message explaining the options (upgrade pkl, set
`[pkl].version`, or disable the `pkl.lint.fmt` backend).

### `pants test`

Runs `pkl test <source>` for each `pkl_test` target:

```bash
pants test src::
pants test src:math-test
```

Exit codes: `0` = all tests passed, `1` = one or more tests failed,
`10` = only expected-file writes occurred (new snapshots created).

Snapshot expected files (`.pkl-expected.pcf`) in the same directory are
automatically included in the sandbox.

To regenerate snapshots: set `[pkl-test-runner].overwrite = true` or pass
`--pkl-test-runner-overwrite`.

### `pants package`

Evaluates a `pkl_package` target and writes output to `dist/`:

```bash
pants package src:config-json     # → dist/config.json
pants package src:config-yaml     # → dist/config.yaml
```

Supports all PKL output formats and three output modes (single-file, multi-file,
and expression).

### `pants tailor`

Auto-generates BUILD entries for unowned `.pkl` files:

```bash
pants tailor src::
```

The tailor reads the first 512 bytes of each `.pkl` file to detect test modules
(files containing `amends "pkl:test"`). It creates `pkl_sources()` for source
files and `pkl_tests()` for test files, grouped by directory.

`PklProject` files are never matched because the glob uses `*.pkl` and
`PklProject` has no `.pkl` extension.

### `pants dependencies`

Shows inferred dependencies for PKL targets:

```bash
pants dependencies src:config
```

Dependencies are inferred automatically — see the next section.

---

## Dependency inference

The plugin infers dependencies between `.pkl` files automatically using two
strategies:

### Primary: `pkl analyze imports`

Runs `pkl analyze imports -f json <source>` — a static analysis command that
extracts all imports (`import`, `import*`, `amends`, `extends`) without
evaluating the module. The JSON output lists `file://` URIs for each direct
import, which are then matched against known `pkl_source` and `pkl_test`
targets.

This method handles all import types and correctly resolves relative paths,
including `..` traversals.

### Fallback: regex

If `pkl analyze imports` fails (e.g., due to an unresolvable import in a
partially-authored module), the plugin falls back to regex parsing over the
source text. The regex matches:

```
import "path/to/module.pkl"
import* "path/to/module.pkl"
amends "path/to/module.pkl"
extends "path/to/module.pkl"
```

Non-local URIs (`pkl:`, `package:`, `https:`, `http:`, `modulepath:`,
`projectpackage:`) are skipped. Only relative file paths are resolved.

### Registration

Dependency inference is registered for both `PklSourceField` and
`PklTestSourceField`, so test modules that import shared library modules have
their dependencies inferred correctly.

---

## External packages

PKL projects that use external packages (via `PklProject` dependencies like
`@formae` or `package://` URIs) need those packages available in the sandbox.

The plugin resolves external packages through the `PklResolvedPackages` rule,
controlled by `[pkl].package_resolve_mode`:

### Mode: `auto` (default)

1. Check for a vendored `pkl-packages/` directory in the repository
2. If found, use it (no network access needed)
3. If not found, parse all `PklProject.deps.json` files and download packages
   via `pkl download-package`

### Mode: `vendored`

Always use the vendored `pkl-packages/` directory. Raises `FileNotFoundError`
if it does not exist. Use this for strict offline builds:

```toml
[pkl]
package_resolve_mode = "vendored"
```

To populate the vendored directory:

```bash
pkl project resolve -o pkl-packages/
```

Then commit `pkl-packages/` to your repository.

### Mode: `download`

Always download from `PklProject.deps.json`, even if a vendored directory
exists. Requires network access:

```toml
[pkl]
package_resolve_mode = "download"
```

### How download works

1. All `PklProject.deps.json` files in the repository are discovered via glob
2. Each file is parsed (schema version 1 only — unknown versions log a warning)
3. Remote dependencies (type `"remote"`) are collected; local dependencies are
   skipped (Pants handles those via filesystem dep inference)
4. `pkl download-package --cache-dir pkl-packages <uri1>::sha256:<hash> ...` is
   executed with the embedded SHA-256 checksums from the deps.json
5. The resulting `pkl-packages/` digest is cached per Pants session and merged
   into every sandbox that needs it

### `PklProject.deps.json` format

The plugin expects schema version 1:

```json
{
  "schemaVersion": 1,
  "resolvedDependencies": {
    "package://example.com/foo/bar@0": {
      "type": "remote",
      "uri": "projectpackage://example.com/foo/bar@0.5.0",
      "checksums": {
        "sha256": "abc123..."
      }
    }
  }
}
```

This file contains the full transitive dependency closure — no recursive
resolution is needed. Generate it with `pkl project resolve`.

### Setting `project_dir`

When `PklProject` is not in the same directory as the source files, set
`project_dir` on your targets:

```python
pkl_sources(
    project_dir = "config/pkl",
)

pkl_package(
    name = "config-json",
    source = "global.pkl",
    output_format = "json",
    project_dir = "config/pkl",
)
```

---

## PKL binary resolution

The plugin supports two strategies for obtaining the pkl binary, controlled by
`[pkl].use_system_binary`:

### System-first (default)

1. Search `[pkl].search_path` (default: `$PATH`) for a `pkl` binary
2. Run `pkl --version` to determine the version
3. If the version is >= `[pkl].minimum_version` (default: `0.27.0`), use it
4. Otherwise, log an informational message and fall back to download

System binaries use `EMPTY_DIGEST` (no files added to the sandbox) and the
absolute path on disk as the executable.

### Download-only

Set `[pkl].use_system_binary = false` to always download:

```toml
[pkl]
use_system_binary = false
```

The plugin downloads the binary specified by `[pkl].version` from GitHub
releases and extracts it into the sandbox. This provides fully hermetic builds.

### Version parsing

The version parser handles all known PKL version output formats:

- `Pkl 0.28.0 (Linux, Native)` — native binary
- `Pkl 0.29.1 (macOS 15.7.3, Java 21.0.8)` — Java-based binary
- `Pkl 0.32.0-dev (Linux, Native)` — dev builds (only numeric portion is extracted)

### Platform support

| Platform | Download binary name |
|---|---|
| macOS ARM64 (Apple Silicon) | `pkl-macos-aarch64` |
| macOS x86_64 (Intel) | `pkl-macos-amd64` |
| Linux x86_64 | `pkl-linux-amd64` |
| Linux ARM64 | `pkl-linux-aarch64` |

---

## Limitations

### No Windows support

The plugin only provides download checksums for macOS and Linux. Windows is not
supported.

### `<PATH>` expansion in Nix environments

The default `search_path = ["<PATH>"]` expands to the `$PATH` visible to the
Pants process. In Nix-based environments, Pants' execution environment may not
include nix-profile paths. Workaround:

```toml
[pkl]
search_path = ["/Users/<you>/.nix-profile/bin", "<PATH>"]
```

Or set `use_system_binary = false` to bypass system detection entirely.

### `pkl format` requires PKL >= 0.30.0

The `pkl format` subcommand was introduced in PKL 0.30.0. If the resolved
binary is older, `pants fmt` raises a clear error. Workarounds:

- Upgrade your system pkl
- Set `[pkl].version` to `>= 0.30.0` and `[pkl].use_system_binary = false`
- Remove `pkl.lint.fmt` from `backend_packages`

### Only `PklProject.deps.json` schema version 1

The dependency parser only understands schema version 1. If PKL introduces a
new schema version, the plugin logs a warning and skips that file. Update the
plugin to support newer schema versions as they are released.

### Pre-computed checksums for 0.31.0 only

The `[pkl].known_versions` option ships with checksums for PKL 0.31.0. To use a
different PKL version for download, you must provide the checksums yourself:

```toml
[pkl]
version = "0.28.2"
known_versions = [
  "0.28.2|macos_arm64|<sha256>|<size>",
  "0.28.2|macos_x86_64|<sha256>|<size>",
  "0.28.2|linux_x86_64|<sha256>|<size>",
  "0.28.2|linux_arm64|<sha256>|<size>",
]
```

### Regex fallback is limited

The regex fallback for dependency inference only handles simple relative path
imports. It does not resolve:

- `modulepath:` URIs
- `package://` URIs
- `file://` absolute URIs
- Computed import expressions (e.g., `import(dynamicPath)`)

These cases are handled by the primary `pkl analyze imports` strategy.

### No `pkl project resolve` integration

The plugin does not run `pkl project resolve` to generate `PklProject.deps.json`.
You must run this command manually (or in CI) and commit the result. The plugin
only reads existing deps.json files.

### Network access for remote packages

When `package_resolve_mode` is `auto` (with no vendored directory) or
`download`, the `pkl download-package` process requires network access. This
happens inside a Pants `Process` with `cache_scope=PER_SESSION` — results are
cached within a build session but re-fetched if `PklProject.deps.json` changes.

---

## Troubleshooting

### "Unknown command: format"

Your pkl binary is older than 0.30.0. See [pkl format limitation](#pkl-format-requires-pkl--0300).

### "pkl format requires PKL >= 0.30.0"

The resolved pkl binary version does not support `pkl format`. Either:
- Set `[pkl].use_system_binary = false` and `[pkl].version = "0.31.0"` to force download
- Install PKL >= 0.30.0 on your system
- Remove `pkl.lint.fmt` from backends

### System binary not detected

Run `pkl --version` manually to confirm it is on your `$PATH`. Check that the
version output matches the expected format (`Pkl X.Y.Z ...`).

In Nix environments, set `[pkl].search_path` explicitly (see Limitations).

### Eval-check fails with serialization error

The eval-check uses `--format json` to avoid PCF serialization issues with `Map`
values. If you still see serialization errors, it may be a genuine type error in
your PKL module. Check the stderr output for details.

### Dependency inference misses an import

If `pkl analyze imports` fails for a specific file (check Pants log at
`-ldebug`), the plugin falls back to regex. The regex only finds `import`,
`import*`, `amends`, and `extends` with string literals. Dynamic imports and
`modulepath:` URIs are not resolved by the regex fallback.

### "package_resolve_mode is 'vendored' but no pkl-packages/ directory was found"

You have set `package_resolve_mode = "vendored"` but there is no `pkl-packages/`
directory in the repository root. Either:
- Run `pkl project resolve -o pkl-packages/` and commit the result
- Switch to `auto` or `download` mode

---

## Example project

A complete working configuration using all backends:

### `pants.toml`

```toml
[GLOBAL]
pants_version = "2.31.0"
pythonpath = ["%(buildroot)s/pants-plugins"]
backend_packages = [
  "pants.backend.plugin_development",
  "pkl",
  "pkl.goals",
  "pkl.lint.eval_check",
  "pkl.lint.fmt",
]

[source]
root_patterns = ["pants-plugins", "/"]

[python]
enable_resolves = true

[python.resolves]
pants-plugins = "pants-plugins/lock.txt"

[python.resolves_to_interpreter_constraints]
pants-plugins = ["==3.11.*"]
```

### `config/pkl/BUILD`

```python
pkl_sources(
    project_dir = "config/pkl",
)

pkl_package(
    name = "global-json",
    source = "global.pkl",
    output_format = "json",
    project_dir = "config/pkl",
)

pkl_package(
    name = "global-yaml",
    source = "global.pkl",
    output_format = "yaml",
    project_dir = "config/pkl",
)

pkl_tests(
    name = "tests",
    project_dir = "config/pkl",
)
```

### Commands

```bash
# Auto-generate BUILD targets for new .pkl files
pants tailor config/pkl::

# Validate all PKL files compile cleanly
pants lint config/pkl::

# Format PKL files
pants fmt config/pkl::

# Run PKL tests
pants test config/pkl:tests

# Package config to JSON
pants package config/pkl:global-json

# Package config to YAML
pants package config/pkl:global-yaml

# Show inferred dependencies
pants dependencies config/pkl/global.pkl
```

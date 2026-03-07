# pants-plugin-pkl

A [Pants](https://www.pantsbuild.org) plugin for the [PKL configuration language](https://pkl-lang.org). It brings PKL files into the Pants build graph, enabling `pants test`, `pants fmt`, `pants lint`, `pants package`, `pants tailor`, and automatic dependency inference for `.pkl` files.

## What this plugin does

`pants-plugin-pkl` integrates PKL into a Pants monorepo so that `.pkl` source files are first-class build targets. The plugin resolves the `pkl` binary automatically (system-first with download fallback) and uses it to:

- **Validate** PKL files compile cleanly (`pants lint` via `pkl eval`)
- **Format** PKL files in-place or check formatting in CI (`pants fmt` / `pants lint` via `pkl format`)
- **Run tests** written with `pkl:test` (`pants test`)
- **Package** PKL modules into JSON, YAML, XML, and other formats (`pants package`)
- **Suggest BUILD targets** automatically for new `.pkl` files (`pants tailor`)
- **Infer dependencies** between `.pkl` files so you never have to list them by hand
- **Resolve external packages** from `PklProject.deps.json` (vendored or downloaded)

## Quick start

1. Copy `pants-plugins/pkl/` into your repository
2. Configure `pants.toml`:

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

3. Run `pants tailor` to auto-generate BUILD targets, then use Pants as normal:

```bash
pants lint src::       # validate + format check
pants fmt src::        # format in-place
pants test src::       # run pkl tests
pants package src:pkg  # evaluate to JSON/YAML/etc
```

## Documentation

See **[docs/index.md](docs/index.md)** for the full reference, including:

- [Prerequisites](docs/index.md#prerequisites)
- [Installation](docs/index.md#installation)
- [Architecture](docs/index.md#architecture)
- [Backends](docs/index.md#backends)
- [Configuration](docs/index.md#configuration) (`[pkl]`, `[pkl-fmt]`, `[pkl-eval-check]`, `[pkl-test-runner]`)
- [Target types](docs/index.md#target-types) (`pkl_source`, `pkl_sources`, `pkl_test`, `pkl_tests`, `pkl_package`)
- [Goals](docs/index.md#goals) (lint, fmt, test, package, tailor, dependencies)
- [Dependency inference](docs/index.md#dependency-inference)
- [External packages](docs/index.md#external-packages) (auto/vendored/download modes)
- [PKL binary resolution](docs/index.md#pkl-binary-resolution) (system-first vs download-only)
- [Limitations](docs/index.md#limitations)
- [Troubleshooting](docs/index.md#troubleshooting)

## PKL version requirements

| Feature | Minimum PKL version |
|---|---|
| Core (`test`, `lint`, `package`, dep inference) | 0.27.0 |
| `pants fmt` (`pkl format`) | 0.30.0 |
| Default / recommended | **0.31.0** |

## License

See [LICENSE](LICENSE).

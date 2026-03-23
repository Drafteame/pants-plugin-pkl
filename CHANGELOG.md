## v1.0.1 (2026-03-23)


- Merge pull request #5 from Drafteame/chore/fix-version-sync
- chore: fix version sync
- chore: save
- chore: fix test-release workflow to read version from git tags
- chore: fix version sync between commitizen and pyproject.toml

## v0.3.0 (2026-03-23)


- Merge pull request #4 from Drafteame/feat/pkl-package-env-vars
- feat: add env_vars field to pkl_package target
- feat: add env_vars field to pkl_package target

## v0.2.0 (2026-03-23)


- Merge pull request #2 from Drafteame/chore/remove-pantsbuild-dependency
- chore: remove pantsbuild.pants runtime dependency
- Merge pull request #3 from Drafteame/chore/pkl-alias-dependency-infernce
- feat: add alias import resolution for PKL project dependencies
- feat: add alias import resolution for PKL project dependencies
- chore: remove pantsbuild.pants runtime dependency

## v0.1.0 (2026-03-17)


- Merge pull request #1 from Drafteame/ci/publish-to-pypi
- ci: publish to PyPI
- ci: save
- ci: update release workflow and add test-release for publishing to TestPyPI
- build: add PyPI packaging and publish workflow
- docs: update index.md to reflect fixes 1-4
- - Sandbox containment: replace old --allowed-modules/--allowed-resources
  whitelist with --allowed-modules .* and drop --allowed-resources entirely
- eval-check: document -x module instead of --format json; explain why
  expression mode avoids both the PCF/Map and JSON/Class false positives
- project_dir: add auto-detection section; manual field is now an override
  only (plugin walks the sandbox file tree to find the nearest PklProject)
- <PATH> expansion: rewrite Nix limitation as resolved behaviour; the
  sentinel is now correctly expanded to real PATH directories at rule time
- Troubleshooting: rewrite eval-check serialization entry to reflect that
  -x module bypasses the renderer so renderer errors are no longer expected
- fix: use -x module instead of --format json in eval-check
- --format json failed on PKL modules whose packages define Class-type
properties (e.g. formae@0.82.1 exposes `fixed Type: Class = type`),
causing false eval-check failures for otherwise valid modules.
- Switch to `pkl eval -x module -o /dev/null` which evaluates the full
module object without invoking the output renderer at all.  This avoids
both the JSON/Class issue and the pre-existing PCF/Map issue, while
still catching all real errors: type mismatches, constraint violations,
and unresolved imports all produce a non-zero exit code.
- fix: pass --allowed-modules '.*' to allow custom PKL URI schemes
- The old hardcoded whitelist (pkl:,file:,modulepath:,projectpackage:,
repl:) blocked custom URI schemes registered by remote PKL packages
(e.g. formae:, aws:).  PKL's default allowlist has the same problem.
- Pass --allowed-modules '.*' (Java regex, matches all URIs) so that
any scheme registered by a vendored package is automatically permitted.
File-system isolation is already enforced by --root-dir .; a
module-level allowlist provides no additional security in the sandbox.
- Fix fmt tests that started failing after Fix 1 enabled the system
pkl 0.29.1 binary: add --pkl-use-system-binary=False to force
downloading pkl 0.31.0 which supports the format subcommand.
- fix: auto-detect --project-dir from PklProject files in sandbox
- PKL's project-discovery walks up from the *source file* directory, not
from the CWD.  Inside a Pants sandbox the CWD is always the sandbox
root, so PKL never finds PklProject files adjacent to (or above) the
source file unless --project-dir is set explicitly in the BUILD file.
- Add detect_project_dir(source_file, sandbox_files) to pkl_process.py
that replicates PKL's walk-up logic using the merged sandbox frozenset.
All consumer rules (eval-check, package, test, dependency inference)
now call detect_project_dir(source_file, frozenset(snapshot.files)) to
derive an effective project_dir when the BUILD-level field is unset.
- fix: expand <PATH> sentinel in system binary search path resolution
- BinaryPathRequest.search_path expects real directory paths; the raw
<PATH> sentinel is passed through as-is by Pants which causes
_find_candidate_paths_via_path_metadata_lookups to call
os.path.abspath('<PATH>') and produce a nonsense path.
- Resolve the actual PATH environment variable via
environment_vars_subset(EnvironmentVarsRequest(('PATH',))) before
building BinaryPathRequest so the system binary is correctly found.
Register env_vars.rules() in register.py to make the rule available.
- docs: add comprehensive reference documentation in docs/index.md
- refactor: migrate consumer rules from deprecated Get() to call-by-name API
- Replace all `await Get(PklBinary, PklBinaryRequest())` and
`await Get(PklResolvedPackages, PklResolvedPackagesRequest())` calls with
`await resolve_pkl_binary(PklBinaryRequest())` and
`await resolve_pkl_packages(PklResolvedPackagesRequest())` across
dependency_inference.py, goals/package.py, goals/test.py,
lint/eval_check/rules.py, lint/fmt/rules.py, and pkl_dependencies.py.
- test: add unit tests for version helpers, PklBinary, and deps.json parser
- refactor: replace PathGlobs vendoring with PklResolvedPackages digest in all consumer rules
- feat: add remote PKL package resolver with vendored fallback
- feat: add pkl format version gate (>= 0.30.0) to pkl_fmt rule
- refactor: migrate all consumer rules from download_external_tool to PklBinary
- feat: add PklBinary type and system-first binary resolution to subsystem
- docs: update README with complete setup instructions and missing details
- feat: add vendored pkl-packages support for offline package:// dependency resolution
- Include pkl-packages/ directory in the Pants sandbox for dependency inference,
test, and package goals so that external package:// dependencies resolve from
the local cache without network access. Pass use_cache=True to build_pkl_argv()
to use --cache-dir instead of --no-cache, matching eval-check which already had
this support. Also add skip_eval_check field to pkl_package targets.
- docs: improve docstrings and remove dead code in pkl plugin
- - Correct misleading --root-dir docstring in lint/fmt/rules.py to
  accurately state that pkl format does not accept --root-dir
- Remove dead _PKL_PROJECT_BASENAME constant and exclusion filter in
  tailor.py (PklProject has no .pkl extension so is never matched by
  the *.pkl glob)
- Add comprehensive backend documentation to register.py listing all
  four available pants.toml backend_packages entries
- Document batch.single_element assumption in test.py module docstring
- refactor: migrate all rules to Pants 2.31 call-by-name API
- Replace all Get()/MultiGet() calls with their Pants 2.31 equivalents:
- Get(DownloadedExternalTool, ...) -> download_external_tool()
- Get(SourceFiles, ...) -> determine_source_files()
- Get(Digest, MergeDigests(...)) -> merge_digests()
- Get(Snapshot, PathGlobs(...)) -> path_globs_to_digest() + digest_to_snapshot()
- Get(DigestContents, ...) -> get_digest_contents()
- Get(FallibleProcessResult, ...) -> execute_process(**implicitly(...))
- Get(ProcessResult, ...) -> execute_process_or_raise(**implicitly(...))
- Get(TransitiveTargets, ...) -> transitive_targets(**implicitly(...))
- MultiGet(Get(...) for ...) -> concurrently(fn(...) for ...)
- Complete tailor.py migration that was only partially done: replace
remaining Get(Snapshot, ...) and Get(DigestContents, ...) with
path_globs_to_digest/digest_to_snapshot/get_digest_contents and
remove the unused Get import.
- fix: add use_cache support for external packages and PklTest dep inference
- - Add PKL_PACKAGES_DIR constant and use_cache parameter to build_pkl_argv
  for resolving external package:// dependencies from vendored cache
- Enable use_cache in eval_check rules and include pkl-packages/ in sandbox
- Add PklTestInferenceFieldSet and infer_pkl_test_dependencies rule so
  pkl_test targets have automatic dependency inference
- Extract _resolve_import_addresses helper covering both PklSourceField
  and PklTestSourceField targets
- Fix path-segment boundary checks in _parse_analyze_output and address
  suffix matching to prevent false-positive matches
- fix: resolve argv ordering, sandbox inclusion, and serialisation bugs in pkl rules
- - Replace fragile argv.insert(-1, ...) pattern with pre_args list in test.py
- Move -o /dev/null into extra_args and add --format json in eval_check to
  avoid PCF serialisation errors with Map values
- Add PartitionerType.DEFAULT_SINGLE_PARTITION to PklEvalCheckRequest to
  prevent rule graph errors at startup
- Include PklProject and PklProject.deps.json in sandbox digests across
  test, eval_check, package, and dependency_inference rules
- Fix IntOption(default=None) type mismatch in PklTestSubsystem; use default=0
  and update timeout resolution chain
- test: fix RuleRunner fixtures and invalid pkl expression syntax
- - register_test.py and tailor_test.py: RuleRunner was missing
  external_tool_rules() and source_files_rules(), required by the dependency
  inference rule that register.rules() installs
- lint/fmt/rules_test.py: rule_runner.request(Snapshot, PathGlobs(...)) must
  be rule_runner.request(Snapshot, [PathGlobs(...)])
- package_test.py: expression=".name" is invalid PKL syntax; corrected to
  expression="name"
- fix: normalize relative import paths and allow repl: scheme for eval expressions
- - _extract_local_paths_from_regex was returning unnormalized paths like
  `src/sub/../shared/utils.pkl` instead of `src/shared/utils.pkl`; PurePosixPath
  does not resolve `..` components so a manual normalization loop is now used
- `pkl eval -x` internally loads `repl:text` which was blocked by the
  --allowed-modules allowlist, causing all expression-mode evaluations to fail
  with exit code 1; added `repl:` to the allowed schemes
- chore: remove task references and unused imports
- chore: remove development scratch files
- docs: add comprehensive README for the PKL Pants plugin
- fix: correct Pants 2.31 API incompatibilities in lint, fmt, and test rules
- test: add end-to-end integration test exercising all plugin goals
- chore: replace .gitignore with minimal project-specific version
- feat: add pants tailor support for PKL source and test targets
- Implements PutativePklTargetsRequest and the find_putative_pkl_targets
rule. Files containing `amends "pkl:test"` are classified as pkl_tests;
remaining .pkl files become pkl_sources. PklProject files are excluded.
Detection uses file content (not filename patterns) for test identification.
One target suggestion per directory; already-owned files are skipped.
Tests cover source files, test detection by content, mixed directories,
already-owned files, and PklProject exclusion.
- feat: add PKL dependency inference via pkl analyze imports with regex fallback
- Implements InferPklDependenciesRequest and the infer_pkl_dependencies rule.
Primary method uses `pkl analyze imports -f json` for accurate static
analysis; falls back to regex (PKL_IMPORT_RE) when the command fails.
Ignores pkl:, package://, https:// and other non-local URI schemes.
Maps resolved file paths to target addresses via AllTargets.
Tests cover JSON parsing, regex fallback, URI filtering, and a
RuleRunner integration test for a local import.
- feat: add pkl_package goal with single/multi-file and expression output modes
- Implements PklPackageFieldSet and the package_pkl rule that runs
`pkl eval` via pants package. Supports single-file (--format + -o),
multi-file (-m base_dir), and expression (-x expr) modes.
Artifacts are enumerated from the output digest for multi-file mode.
Tests cover default path, JSON/YAML output, custom path, and expression.
- feat: add pkl formatter
- feat: add pkl test runner
- feat: add pkl eval-check lint rule
- feat: implement core registration and smoke tests
- feat: implement PklTool subsystem and build_pkl_argv helper
- chore: compute and record PKL 0.31.0 known_versions for PklTool
- Downloads 4 platform binaries (macos-aarch64, macos-amd64, linux-amd64, linux-aarch64)
from the 0.31.0 release, verifies sha256 against GitHub's published hashes, and records
the version|platform|sha256|bytesize strings required by PklTool.default_known_versions.
- fix: enable PBS provider and pants-plugins resolve so tests run on Python 3.13
- - Add pants.backend.python.providers.experimental.python_build_standalone to
  backend_packages so Pants auto-downloads Python 3.11 hermetically via PBS
- Enable resolves with a dedicated pants-plugins resolve constrained to ==3.11.*
  (required by pantsbuild.pants.testutil==2.31.0)
- Update all BUILD files under pants-plugins/ with resolve and interpreter_constraints
- Generate pants-plugins/lock.txt (pins pantsbuild.pants.testutil==2.31.0 and deps)
- All 53 tests in target_types_test.py pass under Pants 2.31.0 + PBS Python 3.11.14
- feat: implement PKL target types, fields, and tests
- Add all PKL field classes (source, config, test, skip, timeout, etc.)
Add all PKL target types (pkl_source, pkl_sources, pkl_test, pkl_tests, pkl_package)
Add target_types_test.py with 53 unit + RuleRunner integration tests
Fix pants_version in pants.toml from non-existent 2.31.0 to latest stable 2.24.3
- chore: scaffold project structure for pants-plugin-pkl
- Add pants.toml with Pants 2.31.0, pkl backend, and interpreter_constraints >=3.9,<3.13
Create pants-plugins/ directory tree with BUILD files for all packages
Add __init__.py files for pkl, goals, lint, lint/fmt, lint/eval_check
Add stub register.py so the pkl backend loads cleanly
Verified: pants --version returns 2.31.0 and pants list pants-plugins/pkl:: resolves all targets
- Initial commit

# Dependency security verification

The dependency security workflow tests Windows and Linux with Python 3.10, 3.11,
and 3.12. Each job installs the lockfile, checks dependency consistency, runs the
test suite, Ruff and mypy, builds a wheel, and tests a fresh ordinary pip wheel
installation with the source directory removed from pytest's Python path.

Security minimums are declared in the wheel's dependency metadata as well as
the lockfile. The October 2026 update covers all 27 open Dependabot alerts:

| Package | Alerts | Minimum | Locked version |
|---|---|---|---|
| AnyIO | #24–26 | 4.14.2 | 4.15.1 |
| PyJWT | #27–39 | 2.15.1 | 2.15.1 |
| pypdf | #40–47 | 6.19.0 | 6.21.1 |
| MCP | #48 | 1.30.0 | 1.30.0 |
| Soup Sieve | #22–23 | 2.9.0 | 2.10 |

For [PyJWT #39](https://github.com/takashi000/ollama-web/security/dependabot/39),
the advisory did not specify a first patched version at investigation time.
PyJWT 2.15.1 copies caller options in `PyJWT._merge_options`. Regression tests
cover both `decode` and `decode_complete`: an unverified decode must preserve
the options dictionary, and reusing it with signature verification enabled must
reject expired tokens, incorrect audiences, and incorrect issuers.

MCP integration tests use real SDK servers to list and call tools over stdio
and Streamable HTTP. The HTTP test also checks session DELETE requests and
client closure. No external service is needed.

## Completion gates

1. Fetch all Dependabot alerts with `gh api --paginate --slurp
   'repos/takashi000/ollama-web/dependabot/alerts?per_page=100'`. Record the
   retrieval time, repository SHA, and all open alert IDs, including new alerts.
2. Resolve secure versions for every supported Python version without
   reintroducing previously fixed vulnerabilities.
3. Pass the entire compatibility matrix for locked and ordinary pip
   installations, including the PyJWT regression and MCP transport tests.
4. Merge into the default branch, wait for dependency graph processing, and
   fetch alerts again. Completion requires zero open alerts and all 27 target
   alerts fixed. Dismissal and waiting for analysis do not satisfy this gate.
5. Report deployed environment installation, restart, actual versions, and
   smoke tests separately. A fixed GitHub alert does not verify deployment.

To reproduce wheel validation locally, build one wheel in `dist/`, then run
`python scripts/check_wheel_install.py` using the desired Python version. The
script creates and removes its own temporary virtual environment and uses pip
without the uv lockfile or constraints.

# Packaging

## PyPI

Releases publish automatically when you push a `v*` tag. Trusted publishing
is configured via GitHub Actions (`.github/workflows/release.yml`). Set up
the project on PyPI first:

1. In PyPI, add a trusted publisher for project `handoff-agent`. The name
   `handoff` was already taken, so the distribution is published as
   `handoff-agent` while the CLI command stays `handoff`.
2. Use repo `HacksonClark/handoff`, workflow `release.yml`, environment `pypi`.
3. If the project does not exist on PyPI yet, configure it as a pending
   publisher so the first publish creates the project.
4. Tag a release: `git tag v0.1.0 && git push origin v0.1.0`.

Once the workflow publishes `handoff-agent` to PyPI, users can install it
with:

```bash
uv tool install handoff-agent
```

## Homebrew

`handoff.rb` is a template formula. To cut a real Homebrew release:

```bash
pip install homebrew-pypi-poet
poet -f handoff >> handoff.rb       # regenerate resource list
# Drop the template fields and update url + sha256 to point at the PyPI sdist
```

Ship via a personal tap first; if there's uptake, submit to `homebrew-core`.

## uv tool

Nothing separate to do. `uv tool install handoff-agent` installs from PyPI
by default, so publishing to PyPI is what makes uv installs work.

## Shell completion

Generated at runtime by click. See `handoff completion --help`.

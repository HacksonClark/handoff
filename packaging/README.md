# Packaging

## PyPI

Releases publish automatically when you push a `v*` tag. Trusted publishing
is configured via GitHub Actions (`.github/workflows/release.yml`). Set up
the project on PyPI first:

1. Create the project on https://pypi.org by uploading a manual release once.
2. Add a trusted publisher: repo `HacksonClark/handoff`, workflow
   `release.yml`, environment `pypi`.
3. Tag a release: `git tag v0.1.0 && git push --tags`.

## Homebrew

`handoff.rb` is a template formula. To cut a real Homebrew release:

```bash
pip install homebrew-pypi-poet
poet -f handoff >> handoff.rb       # regenerate resource list
# Drop the template fields and update url + sha256 to point at the PyPI sdist
```

Ship via a personal tap first; if there's uptake, submit to `homebrew-core`.

## uv tool

Nothing to do — `uv tool install handoff` works once the package is on PyPI.

## Shell completion

Generated at runtime by click. See `handoff completion --help`.

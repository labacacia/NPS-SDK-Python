English | [中文版](./CHANGELOG.cn.md)

# Changelog — Python SDK (`nps-lib`)

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until NPS reaches v1.0 stable, every repository in the suite is synchronized to the same pre-release version tag.

---

## [1.0.0-alpha.2] — 2026-04-19

### Changed

- **PyPI distribution renamed from `nps-sdk` to `nps-lib`.** The `nps-sdk` name on PyPI is owned by an unrelated party (Ingenico); LabAcacia ships under `nps-lib` instead. Import module `nps_sdk` is unchanged, so existing `import nps_sdk` code works without modification — only `pip install` and `pyproject.toml` dependency declarations need updating.
- Version bump to `1.0.0-alpha.2` for suite-wide synchronization. No functional changes beyond version alignment.
- 162 tests, 97% coverage green.

### Covered modules

- nps_sdk.core / ncp / nwp / nip / ndp / nop

---

## [1.0.0-alpha.1] — 2026-04-10

First public alpha as part of the NPS suite `v1.0.0-alpha.1` release.

[1.0.0-alpha.2]: https://github.com/LabAcacia/nps/releases/tag/v1.0.0-alpha.2
[1.0.0-alpha.1]: https://github.com/LabAcacia/nps/releases/tag/v1.0.0-alpha.1

# Changelog

## [0.1.12](https://github.com/manhhailua/lorekeep/compare/v0.1.11...v0.1.12) (2026-06-27)


### Features

* .lorekeep data home, backup CLI, and interactive onboarding ([#39](https://github.com/manhhailua/lorekeep/issues/39)) ([9b21575](https://github.com/manhhailua/lorekeep/commit/9b215752568a89eb4a830b13fe4d97848601f874))

## [0.1.11](https://github.com/manhhailua/lorekeep/compare/v0.1.10...v0.1.11) (2026-06-22)


### Features

* add agent watch sessions for continuous live ingest ([#32](https://github.com/manhhailua/lorekeep/issues/32)) ([4a48dbd](https://github.com/manhhailua/lorekeep/commit/4a48dbd35d46199255268eff329808bccda5dcbe)), closes [#9](https://github.com/manhhailua/lorekeep/issues/9)

## [0.1.10](https://github.com/manhhailua/lorekeep/compare/v0.1.9...v0.1.10) (2026-06-22)


### Documentation

* mandate PR-only git workflow, never push to main ([#30](https://github.com/manhhailua/lorekeep/issues/30)) ([b080360](https://github.com/manhhailua/lorekeep/commit/b0803603ff355ba70816255503eb74b5eff6622b))

## [0.1.9](https://github.com/manhhailua/lorekeep/compare/v0.1.8...v0.1.9) (2026-06-22)


### Features

* implement lorekeep agent ingest with human-in-the-loop review ([5cf77fe](https://github.com/manhhailua/lorekeep/commit/5cf77fe7829ff2fccbf9f95bddba135c67c0f29a)), closes [#15](https://github.com/manhhailua/lorekeep/issues/15)

## [0.1.8](https://github.com/manhhailua/lorekeep/compare/v0.1.7...v0.1.8) (2026-06-22)


### Features

* implement autonomous agent, journal, and MCP write tools ([188ebd5](https://github.com/manhhailua/lorekeep/commit/188ebd5787d962d41e43c421e6e83dbaeaf8a66b))
* implement autonomous agent, journal, and MCP write tools ([f669b3b](https://github.com/manhhailua/lorekeep/commit/f669b3b07ed2a53d5299da325d895c037a2788b5))
* implement autonomous agent, journal, and MCP write tools ([#26](https://github.com/manhhailua/lorekeep/issues/26)) ([188ebd5](https://github.com/manhhailua/lorekeep/commit/188ebd5787d962d41e43c421e6e83dbaeaf8a66b))


### Bug Fixes

* auto-resolve in agent watch, edge ID generation, update_fact for edges ([db40785](https://github.com/manhhailua/lorekeep/commit/db407850dc2431b47cb31e5461a8354473d47d91))

## [0.1.7](https://github.com/manhhailua/lorekeep/compare/v0.1.6...v0.1.7) (2026-06-21)


### Bug Fixes

* use workflow_dispatch to trigger release.yml, avoid OIDC name mismatch ([c857706](https://github.com/manhhailua/lorekeep/commit/c857706718fbcf817bdec7488edc7fa06cfde6b7))
* use workflow_dispatch to trigger release.yml, avoid OIDC name mismatch ([#24](https://github.com/manhhailua/lorekeep/issues/24)) ([c857706](https://github.com/manhhailua/lorekeep/commit/c857706718fbcf817bdec7488edc7fa06cfde6b7))

## [0.1.6](https://github.com/manhhailua/lorekeep/compare/v0.1.5...v0.1.6) (2026-06-21)


### Bug Fixes

* use setup-uv@v7, [@v8](https://github.com/v8) tag does not exist ([ff5a1b6](https://github.com/manhhailua/lorekeep/commit/ff5a1b634f9551a2d1e10ae843d273b8a1925dd8))
* use setup-uv@v7, [@v8](https://github.com/v8) tag does not exist ([2c67bd7](https://github.com/manhhailua/lorekeep/commit/2c67bd78c2e53f78ccade493a40631e09209f372))
* use setup-uv@v7, [@v8](https://github.com/v8) tag does not exist ([#22](https://github.com/manhhailua/lorekeep/issues/22)) ([ff5a1b6](https://github.com/manhhailua/lorekeep/commit/ff5a1b634f9551a2d1e10ae843d273b8a1925dd8))

## [0.1.5](https://github.com/manhhailua/lorekeep/compare/v0.1.4...v0.1.5) (2026-06-21)


### Bug Fixes

* inline PyPI publish in release-please, OIDC zero-trust, no PAT ([79afa7d](https://github.com/manhhailua/lorekeep/commit/79afa7d091ac6aefb21440255bd3bcc670976bf7))
* inline PyPI publish in release-please, OIDC zero-trust, no PAT ([9a48213](https://github.com/manhhailua/lorekeep/commit/9a48213482f2f0d79f6542c3a09c61389957904a))
* inline PyPI publish in release-please, OIDC zero-trust, no PAT ([#20](https://github.com/manhhailua/lorekeep/issues/20)) ([79afa7d](https://github.com/manhhailua/lorekeep/commit/79afa7d091ac6aefb21440255bd3bcc670976bf7))

## [0.1.4](https://github.com/manhhailua/lorekeep/compare/v0.1.3...v0.1.4) (2026-06-21)


### Features

* import --from cursor (deep-only) ([#14](https://github.com/manhhailua/lorekeep/issues/14)) ([292fad2](https://github.com/manhhailua/lorekeep/commit/292fad23b4daa8991df89b7300771342c68ee951)), closes [#4](https://github.com/manhhailua/lorekeep/issues/4)
* living knowledge architecture — append-and-resolve, agent journals, autonomous agent ([d173a46](https://github.com/manhhailua/lorekeep/commit/d173a46868cdbcc958ae8a777f7ad90340c6f6bf))
* living knowledge architecture — append-and-resolve, agent journals, autonomous agent ([#17](https://github.com/manhhailua/lorekeep/issues/17)) ([d173a46](https://github.com/manhhailua/lorekeep/commit/d173a46868cdbcc958ae8a777f7ad90340c6f6bf))


### Bug Fixes

* revert release-please to simple model, use PAT fallback for PyPI auto-publish ([d5dd6c7](https://github.com/manhhailua/lorekeep/commit/d5dd6c7f8952cb46591afff4b13db39022eb4914))
* revert release-please to simple model, use PAT fallback for PyPI auto-publish ([0e20d7a](https://github.com/manhhailua/lorekeep/commit/0e20d7a3b7af3a089a2fe2bbd08b7418d0a89357))
* revert release-please to simple model, use PAT fallback for PyPI auto-publish ([#18](https://github.com/manhhailua/lorekeep/issues/18)) ([d5dd6c7](https://github.com/manhhailua/lorekeep/commit/d5dd6c7f8952cb46591afff4b13db39022eb4914))


### Documentation

* fix confidence overlap, ns param, journal partitions, edge retry ([0ed8f0e](https://github.com/manhhailua/lorekeep/commit/0ed8f0e6e34e4f182e8d50863d1c5c7ccd3b32fa))
* fix numbering, ns param drift, planned tags, confidence threshold clarity ([7bec282](https://github.com/manhhailua/lorekeep/commit/7bec282611c75494e6b75e3db8cfaa1eb9bebd59))
* harden journal write path security model ([874ee36](https://github.com/manhhailua/lorekeep/commit/874ee364525a701d8760284fe06a02b7a4a1461f))
* mark planned features as [planned] to match v1 implementation state ([6f7644f](https://github.com/manhhailua/lorekeep/commit/6f7644f75a19a0bc2fd2b4052123aff7d3a4001c))
* replace compile-only with append-and-resolve architecture, add journal and autonomous agent ([465e765](https://github.com/manhhailua/lorekeep/commit/465e765ebc078dfb6303298c0ace0ca84105e4da))

## [0.1.3](https://github.com/manhhailua/lorekeep/compare/v0.1.2...v0.1.3) (2026-06-17)


### Documentation

* add AGENTS.md as canonical agent guidance file ([#7](https://github.com/manhhailua/lorekeep/issues/7)) ([9e69cfd](https://github.com/manhhailua/lorekeep/commit/9e69cfd5952119a921c971346d1bf9350f31ec64))
* restructure docs/ into architecture + guides ([#10](https://github.com/manhhailua/lorekeep/issues/10)) ([c8747fc](https://github.com/manhhailua/lorekeep/commit/c8747fcdc55bd57066fbc5cd5e0937d06019f0e5))

## [0.1.2](https://github.com/manhhailua/lorekeep/compare/v0.1.1...v0.1.2) (2026-06-17)


### Features

* lorekeep import --from claude (memories + transcript) ([df599ae](https://github.com/manhhailua/lorekeep/commit/df599aeddeafa61b8baf51258106bc6937884566))

## [0.1.1](https://github.com/manhhailua/lorekeep/compare/v0.1.0...v0.1.1) (2026-06-16)


### Bug Fixes

* **ci:** move release-please manifest to repo root ([b6533ed](https://github.com/manhhailua/lorekeep/commit/b6533ed9822c33c80133faa43222b202dda8ed4c))
* **ci:** rename release-please config (v4 requires no leading dot) ([3d8f2be](https://github.com/manhhailua/lorekeep/commit/3d8f2be3f9c053f007264de53c28b336cee29e98))


### Documentation

* add cover image to README ([9ede2e0](https://github.com/manhhailua/lorekeep/commit/9ede2e0eb8249266629f5b9bc0b5d5a83ce4f188))

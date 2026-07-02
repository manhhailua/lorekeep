# Changelog

## [0.10.0](https://github.com/manhhailua/lorekeep/compare/v0.9.0...v0.10.0) (2026-07-02)


### Features

* cross-agent session watch + init→graph + daemon hardening ([#95](https://github.com/manhhailua/lorekeep/issues/95)) ([c95c169](https://github.com/manhhailua/lorekeep/commit/c95c169495b030a98b86d511d76c5e643ec97f96))


### Bug Fixes

* onboarding clarity — LLM purpose, compile feedback, bio→graph ([#93](https://github.com/manhhailua/lorekeep/issues/93)) ([e12f96e](https://github.com/manhhailua/lorekeep/commit/e12f96eefb5240a5cbc5132a1ca393703c62e183))

## [0.9.0](https://github.com/manhhailua/lorekeep/compare/v0.8.4...v0.9.0) (2026-07-01)


### Features

* interactive provider/model picker with litellm catalog ([#89](https://github.com/manhhailua/lorekeep/issues/89)) ([ff915f5](https://github.com/manhhailua/lorekeep/commit/ff915f590c7de72f23e8ce9e8f82453a0a69763d))

## [0.8.4](https://github.com/manhhailua/lorekeep/compare/v0.8.3...v0.8.4) (2026-06-30)


### Documentation

* fix Install + Quickstart consistency ([#85](https://github.com/manhhailua/lorekeep/issues/85)) ([87125de](https://github.com/manhhailua/lorekeep/commit/87125deff40a7318fc78391058eea143b1054ea8))

## [0.8.3](https://github.com/manhhailua/lorekeep/compare/v0.8.2...v0.8.3) (2026-06-30)


### Bug Fixes

* disable uv cache on publish job — prevents cache race warning ([#83](https://github.com/manhhailua/lorekeep/issues/83)) ([03c9598](https://github.com/manhhailua/lorekeep/commit/03c95989169cc16d0ecf4618678f895f3b1c0f94))

## [0.8.2](https://github.com/manhhailua/lorekeep/compare/v0.8.1...v0.8.2) (2026-06-30)


### Bug Fixes

* sync uv.lock via PR — branch protection blocks direct push to main ([#80](https://github.com/manhhailua/lorekeep/issues/80)) ([4b2b94f](https://github.com/manhhailua/lorekeep/commit/4b2b94fc511fd1fd2d67e01e6559224491e6931f))

## [0.8.1](https://github.com/manhhailua/lorekeep/compare/v0.8.0...v0.8.1) (2026-06-30)


### Documentation

* tighten Why section ([#78](https://github.com/manhhailua/lorekeep/issues/78)) ([4da2edd](https://github.com/manhhailua/lorekeep/commit/4da2edd8f0451535ac47d8318b657cc4ccca958d))

## [0.8.0](https://github.com/manhhailua/lorekeep/compare/v0.7.1...v0.8.0) (2026-06-30)


### Features

* add Tier-2 LoCoMo retrieval eval ([#76](https://github.com/manhhailua/lorekeep/issues/76)) ([a285409](https://github.com/manhhailua/lorekeep/commit/a285409f62584443907491c4cdb517e9dd4b9eea))

## [0.7.1](https://github.com/manhhailua/lorekeep/compare/v0.7.0...v0.7.1) (2026-06-30)


### Bug Fixes

* wire FTS5 search index + include model in extraction cache key ([#74](https://github.com/manhhailua/lorekeep/issues/74)) ([e9cd60a](https://github.com/manhhailua/lorekeep/commit/e9cd60ab573ae42b172f888cf3af5647e747c3f7))

## [0.7.0](https://github.com/manhhailua/lorekeep/compare/v0.6.1...v0.7.0) (2026-06-30)


### Features

* add scope-awareness meta tool — 9th MCP read tool ([#72](https://github.com/manhhailua/lorekeep/issues/72)) ([d7a6c95](https://github.com/manhhailua/lorekeep/commit/d7a6c950ea009bea8c22c81850336b7a3ae0eb12))

## [0.6.1](https://github.com/manhhailua/lorekeep/compare/v0.6.0...v0.6.1) (2026-06-30)


### Documentation

* sync README with shipped features ([#70](https://github.com/manhhailua/lorekeep/issues/70)) ([ea81476](https://github.com/manhhailua/lorekeep/commit/ea81476f4ad49938a5bf1d616e6658ab8b204b36))

## [0.6.0](https://github.com/manhhailua/lorekeep/compare/v0.5.0...v0.6.0) (2026-06-29)


### Features

* add Obsidian-compatible wiki output from compiled graph ([#68](https://github.com/manhhailua/lorekeep/issues/68)) ([e3e53f2](https://github.com/manhhailua/lorekeep/commit/e3e53f26f8f97c947bafe4a19485410f4deb14f6))

## [0.5.0](https://github.com/manhhailua/lorekeep/compare/v0.4.0...v0.5.0) (2026-06-28)


### Features

* add import --from codex and --from opencode + hooks for all 4 agents ([#66](https://github.com/manhhailua/lorekeep/issues/66)) ([8a1e364](https://github.com/manhhailua/lorekeep/commit/8a1e364e19cf1b45944cea2ed89d9d7468fec9c6))

## [0.4.0](https://github.com/manhhailua/lorekeep/compare/v0.3.1...v0.4.0) (2026-06-28)


### Features

* trigger memory import on Claude session end via SessionEnd hook ([#64](https://github.com/manhhailua/lorekeep/issues/64)) ([5914663](https://github.com/manhhailua/lorekeep/commit/5914663a5c02f76e1085a1f9d663066979396d77))

## [0.3.1](https://github.com/manhhailua/lorekeep/compare/v0.3.0...v0.3.1) (2026-06-28)


### Bug Fixes

* add manifest dedup to deep-mode session import ([#61](https://github.com/manhhailua/lorekeep/issues/61)) ([a9a074f](https://github.com/manhhailua/lorekeep/commit/a9a074f901c9a8f8e4cd2e3f906fdf4be7a773d6))

## [0.3.0](https://github.com/manhhailua/lorekeep/compare/v0.2.1...v0.3.0) (2026-06-28)


### Features

* zero-friction init — wire, import, compile, daemon in one command ([#55](https://github.com/manhhailua/lorekeep/issues/55)) ([26e27ad](https://github.com/manhhailua/lorekeep/commit/26e27ad70e9eae90d4ac490694b891eda01b563b))

## [0.2.1](https://github.com/manhhailua/lorekeep/compare/v0.2.0...v0.2.1) (2026-06-28)


### Bug Fixes

* repair daemon auto-compile provider construction and re-merge journals after standalone compile ([#48](https://github.com/manhhailua/lorekeep/issues/48)) ([fb05b29](https://github.com/manhhailua/lorekeep/commit/fb05b290c5c673cc36891790d62036b8b6f65c7e))
* resolve doc contradictions and false cadence claims ([#53](https://github.com/manhhailua/lorekeep/issues/53)) ([10fc794](https://github.com/manhhailua/lorekeep/commit/10fc79406b02d1fa5735274b3dd89c62653ed3ba))


### Documentation

* align docs with shipped write tools, journals, resolve, and daemon ([#50](https://github.com/manhhailua/lorekeep/issues/50)) ([372e283](https://github.com/manhhailua/lorekeep/commit/372e283b475dfb6e7c8c995bf2b650dc2da02499))

## [0.2.0](https://github.com/manhhailua/lorekeep/compare/v0.1.13...v0.2.0) (2026-06-28)


### Features

* auto-detect coding agents during init and wire MCP automatically ([#46](https://github.com/manhhailua/lorekeep/issues/46)) ([c529974](https://github.com/manhhailua/lorekeep/commit/c5299746ce12d5d12a3511a990d9711d357b0277))

## [0.1.13](https://github.com/manhhailua/lorekeep/compare/v0.1.12...v0.1.13) (2026-06-27)


### Bug Fixes

* enable minor bump for feat commits in pre-1.0 ([#43](https://github.com/manhhailua/lorekeep/issues/43)) ([0222182](https://github.com/manhhailua/lorekeep/commit/022218271fee1909c13f5f0495289f6977c6aaf1))

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

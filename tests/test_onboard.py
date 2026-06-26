from pathlib import Path

import yaml

from lorekeep.onboard import (
    PROFILE_NS,
    profile_markdown,
    write_profile,
    update_ns_default,
    run_onboarding,
)


def test_profile_markdown_exact():
    md = profile_markdown("Alice", "Eng", "Backend", "UTC")
    assert md == (
        "# Alice\n\n"
        "- **Role**: Eng\n"
        "- **What I do**: Backend\n"
        "- **Timezone**: UTC\n"
        "\n"
        "Personal namespace — facts about me live here (`raw/me/`).\n"
    )


def test_write_profile_creates_file(tmp_path: Path):
    raw = tmp_path / "raw"
    p = write_profile(raw, "# Alice\n")
    assert p == raw / PROFILE_NS / "profile.md"
    assert p.read_text(encoding="utf-8") == "# Alice\n"


def test_update_ns_default_preserves_rest(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "provider:\n"
        "  model: openai/gpt-4o-mini\n"
        "  backend: openai\n"
        "ns:\n"
        "  default: [public]\n"
        "install_source: pypi\n",
        encoding="utf-8",
    )
    update_ns_default(cfg, [PROFILE_NS, "public"])
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["ns"]["default"] == ["me", "public"]
    assert data["provider"]["model"] == "openai/gpt-4o-mini"
    assert data["install_source"] == "pypi"


def test_run_onboarding_interactive_writes_profile_and_ns(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    cfg = home / "config.yaml"
    cfg.write_text("ns:\n  default: [public]\n", encoding="utf-8")
    answers = iter(["Alice", "Eng", "Backend", "UTC"])
    ran = run_onboarding(home, cfg, interactive=True, prompt=lambda _: next(answers))
    assert ran is True
    assert (home / "raw" / "me" / "profile.md").exists()
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["ns"]["default"] == ["me", "public"]


def test_run_onboarding_non_interactive_template(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    cfg = home / "config.yaml"
    cfg.write_text("ns:\n  default: [public]\n", encoding="utf-8")
    ran = run_onboarding(home, cfg, interactive=False)
    assert ran is True
    md = (home / "raw" / "me" / "profile.md").read_text(encoding="utf-8")
    assert "**Role**: \n" in md  # empty value preserved


def test_run_onboarding_idempotent_skip(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "raw" / "me").mkdir(parents=True)
    (home / "raw" / "me" / "profile.md").write_text("existing", encoding="utf-8")
    cfg = home / "config.yaml"
    cfg.write_text("ns:\n  default: [public]\n", encoding="utf-8")
    # prompt must NOT be called when skipping
    ran = run_onboarding(home, cfg, interactive=True, prompt=lambda _: (_ for _ in ()).throw(AssertionError("prompt called")))
    assert ran is False
    assert (home / "raw" / "me" / "profile.md").read_text(encoding="utf-8") == "existing"


def test_run_onboarding_force_overwrites(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "raw" / "me").mkdir(parents=True)
    (home / "raw" / "me" / "profile.md").write_text("old", encoding="utf-8")
    cfg = home / "config.yaml"
    cfg.write_text("ns:\n  default: [public]\n", encoding="utf-8")
    answers = iter(["Alice", "Eng", "Backend", "UTC"])
    ran = run_onboarding(
        home, cfg, interactive=True, prompt=lambda _: next(answers), force=True
    )
    assert ran is True
    assert "Alice" in (home / "raw" / "me" / "profile.md").read_text(encoding="utf-8")


def test_run_onboarding_update_ns_false_preserves_ns(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "raw" / "me").mkdir(parents=True)
    (home / "raw" / "me" / "profile.md").write_text("old", encoding="utf-8")
    # A user-customized ns.default that must NOT be clobbered.
    cfg = home / "config.yaml"
    cfg.write_text("ns:\n  default: [custom]\n", encoding="utf-8")
    answers = iter(["Alice", "Eng", "Backend", "UTC"])
    ran = run_onboarding(
        home, cfg, interactive=True, prompt=lambda _: next(answers),
        force=True, update_ns=False,
    )
    assert ran is True
    # Profile was rewritten.
    assert "Alice" in (home / "raw" / "me" / "profile.md").read_text(encoding="utf-8")
    # ns.default left untouched.
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["ns"]["default"] == ["custom"]

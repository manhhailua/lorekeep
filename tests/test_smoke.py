from typer.testing import CliRunner
from lorekeep import __version__
from lorekeep.cli import app

runner = CliRunner()


def test_version_command():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"lorekeep {__version__}" in result.stdout

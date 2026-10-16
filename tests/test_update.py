from __future__ import annotations

from dea import update


def test_describe_source_checkout():
    inst = update.describe()
    assert inst.mode == "source"
    assert inst.root and (inst.commit is None or len(inst.commit) == 40)


def test_check_compares_commits(monkeypatch):
    inst = update.Install(mode="source", version="x", commit="abc123" + "0" * 34, branch="main")
    monkeypatch.setattr(update, "describe", lambda: inst)
    assert update.check(latest=lambda b: inst.commit)["update_available"] is False
    assert update.check(latest=lambda b: "f" * 40)["update_available"] is True
    assert update.check(latest=lambda b: None)["update_available"] is None
    assert update.check(latest=lambda b: inst.commit)["command"]


def test_update_runs_the_right_commands(monkeypatch, tmp_path):
    ran: list[list[str]] = []

    def fake_run(cmd):
        ran.append(cmd)
        return 0

    monkeypatch.setattr(
        update, "describe", lambda: update.Install("source", "x", root=str(tmp_path))
    )
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    assert update.update(run=fake_run, quiet=True) == 0
    assert ran[0][:4] == ["git", "-C", str(tmp_path), "pull"]
    assert "pip" in ran[1]

    ran.clear()
    monkeypatch.setattr(update, "describe", lambda: update.Install("tool", "x"))
    monkeypatch.setattr(update.shutil, "which", lambda name: "/usr/bin/uv")
    assert update.update(run=fake_run, quiet=True) == 0
    assert ran == [["/usr/bin/uv", "tool", "upgrade", "dea"]]

    ran.clear()
    monkeypatch.setattr(update, "describe", lambda: update.Install("package", "x"))
    assert update.update(run=lambda cmd: 7, quiet=True) == 7


def test_cli_version(capsys):
    from dea.cli import main

    assert main(["version"]) == 0
    assert "installed from source" in capsys.readouterr().out

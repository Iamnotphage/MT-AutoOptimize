import pytest

from app import App


class _FakeRepl:
    last_instance = None

    def __init__(self, console, runtime):
        self.console = console
        self.runtime = runtime
        self.closed = False
        _FakeRepl.last_instance = self

    def run(self):
        raise KeyboardInterrupt()

    def close(self):
        self.closed = True


def test_app_run_closes_repl_on_keyboard_interrupt(monkeypatch):
    import app as app_mod

    fake_runtime = type(
        "Runtime",
        (),
        {
            "workspace": "/tmp/project",
            "registry": type("Registry", (), {"names": ["read_file"]})(),
            "context_manager": type("CM", (), {"stats": {"loaded_files": 0, "memories_count": 0}})(),
        },
    )()

    monkeypatch.setattr(app_mod, "Repl", _FakeRepl)
    monkeypatch.setattr(
        app_mod,
        "render_banner",
        lambda console: None,
    )

    import core.agent

    monkeypatch.setattr(core.agent, "create_agent_runtime", lambda: fake_runtime)

    app = App()
    with pytest.raises(KeyboardInterrupt):
        app.run()

    assert _FakeRepl.last_instance is not None
    assert _FakeRepl.last_instance.closed is True

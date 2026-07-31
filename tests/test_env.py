from backend.env import load_env_file


def test_project_env_loader_preserves_explicit_environment(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GM_TOKEN=from-file\nEXPECTATION_DB_URL=sqlite:///./data/test.db\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("GM_TOKEN", raising=False)
    monkeypatch.setenv("EXPECTATION_DB_URL", "sqlite:///./data/explicit.db")

    assert load_env_file(env_file) is True
    assert __import__("os").environ["GM_TOKEN"] == "from-file"
    assert __import__("os").environ["EXPECTATION_DB_URL"] == "sqlite:///./data/explicit.db"

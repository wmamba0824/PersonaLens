"""Independent source, privacy and entrypoint checks; no real creator content."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_release", ROOT / "scripts/check_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_no_original_data_or_legacy_entry_is_shipped():
    for name in ("audio_trans_txt", "dynamic_txt", "xueqiu_luohuitou", "persona.md", "embeddings.npy",
                 "app/ingest/existing_md.py", "instances/mo_da"):
        assert not (ROOT / name).exists(), name
    ui = (ROOT / "app/ingest/ui.py").read_text(encoding="utf-8-sig")
    assert "render_existing_md" not in ui and "使用已有 MD" not in ui
    entry = (ROOT / "chat_app.py").read_text(encoding="utf-8-sig")
    assert '["B站人物助手", "本地资料问答"]' in entry


def test_source_audit_reports_no_source_secrets_or_broken_links():
    assert release.audit(ROOT)["findings"] == []


def test_audit_does_not_read_or_publish_local_env_and_runtime(tmp_path):
    (tmp_path / "README.md").write_text("# Synthetic project", encoding="utf-8")
    (tmp_path / ".env").write_text("PRIVATE_VALUE=must-not-appear", encoding="utf-8")
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime/profile.json").write_text('{"private": true}', encoding="utf-8")
    result = release.audit(tmp_path)
    assert not result["findings"] and result["source_files"] == 1
    assert not result["license_present"] and not result["ready_for_publication"]
    assert "must-not-appear" not in str(result)


def test_audit_reports_only_location_not_key_value(tmp_path):
    fake = "sk-" + "synthetic-test-value" * 3
    (tmp_path / "bad.py").write_text("value = '" + fake + "'", encoding="utf-8")
    result = release.audit(tmp_path)
    assert result["findings"][0]["file"] == "bad.py"
    assert fake not in str(result) and not result["ready_for_publication"]


def test_audit_rejects_legacy_data_or_nonempty_example_credentials(tmp_path):
    (tmp_path / "dynamic_txt").mkdir()
    (tmp_path / "dynamic_txt/original.txt").write_text("private original", encoding="utf-8")
    (tmp_path / ".env.example").write_text("DEEPSEEK_API_KEY=synthetic-example-value", encoding="utf-8")
    result = release.audit(tmp_path)
    assert {row["file"] for row in result["findings"]} == {"dynamic_txt/original.txt", ".env.example"}
    assert "synthetic-example-value" not in str(result)


def test_audit_rejects_missing_documentation_link(tmp_path):
    (tmp_path / "README.md").write_text("[missing](docs/absent.md)", encoding="utf-8")
    assert release.audit(tmp_path)["findings"][0]["reason"] == "missing local link"


def test_new_user_can_open_creator_and_empty_knowledge_without_network(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from streamlit.testing.v1.local_script_runner import LocalScriptRunner
    monkeypatch.setenv("MD_NETWORK_MODE", "local")
    original = LocalScriptRunner.run
    def joined(runner, *args, **kwargs):
        try:
            return original(runner, *args, **kwargs)
        finally:
            if runner._script_thread is not None:
                runner._script_thread.join(timeout=3)
                assert not runner._script_thread.is_alive()
    monkeypatch.setattr(LocalScriptRunner, "run", joined)
    app = AppTest.from_file(str(ROOT / "chat_app.py")).run(timeout=30)
    assert not app.exception
    assert app.sidebar.radio[0].value == "B站人物助手"
    assert any(item.label == "B站主页或UID" for item in app.text_input)
    assert not any(item.label == "人物入口" for item in app.radio)
    app.sidebar.radio[0].set_value("本地资料问答").run(timeout=30)
    assert not app.exception and len(app.chat_input) == 1 and app.chat_input[0].disabled

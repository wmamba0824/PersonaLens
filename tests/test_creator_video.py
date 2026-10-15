"""Offline contracts for bounded video -> portrait -> index -> chat integration."""

import json
from pathlib import Path

import numpy as np
import pytest

from app.ingest.bilibili import CollectionError
from app.ingest.identity import parse_creator
from app.ingest.store import CreatorStore
from app.ingest import video
from app.ingest.persona import generate_portrait, chunks_for, portrait_sample
from app.ingest.conversation import build_index, stream_creator_response


class Response:
    def __init__(self, data, status=200, headers=None):
        self.raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.status_code, self.headers, self.closed = status, headers or {}, False

    def iter_content(self, size):
        yield self.raw

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses, self.calls, self.cookies, self.headers = list(responses), [], {}, {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert kwargs["timeout"] == (5, 15)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def api(data, code=0):
    return Response({"code": code, "data": data})


def item(number=0, uid="123"):
    return {"type": "DYNAMIC_TYPE_AV", "id_str": str(100 + number), "modules": {
        "module_author": {"mid": int(uid), "name": "测试作者"}, "module_dynamic": {"major": {
            "type": "MAJOR_TYPE_ARCHIVE", "archive": {"bvid": "BV123456789" + str(number), "duration_text": "05:00"}}}}}


def view(number=0, uid="123"):
    return {"bvid": "BV123456789" + str(number), "aid": 100 + number, "cid": 200 + number,
            "owner": {"mid": int(uid), "name": "测试作者"}, "duration": 300, "pubdate": 1700000000,
            "rights": {}, "title": "可核对的项目经验"}


def player():
    return {"subtitle": {"subtitles": [{"lan": "ai-zh", "subtitle_url": "//aisubtitle.hdslb.com/sub.json"}]}}


def subtitles(number=0):
    return {"body": [{"from": 1, "to": 90, "content": f"项目{number}代号蓝鲸，做事先验证需求再投入。" * 25}]}


def sessions(count=3):
    responses = [api({"items": [item(i) for i in range(count)], "has_more": False})]
    for i in range(count):
        responses.extend([api(view(i)), api(player())])
    return Session(responses), Session([Response(subtitles(i)) for i in range(count)])


def test_video_pipeline_has_provenance_caches_and_never_uses_title_as_speech(tmp_path):
    store = CreatorStore(parse_creator("123"), tmp_path)
    api_session, media = sessions()
    result = video.collect_video_text(store, session=api_session, media_session=media)
    assert len(result["records"]) == 3 and result["coverage"]["subtitle"] == 3
    assert not api_session.responses and not media.responses
    assert all(url.startswith(video.API) for url, _ in api_session.calls)
    assert all("Cookie" not in kwargs["headers"] for _, kwargs in media.calls)
    record = result["records"][0]
    assert record["title"] not in record["text"] and "[1.0–90.0s]" in record["text"]
    assert record["cid"] == 200 and record["source_type"] == "subtitle" and record["sample_complete"] is False
    assert "不代表每句话" in record["speaker_note"]
    with store.operation("collect"):
        store.save_collection(result)
    assert store.read()["collection"]["coverage"]["subtitle"] == 3
    repeated = Session([api({"items": [item(i) for i in range(3)], "has_more": False})]
                       + [api(view(i)) for i in range(3)])
    cached = video.collect_video_text(store, session=repeated, media_session=Session([]))
    assert cached["coverage"]["cache"] == 3 and not repeated.responses
    with store.operation("collect"):
        store.save_collection(cached)
    assert len(store.read()["records"]) == 3
    calls = []
    def model(messages):
        calls.append(messages)
        yield "先验证再投入，短句解释。[样本1]" if "画像草稿" in messages[0]["content"] else "先验证需求，代号蓝鲸。[资料1]"
    draft = generate_portrait(store, consent=True, chat=model)
    assert "来源类型：subtitle" in calls[0][1]["content"]
    store.approve(draft["text"])
    index, status = build_index(store, store.read(), model_factory=lambda name: Encoder())
    assert index is not None and "向量就绪" in status
    pieces = list(stream_creator_response(store, "项目代号", [], index=index, chat=model))
    assert not pieces[-1]["lexical"] and pieces[-1]["sources"][0]["bvid"]
    assert "视频归该账号" in calls[-1][0]["content"]
    assert len(calls) == 2


class Encoder:
    def embed(self, texts, **kwargs):
        return iter(np.array([1., 2., 3.], dtype=np.float32) for _ in texts)

    def query_embed(self, texts):
        return self.embed(texts)


@pytest.mark.parametrize("url", ["http://aisubtitle.hdslb.com/a", "https://127.0.0.1/a", "https://localhost/a",
    "https://hdslb.com.evil.test/a", "https://evil.test/a", "https://user:password@aisubtitle.hdslb.com/a",
    "https://aisubtitle.hdslb.com:8080/a", "file:///a", "https://aisubtitle.hdslb.com/a#fragment"])
def test_media_allowlist_rejects_untrusted_urls(url):
    with pytest.raises(CollectionError):
        video.safe_media_url(url)


@pytest.mark.parametrize("change", ["forward", "other", "paid", "member", "cooperation"])
def test_paid_forwarded_other_owner_and_cooperation_are_excluded(tmp_path, change):
    source, metadata = item(), view()
    if change == "forward":
        source["orig"] = {}
        source["type"] = "DYNAMIC_TYPE_FORWARD"
    elif change == "other":
        metadata["owner"]["mid"] = 456
    elif change == "paid":
        metadata["rights"]["ugc_pay_preview"] = 1
    elif change == "member":
        source["basic"] = {"is_only_fans": True}
    else:
        metadata["rights"]["is_cooperation"] = 1
    session = Session([api({"items": [source], "has_more": False}), api(metadata)])
    result = video.collect_video_text(CreatorStore(parse_creator("123"), tmp_path), session=session, media_session=Session([]))
    assert not result["records"]


def test_no_subtitle_without_asr_consent_never_downloads_audio(tmp_path):
    session = Session([api({"items": [item()], "has_more": False}), api(view()), api({"subtitle": {"subtitles": []}})])
    media = Session([])
    result = video.collect_video_text(CreatorStore(parse_creator("123"), tmp_path), session=session, media_session=media)
    assert not result["records"] and not media.calls and "未授权" in result["coverage"]["skipped"][0]["reason"]


def test_asr_uses_separate_audio_session_bounded_sample_and_deletes_temp_audio(tmp_path):
    session = Session([api({"items": [item()], "has_more": False}), api(view()), api({"subtitle": {"subtitles": []}}),
        api({"dash": {"audio": [{"bandwidth": 64000, "baseUrl": "https://upos.bilivideo.com/audio.m4a?sign=private"}]}})])
    media, seen = Session([Response(b"audio bytes")]), []
    def transcribe(path, **kwargs):
        seen.append(Path(path))
        assert path.read_bytes() == b"audio bytes" and kwargs["sample_seconds"] == 180
        return {"language": "zh", "segments": subtitles()["body"]}
    store = CreatorStore(parse_creator("123"), tmp_path)
    result = video.collect_video_text(store, allow_transcribe=True, session=session, media_session=media, transcriber=transcribe)
    assert result["records"][0]["source_type"] == "transcript"
    assert result["records"][0]["truncated"] and not seen[0].exists()
    assert "sign=private" not in json.dumps(result)


def test_http_refusal_stops_instead_of_trying_next_video(tmp_path):
    session = Session([api({"items": [item(), item(1)], "has_more": False}), Response(b"blocked", 412)])
    result = video.collect_video_text(CreatorStore(parse_creator("123"), tmp_path), session=session, media_session=Session([]))
    assert result["coverage"]["attempted"] == 1 and "平台限制" in result["warning"]


def test_download_refuses_redirect_oversize_and_credentials():
    for response in [Response(b"moved", 302), Response(b"x", headers={"Content-Length": str(video.MAX_JSON_BYTES + 1)})]:
        with pytest.raises(CollectionError):
            video._media(Session([response]), "https://aisubtitle.hdslb.com/sub.json")
        assert response.closed
    session = Session([])
    session.cookies = {"SESSDATA": "not-a-real-cookie"}
    with pytest.raises(CollectionError, match="凭据"):
        video._media(session, "https://aisubtitle.hdslb.com/sub.json")
    assert not session.calls


def test_one_video_never_satisfies_three_record_portrait_gate(tmp_path):
    store = CreatorStore(parse_creator("123"), tmp_path)
    session, media = sessions(1)
    store.save_collection(video.collect_video_text(store, session=session, media_session=media))
    with pytest.raises(ValueError, match="至少需要3条"):
        generate_portrait(store, consent=True, chat=lambda messages: pytest.fail("No paid request"))
    assert not store.read()["profile"] and len(chunks_for(store.read())) > 1


def test_changed_video_text_invalidates_approved_portrait(tmp_path):
    store = CreatorStore(parse_creator("123"), tmp_path)
    session, media = sessions()
    result = video.collect_video_text(store, session=session, media_session=media)
    store.save_collection(result)
    profile = generate_portrait(store, consent=True, chat=lambda messages: iter(["先验证。[样本1]"]))
    store.approve(profile["text"])
    result["records"][0]["text"] += " 新补充内容。"
    store.save_collection(result)
    assert not store.profile_fresh()
    with pytest.raises(ValueError, match="确认"):
        list(stream_creator_response(store, "问题", [], chat=lambda messages: pytest.fail("No paid request")))


def test_malformed_subtitle_and_music_only_short_text_do_not_become_knowledge(tmp_path):
    session, _ = sessions(1)
    result = video.collect_video_text(CreatorStore(parse_creator("123"), tmp_path), session=session,
        media_session=Session([Response({"body": [{"from": 0, "to": 3, "content": "啦啦啦"}]})]))
    assert not result["records"]


def test_marked_song_lyrics_never_trigger_asr_or_portrait(tmp_path):
    session, _ = sessions(1)
    media = Session([Response({"body": [{"from": 0, "to": 90, "content": "♪ 歌词内容并非人物观点 ♪" * 30}]})])
    result = video.collect_video_text(CreatorStore(parse_creator("123"), tmp_path), allow_transcribe=True,
        session=session, media_session=media, transcriber=lambda *a, **kw: pytest.fail("Do not re-transcribe known lyrics"))
    assert not result["records"] and "歌词" in result["coverage"]["skipped"][0]["reason"]
    assert len(media.calls) == 1


def test_mixed_subtitles_drop_music_and_preserve_spoken_lines():
    segments = video._segments([
        {"from": 1, "to": 5, "content": "♪ 歌词 ♪"},
        {"from": 6, "to": 9, "content": "这是正常口述，需要保留。"}], 10)
    assert len(segments) == 1 and segments[0]["from"] == 6


def test_timestamps_and_lyrics_do_not_count_toward_portrait_minimum(tmp_path):
    from app.ingest.persona import portrait_metrics
    assert portrait_metrics({"records": [{"type": "video", "text": "时间码" * 500,
        "segments": [{"content": "♪ 配乐 ♪"}, {"content": "有效口述"}]}]}) == (1, 4)


def test_missing_local_model_never_implicitly_downloads_or_fetches_audio(tmp_path, monkeypatch):
    session = Session([api({"items": [item()], "has_more": False}), api(view()), api({"subtitle": {"subtitles": []}})])
    monkeypatch.setattr(video, "transcription_status", lambda path=None: (False, "本地转录模型未准备"))
    media = Session([])
    result = video.collect_video_text(CreatorStore(parse_creator("123"), tmp_path), allow_transcribe=True,
        session=session, media_session=media)
    assert not result["records"] and not media.calls and "模型未准备" in result["coverage"]["skipped"][0]["reason"]


def test_long_video_without_subtitles_does_not_download_audio(tmp_path):
    metadata = view()
    metadata["duration"] = 3600
    session = Session([api({"items": [item()], "has_more": False}), api(metadata), api({"subtitle": {"subtitles": []}})])
    result = video.collect_video_text(CreatorStore(parse_creator("123"), tmp_path), allow_transcribe=True,
        session=session, media_session=Session([]), transcriber=lambda *a, **kw: pytest.fail("No long ASR"))
    assert not result["records"] and "30分钟" in result["coverage"]["skipped"][0]["reason"]


def test_preview_audio_and_untrusted_audio_urls_are_refused(tmp_path):
    for play in [{"is_preview": True}, {"dash": {"audio": [{"baseUrl": "https://evil.test/audio"}]}}]:
        session = Session([api({"items": [item()], "has_more": False}), api(view()), api({"subtitle": {"subtitles": []}}), api(play)])
        media = Session([])
        result = video.collect_video_text(CreatorStore(parse_creator("123"), tmp_path), allow_transcribe=True,
            session=session, media_session=media, transcriber=lambda *a, **kw: pytest.fail("No ASR"))
        assert not result["records"] and not media.calls


def test_asr_timeout_is_safe_and_worker_is_bounded(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(video, "transcription_status", lambda path=None: (True, "ready"))
    def timeout(command, **kwargs):
        assert "app.ingest.whisper_worker" in command
        assert kwargs["timeout"] == 30 and kwargs["capture_output"]
        raise subprocess.TimeoutExpired(command, 30, output="private output")
    monkeypatch.setattr(video, "run_bounded", timeout)
    with pytest.raises(CollectionError, match="子进程已停止") as error:
        video.transcribe_local(tmp_path / "audio.m4a", timeout=30)
    assert "private output" not in str(error.value)


def test_asr_worker_is_offline_and_does_not_inherit_platform_or_api_credentials(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(video, "transcription_status", lambda path=None: (True, "ready"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-provider-value")
    monkeypatch.setenv("BILI_SESSDATA", "test-platform-value")
    monkeypatch.setenv("BUVID3", "test-device-value")
    monkeypatch.setenv("HF_TOKEN", "test-hf-value")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    def run(command, **kwargs):
        env = kwargs["env"]
        assert env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1"
        assert not {"DEEPSEEK_API_KEY", "BILI_SESSDATA", "BUVID3", "HF_TOKEN"}.intersection(env)
        return SimpleNamespace(returncode=0, stdout='{"language":"zh","segments":[]}')
    monkeypatch.setattr(video, "run_bounded", run)
    assert video.transcribe_local(tmp_path / "audio.m4a")["language"] == "zh"
    assert "HF_HUB_OFFLINE" not in __import__("os").environ


def test_duplicate_videos_and_page_and_video_limits_are_bounded(tmp_path):
    session, media = sessions(1)
    session.responses[0] = api({"items": [item(), item(), item(1)], "has_more": True, "offset": "next"})
    result = video.collect_video_text(CreatorStore(parse_creator("123"), tmp_path), max_videos=1,
        session=session, media_session=media)
    assert len(result["records"]) == 1 and result["coverage"]["attempted"] == 1 and not result["complete"]
    assert len(session.calls) == 3
    for kwargs in [{"max_videos": 6}, {"max_pages": 6}, {"sample_seconds": 601}]:
        with pytest.raises(ValueError):
            video.collect_video_text(CreatorStore(parse_creator("123"), tmp_path), **kwargs)


def test_uid_caches_do_not_cross_even_for_same_bvid(tmp_path):
    one = CreatorStore(parse_creator("123"), tmp_path)
    session, media = sessions(1)
    video.collect_video_text(one, session=session, media_session=media)
    other = CreatorStore(parse_creator("456"), tmp_path)
    session = Session([api({"items": [item(uid="456")], "has_more": False}), api(view()), api(player())])
    result = video.collect_video_text(other, session=session, media_session=Session([]))
    assert not result["records"] and result["coverage"]["cache"] == 0


def test_explicit_model_downloader_has_hard_timeout_without_silent_retry(monkeypatch):
    import subprocess
    import sys
    import dotenv
    from scripts import prepare_video_model
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.setattr(video, "transcription_status", lambda path=None: (False, "not ready"))
    monkeypatch.setattr(sys, "argv", ["prepare_video_model.py", "--download"])
    calls = []
    def timed_out(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 600 and "--worker" in command
        raise subprocess.TimeoutExpired(command, 600)
    monkeypatch.setattr(prepare_video_model, "run_bounded", timed_out)
    assert prepare_video_model.main() == 1 and len(calls) == 1


def test_local_asr_diagnostic_defaults_to_dry_run_and_never_transcribes(monkeypatch):
    from scripts import check_local_transcription
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.setattr(video, "transcription_status", lambda path=None: (False, "not ready"))
    monkeypatch.setattr(video, "transcribe_local", lambda *a, **kw: pytest.fail("No inference on dry run"))
    assert check_local_transcription.main([]) == 0


def test_local_asr_diagnostic_refuses_missing_model_before_inference(tmp_path, monkeypatch):
    from scripts import check_local_transcription
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.setattr(video, "transcription_status", lambda path=None: (False, "not ready"))
    monkeypatch.setattr(video, "transcribe_local", lambda *a, **kw: pytest.fail("No model, no inference"))
    audio = tmp_path / "owned_audio.wav"
    audio.write_bytes(b"test")
    with pytest.raises(SystemExit):
        check_local_transcription.main(["--execute", "--audio", str(audio)])


def test_web_video_flow_consent_approval_chat_refresh_and_creator_isolation(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from streamlit.testing.v1.local_script_runner import LocalScriptRunner
    from app.ingest import store as storage, ui, conversation
    from app.llm import client
    from app.ui import knowledge_chat
    monkeypatch.setenv("MD_NETWORK_MODE", "local")
    monkeypatch.setattr(storage, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(knowledge_chat, "render_knowledge_chat", lambda: None)
    monkeypatch.setattr(conversation, "get_local_model", lambda name: Encoder())
    monkeypatch.setattr(ui, "transcription_status", lambda: (False, "本地转录未准备"))
    acquisitions, model_calls = [], []
    def acquire(store, **kwargs):
        acquisitions.append(kwargs)
        session, media = sessions()
        return video.collect_video_text(store, session=session, media_session=media)
    def model(messages):
        model_calls.append(messages)
        yield "先验证。[样本1]" if "画像草稿" in messages[0]["content"] else "先验证需求。[资料1]"
    monkeypatch.setattr(ui, "collect_video_text", acquire)
    monkeypatch.setattr(client, "stream_chat", model)
    original_run = LocalScriptRunner.run
    def joined(runner, *args, **kwargs):
        try:
            return original_run(runner, *args, **kwargs)
        finally:
            if runner._script_thread is not None:
                runner._script_thread.join(timeout=3)
    monkeypatch.setattr(LocalScriptRunner, "run", joined)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "chat_app.py")).run(timeout=25)
    app.sidebar.radio[0].set_value("B站人物助手").run(timeout=25)
    def button(label):
        return next(widget for widget in app.button if widget.label == label)
    def check(fragment):
        return next(widget for widget in app.checkbox if fragment in widget.label)
    app.text_input[0].set_value("123")
    button("打开 / 创建人物").click().run(timeout=25)
    assert button("采集视频资料").disabled and not acquisitions and not model_calls
    check("我有权").check().run(timeout=25)
    button("采集视频资料").click().run(timeout=25)
    assert not app.exception and len(acquisitions) == 1 and not acquisitions[0]["allow_transcribe"]
    check("12,000字").check().run(timeout=25)
    button("生成人物画像草稿").click().run(timeout=25)
    assert not app.exception and len(model_calls) == 1 and app.chat_input == []
    check("我已核对").check().run(timeout=25)
    button("确认画像，准备知识库").click().run(timeout=25)
    check("同意提问").check().run(timeout=25)
    app.chat_input[0].set_value("如何验证需求").run(timeout=25)
    assert not app.exception and len(model_calls) == 2
    assert app.session_state["creator_messages"][-1]["sources"][0]["source_type"] == "subtitle"
    app.run(timeout=25)
    assert len(model_calls) == 2 and len(acquisitions) == 1
    app.text_input[0].set_value("456")
    button("打开 / 创建人物").click().run(timeout=25)
    assert not app.exception and not app.session_state["creator_messages"]
    assert button("采集视频资料").disabled

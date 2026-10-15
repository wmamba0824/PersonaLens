"""Offline same-creator multi-source import, without paid or platform requests."""

import copy
import hashlib
import json

import pytest

from app.ingest.identity import parse_creator
from app.ingest.store import CreatorStore
from app.ingest.wechat import public_article_url, read_wechat_export, import_wechat_articles
from app.ingest.persona import chunks_for, generate_portrait, portrait_sample
from app.ingest.conversation import prepare_creator_context, stream_creator_response


def article(number=0, account="测试公众号"):
    return {"catalog": {"appmsgid": str(1000 + number), "title": "验证需求的复盘" + str(number),
                       "send_time": "1700000000", "deleted": "0", "copyright_type": "1"},
            "fetch_status": "ok", "http_status": 200, "body_source": "js_content",
            "account_name": account, "author_name": "测试署名", "title_matches_catalog": True,
            "final_url": "https://mp.weixin.qq.com/s/public_article_" + str(number),
            "body_text": f"项目{number}：先验证需求，再决定投入。过往观点要保留日期，不保证未来结果。" * 12}


def exported(tmp_path, rows=None):
    source = tmp_path / "source"
    source.mkdir()
    for i, row in enumerate(rows if rows is not None else [article(i) for i in range(3)]):
        (source / (str(i) + ".json")).write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return source


def test_requires_explicit_same_creator_link_before_import(tmp_path):
    store = CreatorStore(parse_creator("123"), tmp_path / "runtime")
    source = exported(tmp_path)
    with pytest.raises(ValueError, match="同一作者"):
        import_wechat_articles(store, source, "测试公众号")
    assert not store.path.exists()


@pytest.mark.parametrize("changed", ["account", "summary", "missing", "deleted", "title", "non_article", "short", "temp_body"])
def test_conservative_source_filters_do_not_promote_derived_or_missing_text(tmp_path, changed):
    row = article()
    if changed == "account": row["account_name"] = "另一个作者"
    elif changed == "summary": row["body_source"] = "og_description"
    elif changed == "missing": row["fetch_status"] = "missing"
    elif changed == "deleted": row["catalog"]["deleted"] = "1"
    elif changed == "title": row["title_matches_catalog"] = False
    elif changed == "non_article": row = {"report": "Derived machine profile"}
    elif changed == "short": row["body_text"] = "短"
    else: row["body_text"] += " https://mp.weixin.qq.com/s?tempkey=temporary-reader-value"
    result = read_wechat_export(exported(tmp_path, [row]), parse_creator("123"), "测试公众号", True)
    assert result["records"] == [] and result["skipped"] == 1


def test_stable_public_urls_never_keep_session_or_temporary_reader_fields():
    assert public_article_url("http://mp.weixin.qq.com/s/public_article?tempkey=private&scene=1#rd") == "https://mp.weixin.qq.com/s/public_article"
    query = "https://mp.weixin.qq.com/s?__biz=ABCD&mid=123&idx=1&sn=" + "a" * 32 + "&tempkey=private&pass_ticket=private"
    safe = public_article_url(query)
    assert safe.startswith("https://mp.weixin.qq.com/s?") and "tempkey" not in safe and "pass_ticket" not in safe
    for raw in ["https://mp.weixin.qq.com.evil.test/s/public_article", "https://user@mp.weixin.qq.com/s/public_article",
                "https://mp.weixin.qq.com/s?tempkey=private", "http://127.0.0.1/s/article", "https://mp.weixin.qq.com:8080/s/article"]:
        assert public_article_url(raw) == ""


def test_import_is_idempotent_readonly_and_does_not_touch_other_creators(tmp_path):
    source = exported(tmp_path)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob("*.json")}
    store = CreatorStore(parse_creator("123"), tmp_path / "runtime")
    other = CreatorStore(parse_creator("456"), tmp_path / "runtime")
    result = import_wechat_articles(store, source, "测试公众号", True)
    data = store.read()
    assert len(data["records"]) == 3 and not data["profile"] and not other.path.exists()
    assert data["linked_sources"]["wechat"]["测试公众号"]["identity_link"] == "user_confirmed_same_creator"
    digest = data["corpus_digest"]
    import_wechat_articles(store, source, "测试公众号", True)
    assert store.read()["corpus_digest"] == digest and len(store.read()["records"]) == 3
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob("*.json")}
    assert not (store.path / "operation.lock").exists()


def test_wechat_source_survives_portrait_approval_retrieval_and_chat(tmp_path):
    store = CreatorStore(parse_creator("123"), tmp_path / "runtime")
    import_wechat_articles(store, exported(tmp_path), "测试公众号", True)
    prompts = []
    def model(messages):
        prompts.append(messages)
        yield "短句，先验证需求。[样本1]" if "画像草稿" in messages[0]["content"] else "先验证需求。[资料1]"
    draft = generate_portrait(store, consent=True, chat=model)
    assert "公众号原文" in prompts[0][1]["content"] and "由用户确认" in prompts[0][1]["content"]
    store.approve(draft["text"])
    pieces = list(stream_creator_response(store, "验证需求", [], chat=model))
    assert pieces[-1]["sources"][0]["source_platform"] == "wechat"
    assert pieces[-1]["sources"][0]["title"].startswith("验证需求")
    assert "https://mp.weixin.qq.com/s/public_article" in prompts[-1][0]["content"]
    assert len(prompts) == 2


def test_new_article_invalidates_approval_and_unknown_dates_are_not_guessed(tmp_path):
    source = exported(tmp_path)
    store = CreatorStore(parse_creator("123"), tmp_path / "runtime")
    import_wechat_articles(store, source, "测试公众号", True)
    draft = generate_portrait(store, consent=True, chat=lambda messages: iter(["短句。[样本1]"]))
    store.approve(draft["text"])
    added = article(3)
    added["catalog"]["send_time"] = ""
    (source / "extra.json").write_text(json.dumps(added), encoding="utf-8")
    import_wechat_articles(store, source, "测试公众号", True)
    assert not store.profile_fresh()
    unknown = next(r for r in store.read()["records"] if r["article_id"] == "1003")
    assert unknown["published_at"] == "日期未知" and unknown["date_source"] == "unknown"
    with pytest.raises(ValueError):
        list(stream_creator_response(store, "问题", [], chat=lambda m: pytest.fail("No paid call")))


def test_duplicate_body_does_not_make_three_independent_samples(tmp_path):
    rows = [article(i) for i in range(3)]
    for row in rows: row["body_text"] = rows[0]["body_text"]
    result = read_wechat_export(exported(tmp_path, rows), parse_creator("123"), "测试公众号", True)
    assert len(result["records"]) == 1 and result["skipped"] == 2

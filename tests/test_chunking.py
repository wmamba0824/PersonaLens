"""Structure and overlap contracts, not a claim about live model quality."""

from app.rag.chunking import document_chunks


def test_heading_hierarchy_is_attached_to_body_not_indexed_as_empty_facts():
    chunks = list(document_chunks("# 总标题\n\n## 第一章\n\n正文一\n\n## 第二章\n\n正文二"))
    assert [item["text"] for item in chunks] == ["# 总标题\n## 第一章\n正文一", "# 总标题\n## 第二章\n正文二"]
    assert "第一章" not in chunks[1]["heading"]


def test_long_chinese_text_preserves_overlap_and_never_crosses_sections():
    text = "# 上文\n\n" + "甲乙丙丁。" * 180 + "\n\n# 下文\n\n另一个主题"
    chunks = list(document_chunks(text))
    first = [item for item in chunks if item["heading"] == "# 上文"]
    assert len(first) > 2 and all(len(item["text"]) <= 400 for item in chunks)
    assert first[0]["text"][-60:] in first[1]["text"]
    assert chunks[-1]["text"] == "# 下文\n另一个主题"
    assert all("另一个主题" not in item["text"] for item in first)


def test_code_fences_are_text_not_article_headings_and_links_are_not_fetched():
    chunks = list(document_chunks("# 文章\n\n```python\n# code comment\nraise RuntimeError('text only')\n```\n\n[链接](https://example.invalid/no-fetch)"))
    assert all(item["heading"] == "# 文章" for item in chunks)
    assert "# code comment" in chunks[0]["text"]
    assert "https://example.invalid/no-fetch" in chunks[-1]["text"]


def test_empty_heading_only_and_small_plain_text_are_supported():
    assert list(document_chunks(" \n")) == []
    assert list(document_chunks("# 只有标题"))[0]["text"] == "# 只有标题"
    assert [item["text"] for item in document_chunks("段一\n\n段二")] == ["段一", "段二"]

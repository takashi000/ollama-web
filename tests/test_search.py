"""Search policy and result-boundary regression tests (no live network)."""

import json
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import quote

import pytest
from ollama._utils import convert_function_to_tool

from ollama_web.config import Settings, settings
from ollama_web.tools import search
from ollama_web.tools.helper.pagination import parse_paged_result
from ollama_web.tools.registry import default_registry


@pytest.fixture
def provider(monkeypatch):
    calls = []
    rows = [{"title": "Recipe", "href": "https://example.com/recipe", "body": "Summary"}]

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def text(self, query, **kwargs):
            calls.append((query, kwargs))
            return rows

    monkeypatch.setattr(search, "DDGS", FakeDDGS)
    monkeypatch.setattr(settings, "search_region", "auto")
    return calls, rows


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "auto"),
        ("auto", "auto"),
        ("us-en", "us-en"),
        ("jp-jp", "jp-jp"),
        (" JP-JP ", "jp-jp"),
    ],
)
def test_region_setting(monkeypatch, value, expected):
    monkeypatch.delenv("OLLAMA_WEB_SEARCH_REGION", raising=False)
    if value is not None:
        monkeypatch.setenv("OLLAMA_WEB_SEARCH_REGION", value)
    assert Settings().search_region == expected


@pytest.mark.parametrize("value", ["", "ja", "jp", "en-US", "jp-ja"])
def test_invalid_region_setting(monkeypatch, value):
    monkeypatch.setenv("OLLAMA_WEB_SEARCH_REGION", value)
    with pytest.raises(ValueError, match="OLLAMA_WEB_SEARCH_REGION.*auto, us-en, jp-jp"):
        Settings()


def test_invalid_region_fails_at_import(monkeypatch):
    monkeypatch.setenv("OLLAMA_WEB_SEARCH_REGION", "invalid")
    process = subprocess.run(
        [sys.executable, "-c", "import ollama_web.config"], capture_output=True, text=True
    )
    assert process.returncode != 0
    assert "OLLAMA_WEB_SEARCH_REGION must be one of: auto, us-en, jp-jp" in process.stderr


@pytest.mark.parametrize(
    "query,region",
    [
        ("カレー おすすめ レシピ", "jp-jp"),
        ("引越し 費用 相場", "jp-jp"),
        ("ｶﾚｰ ﾚｼﾋﾟ", "jp-jp"),
        ("best curry recipe", "us-en"),
        ("東京 天気", "us-en"),
        ("北京 天气", "us-en"),
        ("рецепт карри", "us-en"),
        ("curry レシピ", "jp-jp"),
        ("カレー site:cookpad.com filetype:pdf", "jp-jp"),
        ("recipe https://example.com/カレー", "us-en"),
        ("recipe WWW.example.com/レシピ", "us-en"),
        ("recipe SITE:レシピ.example -filetype:カレー", "us-en"),
        ("recipe https://example.com/カレー おすすめ", "jp-jp"),
    ],
)
def test_region_and_original_query(provider, query, region):
    calls, _ = provider
    search.web_search(query, 2)
    assert calls == [(query, {"max_results": 2, "region": region, "backend": "auto"})]


@pytest.mark.parametrize("region,query", [("us-en", "カレー"), ("jp-jp", "best curry recipe")])
def test_fixed_region_overrides_query(provider, monkeypatch, region, query):
    monkeypatch.setattr(settings, "search_region", region)
    search.web_search(query)
    assert provider[0][0][1]["region"] == region


@pytest.mark.parametrize("query", ["", " \n\t"])
def test_empty_query_does_not_search(provider, query):
    assert search.web_search(query) == []
    assert provider[0] == []


def test_concurrent_regions_are_local(monkeypatch):
    barrier = Barrier(2)
    monkeypatch.setattr(settings, "search_region", "auto")

    class ConcurrentDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def text(self, query, **kwargs):
            barrier.wait(timeout=5)
            return [{"title": kwargs["region"], "href": "https://example.com", "body": query}]

    monkeypatch.setattr(search, "DDGS", ConcurrentDDGS)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(search.web_search, ["カレー", "curry"]))
    assert [r[0]["title"] for r in results] == ["jp-jp", "us-en"]
    assert settings.search_region == "auto"


@pytest.mark.parametrize(
    "title,expected",
    [
        (None, ""),
        ("", ""),
        ("  ", ""),
        ("Recipe", "Recipe"),
        ("https://www.example.com/カレー/", ""),
        ("www.example.com/カレー", ""),
        ("example.com/%E3%82%AB%E3%83%AC%E3%83%BC/", ""),
        ("https://other.example/recipe", ""),
        ("other.example/recipe", ""),
        ("example.com の使い方", "example.com の使い方"),
        ("Recipe at example.com", "Recipe at example.com"),
        ("Python3.12", "Python3.12"),
        ("v9.16.0", "v9.16.0"),
    ],
)
def test_title_semantics(provider, title, expected):
    _, rows = provider
    rows[:] = [
        {"title": title, "href": "https://www.example.com/カレー/", "body": "A real summary"}
    ]
    assert search.web_search("recipe") == [
        {"title": expected, "href": "https://www.example.com/カレー/", "body": "A real summary"}
    ]


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "/relative",
        "javascript:alert(1)",
        "ftp://example.com/file",
        "https:///path",
        "https://bad host.example",
        "https://[invalid",
        "https://example.com:bad",
        "https://example.com:99999",
        "https://example..com",
        "https://example.com/\npath",
    ],
)
def test_invalid_url_is_omitted(provider, url):
    provider[1][:] = [{"title": "Invalid", "href": url}]
    assert search.web_search("recipe") == []
    assert len(provider[0]) == 1


def test_fallbacks_and_no_extra_network(provider, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Search normalization must not resolve or fetch URLs")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    provider[1][:] = [
        {
            "title": None,
            "href": None,
            "url": "https://example.com/a",
            "body": None,
            "snippet": "Fallback",
        }
    ]
    assert search.web_search("recipe") == [
        {"title": "", "href": "https://example.com/a", "body": "Fallback"}
    ]


@pytest.mark.parametrize(
    "href,expected",
    [
        ("https://bing.com/aclick?u=ad", None),
        ("https://www.bing.com/aclick?u=ad", None),
        ("https://www.bing.com./aclick?u=ad", None),
        (
            "https://r.search.yahoo.com/a/RU="
            + quote("https://www.bing.com/aclick?u=ad", safe="")
            + "/RK=2/RS=x",
            None,
        ),
        (
            "https://r.search.yahoo.com/a/RU="
            + quote("https://example.com/article?q=1", safe="")
            + "/RK=2/RS=x",
            "https://example.com/article?q=1",
        ),
        ("https://r.search.yahoo.com/a/RU=javascript%3Aalert(1)/RK=2", None),
        ("https://www.bing.com/search?q=curry", "https://www.bing.com/search?q=curry"),
        ("https://example.com/RU=article/RK=2", "https://example.com/RU=article/RK=2"),
        ("https://r.search.yahoo.com/article", "https://r.search.yahoo.com/article"),
    ],
)
def test_known_ad_and_wrapper_boundaries(provider, href, expected):
    provider[1][:] = [{"title": "Article", "href": href, "body": "Summary"}]
    result = search.web_search("article")
    assert [r["href"] for r in result] == ([] if expected is None else [expected])


@pytest.mark.parametrize("limit,expected", [(None, 2), (0, 2), (-1, 2), (1, 1), (3, 3)])
def test_limits_and_order(provider, monkeypatch, limit, expected):
    monkeypatch.setattr(settings, "search_max_results", 2)
    provider[1][:] = [{"title": str(i), "href": f"https://example.com/{i}"} for i in range(4)]
    assert [r["title"] for r in search.web_search("recipe", limit)] == [
        str(i) for i in range(expected)
    ]
    assert provider[0][0][1]["max_results"] == expected


def test_omitted_results_do_not_trigger_retry(provider):
    provider[1][:] = [
        {"href": "https://bing.com/aclick?u=ad"},
        {"title": "Kept", "href": "https://example.com/article"},
    ]
    assert len(search.web_search("recipe", 5)) == 1
    assert len(provider[0]) == 1


def test_public_schema_and_registry_result(provider):
    schema = convert_function_to_tool(search.web_search).function.parameters
    assert set(schema.properties) == {"query", "max_results"}
    assert schema.required == ["query"]
    results = json.loads(default_registry().execute("web_search", {"query": "カレー"}))
    assert set(results[0]) == {"title", "href", "body"}
    assert provider[0][0][1]["region"] == "jp-jp"


def test_search_and_fetch_uses_cleaned_candidates_and_continues(provider, monkeypatch):
    from ollama_web.tools import fetch

    provider[1][:] = [
        {"href": "https://bing.com/aclick?u=ad"},
        {"title": "https://example.com/a.txt", "href": "https://example.com/a.txt"},
        {"title": "B", "href": "https://example.com/b.txt"},
    ]
    monkeypatch.setattr(
        fetch,
        "fetch_public_bytes",
        lambda url, timeout=None: (
            ("FIRST" if url.endswith("a.txt") else "SECOND").encode(),
            url,
            "text/plain",
        ),
    )
    first = fetch.search_and_fetch("カレー", "txt", max_results=2)
    body, has_more, token = parse_paged_result(first)
    assert "FIRST" in body and has_more and token
    second = fetch.search_and_fetch("カレー", "txt", continue_from=token)
    assert "SECOND" in parse_paged_result(second)[0]
    assert provider[0] == [
        ("カレー filetype:txt", {"max_results": 4, "region": "jp-jp", "backend": "auto"})
    ]

import httpx
import pytest

from retimer.realset.jimaku import USER_AGENT, Jimaku, JimakuError

KEY = "secret-key-123"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_entries_and_files_are_asked_with_the_key():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/entries/search"):
            return httpx.Response(200, json=[{"id": 7, "name": "Show"}])
        return httpx.Response(
            200, json=[{"name": "Show - 01.srt", "url": "https://x/f", "size": 3}]
        )

    jimaku = Jimaku(KEY, client=client_for(handler))
    assert jimaku.entries(anilist_id=42) == [{"id": 7, "name": "Show"}]
    assert jimaku.files(7)[0]["name"] == "Show - 01.srt"
    assert seen[0].url.params["anilist_id"] == "42"
    assert seen[1].url.path == "/api/entries/7/files"
    for request in seen:
        assert request.headers["authorization"] == KEY
        assert request.headers["user-agent"] == USER_AGENT


def test_a_download_goes_out_without_the_key(tmp_path):
    def handler(request):
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"1\n00:00:01,000 --> 00:00:02,000\nline\n")

    destination = tmp_path / "targets" / "file.srt"
    size = Jimaku(KEY, client=client_for(handler)).download(
        "https://x/entry/7/file.srt", destination
    )
    assert size == destination.stat().st_size > 0


def test_it_waits_as_long_as_the_site_asks():
    waited = []
    answers = [
        httpx.Response(429, headers={"x-ratelimit-reset-after": "2.5"}),
        httpx.Response(503),
        httpx.Response(200, json=[]),
    ]
    jimaku = Jimaku(KEY, client=client_for(lambda request: answers.pop(0)), sleep=waited.append)
    assert jimaku.files(7) == []
    assert waited == [2.5, 2.0]


def test_a_refusal_is_an_error_that_does_not_show_the_key():
    jimaku = Jimaku(
        KEY, client=client_for(lambda request: httpx.Response(401)), sleep=lambda s: None
    )
    with pytest.raises(JimakuError) as caught:
        jimaku.entries(query="show")
    assert "401" in str(caught.value) and KEY not in str(caught.value)

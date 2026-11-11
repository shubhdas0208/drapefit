import json

import httpx
import pytest
import respx

from drapefit import config
from drapefit.parser import ParseError, SarvamParser, clarification_context

from .conftest import item, spec


def sarvam_reply(content: str | None) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


@respx.mock
async def test_valid_json_becomes_spec(http):
    route = respx.post(config.SARVAM_URL).mock(return_value=sarvam_reply(json.dumps(spec(item()))))
    result = await SarvamParser(http, "k", "sarvam-105b-conversations").parse("grey turtleneck M under 2k")
    assert result.spec.items[0].query_terms == "turtleneck"
    assert result.attempts == 1
    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"] == {"type": "json_object"}
    assert '"required"' in sent["messages"][0]["content"]  # schema travels in the prompt
    assert "reasoning_effort" not in sent
    assert route.calls[0].request.headers["api-subscription-key"] == "k"


@respx.mock
async def test_reasoning_model_gets_low_effort(http):
    route = respx.post(config.SARVAM_URL).mock(return_value=sarvam_reply(json.dumps(spec(item()))))
    await SarvamParser(http, "k", "sarvam-105b").parse("x")
    assert json.loads(route.calls[0].request.content)["reasoning_effort"] == "low"


@respx.mock
@pytest.mark.parametrize(
    "bad",
    [
        sarvam_reply("not json"),
        sarvam_reply(None),
        sarvam_reply(json.dumps({"items": [{"query_terms": "x"}]})),  # schema-invalid
        httpx.Response(500, json={}),
    ],
)
async def test_retries_once_then_succeeds(http, bad):
    route = respx.post(config.SARVAM_URL).mock(side_effect=[bad, sarvam_reply(json.dumps(spec(item())))])
    result = await SarvamParser(http, "k", "m").parse("x")
    assert result.attempts == 2 and route.call_count == 2


@respx.mock
async def test_two_failures_raise_parse_error(http):
    respx.post(config.SARVAM_URL).mock(return_value=sarvam_reply("{broken"))
    with pytest.raises(ParseError):
        await SarvamParser(http, "k", "m").parse("x")


@respx.mock
async def test_timeout_counts_as_failed_attempt(http):
    respx.post(config.SARVAM_URL).mock(side_effect=[httpx.ReadTimeout("slow"), sarvam_reply(json.dumps(spec(item())))])
    result = await SarvamParser(http, "k", "m").parse("x")
    assert result.attempts == 2


@respx.mock
async def test_clarification_reply_carries_original_request(http):
    route = respx.post(config.SARVAM_URL).mock(return_value=sarvam_reply(json.dumps(spec(item(size="XS")))))
    ctx = clarification_context("grey turtleneck size 30", ["Chest 30 inches, or XS?"])
    await SarvamParser(http, "k", "m").parse("XS", ctx)
    user_msg = json.loads(route.calls[0].request.content)["messages"][1]["content"]
    assert "grey turtleneck size 30" in user_msg and "User reply: XS" in user_msg


def test_spec_validation_limits():
    from pydantic import ValidationError

    from drapefit.models import RequestSpec

    with pytest.raises(ValidationError):
        RequestSpec.model_validate(spec(item(max_price_inr=-5)))
    with pytest.raises(ValidationError):
        RequestSpec.model_validate(spec(*[item()] * 6))
    blank = RequestSpec.model_validate(spec(item(color="  ", size="")))
    assert blank.items[0].color is None and blank.items[0].size is None
    total = RequestSpec.model_validate(spec(item(max_price_inr=None), budget_type="total", total_max_inr=4000))
    assert total.item_cap(total.items[0]) == 4000
    assert not RequestSpec.model_validate(spec(item(category="not_fashion"))).items[0].in_scope


@respx.mock
async def test_non_json_http_body_is_retried(http):
    respx.post(config.SARVAM_URL).mock(side_effect=[httpx.Response(200, text="<html>gateway</html>"),
                                                    sarvam_reply(json.dumps(spec(item())))])
    assert (await SarvamParser(http, "k", "m").parse("x")).attempts == 2


@respx.mock
async def test_classify_returns_follow_up_with_context_in_prompt(http):
    from drapefit.parser import followup_context
    from drapefit.models import RequestSpec

    reply = {"action": "refine", "labels": [], "items": ["a"],
             "changes": {"color": "black", "size": None, "query_terms": None, "gender": None, "fit": None,
                         "max_price_inr": None, "cheaper": False}}
    route = respx.post(config.SARVAM_URL).mock(return_value=sarvam_reply(json.dumps(reply)))
    context = followup_context(RequestSpec.model_validate(spec(item())), {0: ["A1", "A2"]}, ["A1"])
    result = await SarvamParser(http, "k", "sarvam-105b-conversations").classify("same but black", context)
    assert result.follow_up.action == "refine" and result.follow_up.items == ["A"]
    assert result.follow_up.changes.color == "black"
    sent = json.loads(route.calls[0].request.content)
    assert "Items already shown:" in sent["messages"][1]["content"]
    assert "A: grey turtleneck, size M (options: A1, A2)" in sent["messages"][1]["content"]
    assert "Cart: A1" in sent["messages"][1]["content"]
    assert sent["max_tokens"] == 300


@respx.mock
async def test_classify_retries_then_fails(http):
    route = respx.post(config.SARVAM_URL).mock(return_value=sarvam_reply('{"action": "dance"}'))
    with pytest.raises(ParseError):
        await SarvamParser(http, "k", "m").classify("x", "ctx")
    assert route.call_count == 2

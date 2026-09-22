"""Tests for the Paack API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.paack.api import (
    PaackApiClient,
    PaackApiError,
)

CODE = "EXAMPLE123456"


def _html_session(status: int, body: str) -> MagicMock:
    """Return a text/html response without retaining any real carrier data."""
    response = AsyncMock()
    response.status = status
    response.headers = {}
    response.text = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


def _route_html(external_id: str = CODE) -> str:
    context = {"state": {"loaderData": {"routes/tracking.order": {"orderTrackData": {"external_id": external_id}, "activeEvent": {}, "eventList": []}}}}
    return "<script>window.__remixContext = " + json.dumps(context) + ";</script>"


async def test_get_parcel_extracts_only_order_loader_data():
    session = _html_session(200, _route_html())
    parcel = await PaackApiClient(session).async_get_parcel(CODE, "28012")
    assert parcel["orderTrackData"] == {"external_id": CODE}
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.kwargs["params"]["postal_code"] == "28012"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 404])
async def test_get_parcel_treats_redirect_or_404_as_not_found(status):
    assert await PaackApiClient(_html_session(status, "")).async_get_parcel(CODE, "28012") is None


async def test_get_parcel_rejects_missing_loader_data_but_allows_aliases():
    with pytest.raises(PaackApiError):
        await PaackApiClient(_html_session(200, "<html></html>")).async_get_parcel(CODE, "28012")
    parcel = await PaackApiClient(
        _html_session(200, _route_html("SYNTHETICOTHER"))
    ).async_get_parcel(CODE, "28012")
    assert parcel["orderTrackData"]["external_id"] == "SYNTHETICOTHER"


async def test_get_parcel_handles_rate_limit_and_http_failure():
    session = _html_session(429, "")
    session.get.return_value.__aenter__.return_value.headers = {"Retry-After": "12"}
    with pytest.raises(PaackApiError) as error:
        await PaackApiClient(session).async_get_parcel(CODE, "28012")
    assert error.value.status_code == 429
    assert error.value.retry_after == 12
    with pytest.raises(PaackApiError):
        await PaackApiClient(_html_session(500, "")).async_get_parcel(CODE, "28012")
    session = _html_session(429, "")
    session.get.return_value.__aenter__.return_value.headers = {"Retry-After": "tomorrow"}
    with pytest.raises(PaackApiError) as error:
        await PaackApiClient(session).async_get_parcel(CODE, "28012")
    assert error.value.retry_after is None


async def test_get_parcel_handles_semantic_miss_and_invalid_context():
    assert await PaackApiClient(_html_session(200, "Order not found")).async_get_parcel(CODE, "28012") is None
    with pytest.raises(PaackApiError):
        await PaackApiClient(_html_session(200, "window.__remixContext = []")).async_get_parcel(CODE, "28012")
    context = {"state": {"loaderData": {"routes/tracking.order": {"orderTrackData": None}}}}
    body = "window.__remixContext = " + json.dumps(context)
    assert await PaackApiClient(_html_session(200, body)).async_get_parcel(CODE, "28012") is None
    context["state"]["loaderData"]["routes/tracking.order"] = []
    with pytest.raises(PaackApiError):
        await PaackApiClient(
            _html_session(200, "window.__remixContext = " + json.dumps(context))
        ).async_get_parcel(CODE, "28012")


async def test_get_parcel_returns_parcel_on_success():
    context = {
        "state": {
            "loaderData": {
                "routes/tracking.order": {
                    "orderTrackData": {"external_id": CODE},
                }
            }
        }
    }
    session = _html_session(
        200, "window.__remixContext = " + json.dumps(context)
    )
    client = PaackApiClient(session)

    parcel = await client.async_get_parcel(CODE, "28012")

    assert parcel["orderTrackData"]["external_id"] == CODE
    assert session.get.call_args.kwargs["params"]["tracking_number"] == CODE


async def test_get_parcel_returns_none_when_not_found():
    """An unknown or not-yet-scanned pair is a normal state, not an error."""
    client = PaackApiClient(_html_session(200, "Incorrect order number"))
    assert await client.async_get_parcel("EXAMPLE000000", "28012") is None


async def test_get_parcel_returns_none_on_redirect_to_error_page():
    """The route answers an unknown code-plus-postcode pair with a redirect."""
    client = PaackApiClient(_html_session(302, ""))
    assert await client.async_get_parcel(CODE, "28012") is None


async def test_get_parcel_raises_on_error_status():
    client = PaackApiClient(_html_session(500, ""))
    with pytest.raises(PaackApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_when_route_data_is_missing():
    """A 200 that is neither a result page nor a known miss is a real failure."""
    client = PaackApiClient(_html_session(200, "<html>maintenance</html>"))
    with pytest.raises(PaackApiError):
        await client.async_get_parcel(CODE, "28012")


async def test_get_parcel_raises_on_unparseable_route_data():
    client = PaackApiClient(
        _html_session(200, "window.__remixContext = {not json")
    )
    with pytest.raises(PaackApiError):
        await client.async_get_parcel(CODE, "28012")


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = PaackApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)

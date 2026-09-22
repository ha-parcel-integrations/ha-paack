"""Tests for the Paack services (track_parcel / untrack_parcel)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.paack.const import (
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from custom_components.paack.services import _resolve_entry, async_setup_services

from .payloads import active_sample

_SAMPLE = active_sample()



async def _setup(hass, parcels: list[dict] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels or [], CONF_POSTAL_CODE: "28012"},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.paack.api.PaackApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_track_parcel_adds_to_options(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.paack.api.PaackApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE999999"},
            blocking=True,
        )
        await hass.async_block_till_done()

    parcels = entry.options[CONF_PARCELS]
    assert parcels == [{CONF_TRACKING_CODE: "EXAMPLE999999"}]


async def test_track_parcel_normalizes_code(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.paack.api.PaackApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "example-999 999"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "EXAMPLE999999"}
    ]


async def test_track_parcel_accepts_any_non_empty_code(hass):
    """A short/odd-shaped code is accepted — formats vary too much to gate on."""
    entry = await _setup(hass)
    with patch(
        "custom_components.paack.api.PaackApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: "abc"}, blocking=True
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: "ABC"}]


async def test_track_parcel_rejects_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: ""}, blocking=True
        )


async def test_track_parcel_duplicate_is_noop(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.paack.api.PaackApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        for _ in range(2):
            await hass.services.async_call(
                DOMAIN,
                "track_parcel",
                {CONF_TRACKING_CODE: "EXAMPLE999999"},
                blocking=True,
            )
            await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1


async def test_untrack_parcel_removes_from_options(hass):
    entry = await _setup(
        hass, parcels=[{CONF_TRACKING_CODE: "EXAMPLE999999"}]
    )
    with patch(
        "custom_components.paack.api.PaackApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE999999"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == []


async def test_untrack_unknown_code_is_noop(hass):
    entry = await _setup(
        hass, parcels=[{CONF_TRACKING_CODE: "EXAMPLE999999"}]
    )
    with patch(
        "custom_components.paack.api.PaackApiClient.async_get_parcel",
        new=AsyncMock(return_value=_SAMPLE),
    ):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE000000"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1


def test_resolve_entry_rejects_missing_or_ambiguous_hubs():
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []
    with pytest.raises(ServiceValidationError, match="not set up"):
        _resolve_entry(hass)
    first = MagicMock(options={CONF_POSTAL_CODE: "28012"})
    second = MagicMock(options={CONF_POSTAL_CODE: "08500"})
    hass.config_entries.async_entries.return_value = [first, second]
    assert _resolve_entry(hass, "28 012") is first
    with pytest.raises(ServiceValidationError, match="No Paack hub"):
        _resolve_entry(hass, "99999")
    with pytest.raises(ServiceValidationError, match="Multiple Paack hubs"):
        _resolve_entry(hass)


def test_service_registration_is_idempotent(hass):
    async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, "track_parcel")
    async_setup_services(hass)

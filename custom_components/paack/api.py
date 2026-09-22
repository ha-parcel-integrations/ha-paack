"""Paack public tracking API client.

This module is carrier-specific and implements the public route's behaviour. Keep the
*contract* the coordinator relies on:

* ``async_get_parcel`` returns the raw per-parcel dict on success,
* returns ``None`` when the carrier says the tracking code is unknown or not
  yet scanned (a normal, expected state — never an error),
* raises :class:`PaackApiError` for anything else, with
  ``status_code`` set on a non-2xx response and ``retry_after`` set when the
  carrier's own ``Retry-After`` header on a 429 could be parsed as seconds —
  the coordinator's backoff (Section 3 of the dynamic-polling plan) reads
  both,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from .const import TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)


class PaackApiError(Exception):
    """Raised when a Paack API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Paack API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class PaackApiClient:
    """Client for the public Paack tracking route.

    No authentication, but the lookup is keyed on the tracking code *and* the
    delivery postcode. There is no JSON API: the route answers HTTP 200 with a
    server-rendered Remix page whose ``window.__remixContext`` blob carries the
    order under ``state.loaderData["routes/tracking.order"]``. An unknown pair
    is answered with a redirect to an error page, so 3xx and 404 both mean
    not-found rather than a failure.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(
        self, tracking_code: str, postal_code: str = ""
    ) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the parcel dict for a known parcel, or ``None`` when the
        route reports the code as unknown — which is also what a
        not-yet-scanned parcel gets. Unreadable route data or any other
        non-2xx status raises :class:`PaackApiError`; network errors
        propagate as ``aiohttp.ClientError``.
        """
        async with self._session.get(
            TRACKING_API_URL,
            params={"tracking_number": tracking_code, "postal_code": postal_code},
            allow_redirects=False,
        ) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_header) if retry_after_header else None
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise PaackApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status in (301, 302, 303, 307, 308, 404):
                return None
            if response.status != 200:
                raise PaackApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            try:
                body = await response.text()
            except (ValueError, UnicodeDecodeError) as err:
                raise PaackApiError(f"unparseable body ({err})") from err
        marker = "window.__remixContext = "
        position = body.find(marker)
        if position < 0:
            if "Order not found" in body or "Incorrect order number" in body:
                return None
            raise PaackApiError("missing Remix route data")
        try:
            context = json.JSONDecoder().raw_decode(
                body[position + len(marker) :].lstrip()
            )[0]
            order = context["state"]["loaderData"]["routes/tracking.order"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as err:
            raise PaackApiError("invalid Remix route data") from err
        if not isinstance(order, dict):
            raise PaackApiError("invalid order route data")
        track = order.get("orderTrackData")
        if not isinstance(track, dict):
            return None
        return order

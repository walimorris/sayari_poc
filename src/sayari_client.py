"""
Thin client for the Sayari API, covering exactly the endpoints this project
uses: OAuth token, Project, Project Entity, and the Traversal-family (ownership,
watchlist) endpoints.

Docs:
  Auth:            https://documentation.sayari.com/api/key-concepts/authentication
  Rate limits:     https://documentation.sayari.com/api/key-concepts/rate-limits
  Create Project:  https://documentation.sayari.com/api/api-reference/project/create-project
  Create Proj Ent: https://documentation.sayari.com/api/api-reference/project-entity/create-project-entity
  Ownership:       https://documentation.sayari.com/api/api-reference/traversal/ownership
  Watchlist:       https://documentation.sayari.com/api/api-reference/traversal/watchlist
"""
import time
import logging
from typing import Any, Optional

import requests

from src.config import SAYARI_CLIENT_ID, SAYARI_CLIENT_SECRET, SAYARI_BASE_URL, SAYARI_TOKEN_URL
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Endpoints Sayari classifies as "advanced" tier (15 req / 10s). Everything else
# used here falls under "standard" (200 req / 60s).
# https://documentation.sayari.com/api/key-concepts/rate-limits
ADVANCED_TIER_PATH_PREFIXES = ("/v1/search", "/v1/traversal", "/v1/ubo", "/v1/downstream",
                               "/v1/watchlist", "/v1/shortest_path", "/v1/supply_chain/upstream")


class SayariAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str, payload: Any = None):
        super().__init__(f"Sayari API error {status_code}: {message}")
        self.status_code = status_code
        self.payload = payload


class SayariClient:
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self.client_id = client_id or SAYARI_CLIENT_ID
        self.client_secret = client_secret or SAYARI_CLIENT_SECRET
        if not self.client_id or not self.client_secret:
            raise ValueError("Sayari client_id/client_secret not configured (check .env)")
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._limiter = RateLimiter()
        self._session = requests.Session()

    def _ensure_token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expiry:
            return self._access_token
        resp = self._session.post(
            SAYARI_TOKEN_URL,
            headers={"accept": "application/json", "content-type": "application/json"},
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "audience": "sayari.com",
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise SayariAPIError(resp.status_code, "token request failed", resp.text)
        body = resp.json()
        self._access_token = body["access_token"]
        # Refresh a bit early (60s) rather than cutting it exactly at expiry.
        self._token_expiry = time.monotonic() + max(body.get("expires_in", 86400) - 60, 60)
        return self._access_token

    def _tier_for_path(self, path: str) -> str:
        return "advanced" if path.startswith(ADVANCED_TIER_PATH_PREFIXES) else "standard"

    def _request(self, method: str, path: str, *, params: dict = None, json_body: dict = None,
                 max_retries: int = 4, timeout_retries: int = 2) -> dict:
        url = f"{SAYARI_BASE_URL}{path}"
        tier = self._tier_for_path(path)
        request_timeout = 120 if tier == "advanced" else 60
        attempt = 0
        timeout_attempt = 0
        while True:
            attempt += 1
            self._limiter.wait(tier)
            token = self._ensure_token()
            try:
                resp = self._session.request(
                    method, url,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params, json=json_body, timeout=request_timeout,
                )
            except requests.exceptions.Timeout:
                timeout_attempt += 1
                if timeout_attempt > timeout_retries:
                    raise SayariAPIError(
                        408, f"request to {path} timed out after {timeout_retries} retries "
                             f"(likely a large/highly-connected entity; consider lowering max_depth/limit)"
                    )
                logger.warning("Timeout on %s (attempt %d/%d), retrying...", path, timeout_attempt, timeout_retries)
                continue
            except requests.exceptions.ConnectionError as e:
                if attempt > max_retries:
                    raise SayariAPIError(0, f"connection error to {path}: {e}")
                logger.warning("Connection error on %s (attempt %d), retrying...", path, attempt)
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 429:
                if attempt > max_retries:
                    raise SayariAPIError(429, "rate limit exceeded, retries exhausted", resp.text)
                retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning("429 from %s, backing off %.1fs (attempt %d)", path, retry_after, attempt)
                time.sleep(retry_after)
                continue
            if resp.status_code == 401 and attempt <= max_retries:
                # token may have been invalidated server-side; force refresh once
                self._access_token = None
                continue
            if resp.status_code >= 400:
                raise SayariAPIError(resp.status_code, resp.text[:500], resp.text)
            if not resp.content:
                return {}
            return resp.json()

    def create_project(self, label: str, project_type: str = "network") -> dict:
        return self._request("POST", "/v1/projects", json_body={"label": label, "type": project_type})

    def create_project_entity(self, project_id: str, *, name: str, address: str = None,
                               country: str = None, identifier: str = None,
                               limit: int = 10, profile: str = "corporate") -> dict:
        attributes: dict = {"name": [name]}
        if address:
            attributes["address"] = [address]
        if country:
            attributes["country"] = [country]
        if identifier:
            attributes["identifier"] = [identifier]
        return self._request(
            "POST", f"/v1/projects/{project_id}/entities/create",
            json_body={"attributes": attributes, "limit": limit, "profile": profile},
        )

    def get_project_entities(self, project_id: str, **params) -> dict:
        return self._request("GET", f"/v1/projects/{project_id}/entities", params=params)

    def ownership(self, entity_id: str, *, limit: int = 50, max_depth: int = 4, **params) -> dict:
        return self._request("GET", f"/v1/downstream/{entity_id}",
                              params={"limit": limit, "max_depth": max_depth, **params})

    def watchlist(self, entity_id: str, *, limit: int = 50, max_depth: int = 4, **params) -> dict:
        return self._request("GET", f"/v1/watchlist/{entity_id}",
                              params={"limit": limit, "max_depth": max_depth, **params})

    def traversal(self, entity_id: str, *, limit: int = 50, max_depth: int = 4, **params) -> dict:
        return self._request("GET", f"/v1/traversal/{entity_id}",
                              params={"limit": limit, "max_depth": max_depth, **params})

    def get_usage(self) -> dict:
        return self._request("GET", "/v1/usage")

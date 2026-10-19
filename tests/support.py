from __future__ import annotations

import json

from model_lifecycle_radar.fetch import FetchResponse


def json_catalog(*model_ids: str) -> bytes:
    return json.dumps({"models": [{"id": value} for value in model_ids]}).encode()


class MappingFetcher:
    def __init__(self, responses: dict[str, bytes | Exception]):
        self.responses = responses
        self.calls: list[str] = []

    def fetch(self, url: str, allowed_hosts: tuple[str, ...], **kwargs) -> FetchResponse:
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return FetchResponse(body=response, url=url, headers={})

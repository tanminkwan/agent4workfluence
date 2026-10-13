"""타겟 API HTTP 클라이언트: base URL, token, TLS, proxy (설계 15.5절)."""

from __future__ import annotations

import ssl

import httpx

from .request import HttpRequest


class TargetHttpClient:
    def __init__(self, *, base_url: str, token: str | None, client: httpx.Client):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("타겟 base URL은 http:// 또는 https://로 시작해야 한다")
        self._base = base_url.rstrip("/")
        self._token = token
        self._client = client

    @classmethod
    def from_settings(cls, target_cfg, secrets) -> "TargetHttpClient":
        """타겟 설정(TLS·시간 제한)과 타겟 비밀(주소·token)로 만든다."""
        verify: bool | ssl.SSLContext = target_cfg.require_bool("TLS_VERIFY")
        ca_file = target_cfg.optional("TLS_CA_FILE")
        if verify and ca_file:
            verify = ssl.create_default_context(cafile=ca_file)
        # proxy는 환경변수(HTTP_PROXY·HTTPS_PROXY·NO_PROXY)를 따른다
        client = httpx.Client(timeout=target_cfg.require_int("CALL_TIMEOUT_SECONDS"), verify=verify, trust_env=True)
        # 명세의 경로가 붙는 앞머리 (Workfluence는 /api/v1). 앞머리가 없는 타겟은 "/"
        prefix = target_cfg.require("TARGET_API_PATH_PREFIX").strip("/")
        base = secrets.require("TARGET_API_BASE_URL").rstrip("/")
        if prefix and not base.endswith("/" + prefix):
            base = f"{base}/{prefix}"
        return cls(base_url=base, token=secrets.require("TARGET_API_TOKEN"), client=client)

    @property
    def base_url(self) -> str:
        return self._base

    def send(self, req: HttpRequest) -> httpx.Response:
        headers = dict(req.headers)
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        kwargs = {"params": req.query or None, "headers": headers}
        if req.json_body is not None:
            kwargs["json"] = req.json_body
        return self._client.request(req.method, self._base + req.path, **kwargs)

    def __repr__(self) -> str:  # token을 드러내지 않는다
        return f"TargetHttpClient(base_url={self._base!r})"

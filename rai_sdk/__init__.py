"""RAI Control Plane Python SDK — stable /sdk/v1 client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

__version__ = "0.1.0"
API_RANGE = ">=1.0.0,<2.0.0"
_REDACTED = "***"


def _redact_secrets(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, _REDACTED)
    return text


class RAIError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.status_code = status_code
        super().__init__(f"{code}: {message}")


@dataclass
class Decision:
    decision: str
    decision_id: str | None = None
    reason_code: str | None = None
    human_message: str | None = None
    machine_reason: str | None = None
    approval_request_id: str | None = None
    enforcement_receipt_id: str | None = None
    retryable: bool = False
    raw: dict[str, Any] | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == "ALLOW"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Decision:
        approval = data.get("approval") or {}
        return cls(
            decision=data.get("decision", "DENY"),
            decision_id=data.get("decision_id"),
            reason_code=data.get("reason_code"),
            human_message=data.get("human_message"),
            machine_reason=data.get("machine_reason"),
            approval_request_id=data.get("approval_request_id")
            or approval.get("approval_request_id"),
            enforcement_receipt_id=data.get("enforcement_receipt_id"),
            retryable=bool(data.get("retryable")),
            raw=data,
        )

    def report_execution(
        self,
        client: "RAIClient",
        *,
        status: str,
        external_execution_id: str | None = None,
        result_metadata: dict[str, Any] | None = None,
        error_metadata: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        cost: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.decision_id:
            raise RAIError("INVALID_REQUEST", "Decision has no decision_id")
        return client.report_execution(
            self.decision_id,
            status=status,
            external_execution_id=external_execution_id,
            result_metadata=result_metadata,
            error_metadata=error_metadata,
            usage=usage,
            cost=cost,
        )


class Session:
    def __init__(self, client: "RAIClient", data: dict[str, Any]) -> None:
        self._client = client
        self.rai_session_id = data["rai_session_id"]
        self.trajectory_id = data.get("trajectory_id")
        self.correlation_id = data.get("correlation_id")
        self.raw = data

    def observe_input(
        self,
        content: str | None = None,
        *,
        content_type: str = "text",
        observed_model: str | None = None,
        principal: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._client._request(
            "POST",
            f"/sdk/v1/sessions/{self.rai_session_id}/input",
            json={
                "content": content,
                "content_type": content_type,
                "observed_model": observed_model,
                "principal": principal,
                "metadata": metadata or {},
            },
        )

    def authorize_action(
        self,
        *,
        tool: str | None = None,
        tool_id: str | None = None,
        target: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        amount: float | None = None,
        currency: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        approval_request_id: str | None = None,
    ) -> Decision:
        data = self._client._request(
            "POST",
            f"/sdk/v1/sessions/{self.rai_session_id}/actions/authorize",
            json={
                "tool": tool,
                "tool_id": tool_id,
                "target": target,
                "parameters": parameters or {},
                "amount": amount,
                "currency": currency,
                "idempotency_key": idempotency_key or str(uuid4()),
                "metadata": metadata or {},
                "approval_request_id": approval_request_id,
            },
            headers={"Idempotency-Key": idempotency_key}
            if idempotency_key
            else None,
        )
        return Decision.from_dict(data)

    def observe_output(
        self,
        content: str | None = None,
        *,
        content_type: str = "text",
        observed_model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._client._request(
            "POST",
            f"/sdk/v1/sessions/{self.rai_session_id}/output",
            json={
                "content": content,
                "content_type": content_type,
                "observed_model": observed_model,
                "metadata": metadata or {},
            },
        )

    def end(self) -> dict[str, Any]:
        return self._client._request(
            "POST", f"/sdk/v1/sessions/{self.rai_session_id}/end"
        )


class RAIClient:
    """Fail-closed SDK client for RAI Control Plane /sdk/v1."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str,
        integration_id: str,
        timeout: float = 30.0,
        fail_closed: bool = True,
        sdk_version: str = __version__,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.integration_id = integration_id
        self.timeout = timeout
        self.fail_closed = fail_closed
        self.sdk_version = sdk_version
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "X-RAI-Agent-Key": api_key,
                "User-Agent": f"rai-control-plane-sdk-python/{sdk_version}",
                "X-RAI-SDK-Version": sdk_version,
            },
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "RAIClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"RAIClient(base_url={self.base_url!r}, "
            f"integration_id={self.integration_id!r}, "
            f"timeout={self.timeout!r}, fail_closed={self.fail_closed!r})"
        )

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/sdk/v1/health", auth=False)

    def heartbeat(self, capability_matrix: dict[str, bool] | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/sdk/v1/heartbeat",
            json={
                "integration_id": self.integration_id,
                "sdk_version": self.sdk_version,
                "protocol_version": 1,
                "capability_matrix": capability_matrix,
            },
        )

    def start_session(
        self,
        *,
        external_session_id: str | None = None,
        external_conversation_id: str | None = None,
        principal: str | None = None,
        channel: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        data = self._request(
            "POST",
            "/sdk/v1/sessions",
            json={
                "integration_id": self.integration_id,
                "external_session_id": external_session_id,
                "external_conversation_id": external_conversation_id,
                "principal": principal,
                "channel": channel,
                "metadata": metadata or {},
            },
        )
        return Session(self, data)

    def report_execution(
        self,
        decision_id: str,
        *,
        status: str,
        external_execution_id: str | None = None,
        result_metadata: dict[str, Any] | None = None,
        error_metadata: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        cost: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/sdk/v1/actions/{decision_id}/execution",
            json={
                "status": status,
                "external_execution_id": external_execution_id,
                "result_metadata": result_metadata or {},
                "error_metadata": error_metadata,
                "usage": usage,
                "cost": cost,
            },
        )

    def get_decision(self, decision_id: str) -> Decision:
        return Decision.from_dict(
            self._request("GET", f"/sdk/v1/decisions/{decision_id}")
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        try:
            resp = self._http.request(method, path, json=json, headers=headers)
        except httpx.HTTPError as exc:
            if self.fail_closed:
                raise RAIError(
                    "RAI_UNAVAILABLE",
                    _redact_secrets(f"RAI unavailable: {exc}", self.api_key),
                    retryable=True,
                ) from exc
            raise

        if resp.status_code >= 400:
            detail: dict[str, Any]
            try:
                payload = resp.json()
                detail = payload.get("detail", payload)
                if not isinstance(detail, dict):
                    detail = {"message": str(detail)}
            except Exception:
                detail = {"message": resp.text}
            code = detail.get("error", "INVALID_REQUEST")
            message = _redact_secrets(
                str(detail.get("message", resp.reason_phrase)),
                self.api_key,
            )
            raise RAIError(
                str(code),
                message,
                retryable=bool(detail.get("retryable")),
                details=detail.get("details") or {},
                status_code=resp.status_code,
            )
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

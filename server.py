"""
Azure Billing MCP Server -- actual spend / cost analysis via the Azure Cost
Management Query API. Everything lives in this one file on purpose: it's a
single small service, not worth splitting into a package.

Auth model:
  - Who can CALL this server at all: Cloud Run IAM (--no-allow-unauthenticated
    + granting Gemini Enterprise's own service account roles/run.invoker).
    See SETUP.md. This file does not validate an inbound OAuth token.
  - Who this server queries AZURE as: one or more service principals, one per
    tenant, configured via AZURE_TENANT_ID / AZURE_CLIENT_ID /
    AZURE_CLIENT_SECRET (and optionally _2 suffixed variants for additional
    tenants). The bearer token Gemini Enterprise forwards carries a "tid"
    (tenant) claim; the server decodes it to pick the matching credential set.
    See SETUP.md and docs/CONFIGURATION.md.

Run locally:   python server.py
Run in Docker: see Dockerfile (CMD ["python", "server.py"])
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import logging
import os
import time
from contextvars import ContextVar
from typing import Any

import httpx
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from dotenv import load_dotenv
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

load_dotenv()  # no-op on Cloud Run (env vars injected directly, no .env file)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | %(name)s - %(message)s",
)
logger = logging.getLogger("azure-billing-mcp")

# --------------------------------------------------------------------------
# Bearer token capture + tenant routing
# --------------------------------------------------------------------------

current_token: ContextVar[str] = ContextVar("current_token", default="")
current_tenant: ContextVar[str] = ContextVar("current_tenant", default="")


def _decode_tenant(token: str) -> str:
    """Extract the 'tid' claim from a JWT without verifying the signature."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims.get("tid", "")
    except Exception:
        return ""


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Captures the incoming Bearer token and decodes its tenant for routing."""

    async def dispatch(self, request, call_next):
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        tenant = _decode_tenant(token) if token else ""
        ctx_token = current_token.set(token)
        ctx_tenant = current_tenant.set(tenant)
        if token:
            logger.info(
                "Request with bearer token (len=%d, tenant=%s)",
                len(token), tenant or "unknown",
            )
        try:
            return await call_next(request)
        finally:
            current_token.reset(ctx_token)
            current_tenant.reset(ctx_tenant)


# --------------------------------------------------------------------------
# Azure Cost Management client
# --------------------------------------------------------------------------

MANAGEMENT_ENDPOINT = "https://management.azure.com"
COST_MGMT_API_VERSION = "2023-11-01"
ARM_SCOPE = "https://management.azure.com/.default"

VALID_TIMEFRAMES = {
    "MonthToDate", "BillingMonthToDate", "TheLastMonth",
    "TheLastBillingMonth", "WeekToDate", "Custom",
}
VALID_GRANULARITIES = {"Daily", "Monthly", "None"}


class AzureCostClientError(RuntimeError):
    """Raised for auth failures or non-2xx responses from the Cost Management API."""


class AzureCostClient:
    def __init__(
        self,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        if tenant_id and client_id and client_secret:
            self._credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        else:
            # Fallback for local dev: picks up `az login` or env vars automatically
            self._credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._token: str | None = None
        self._token_expires_on: int = 0
        self._http = httpx.Client(timeout=60.0)

    def _get_token(self) -> str:
        now = _dt.datetime.now(_dt.timezone.utc).timestamp()
        if self._token and now < self._token_expires_on - 60:
            return self._token
        try:
            token = self._credential.get_token(ARM_SCOPE)
        except ClientAuthenticationError as exc:
            raise AzureCostClientError(
                "Failed to authenticate to Azure. Check credentials. "
                f"Underlying error: {exc}"
            ) from exc
        self._token = token.token
        self._token_expires_on = token.expires_on
        return self._token

    @staticmethod
    def build_scope(subscription_id: str, resource_group: str | None = None) -> str:
        scope = f"/subscriptions/{subscription_id}"
        if resource_group:
            scope += f"/resourceGroups/{resource_group}"
        return scope

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        params: dict | None = None,
        json_body: dict | None = None,
        max_retries: int = 2,
    ) -> httpx.Response:
        for attempt in range(max_retries + 1):
            if method == "POST":
                resp = self._http.post(url, headers=headers, params=params or {}, json=json_body)
            else:
                resp = self._http.get(url, headers=headers, params=params or {})
            if resp.status_code != 429 or attempt == max_retries:
                return resp
            wait = min(int(resp.headers.get("Retry-After", "15")), 60)
            logger.warning(
                "Azure API rate-limited (429) — waiting %ds before retry %d/%d",
                wait, attempt + 1, max_retries,
            )
            time.sleep(wait)
        raise AzureCostClientError("Exhausted retries after repeated 429 responses from Azure API")

    def query_costs(
        self,
        scope: str,
        timeframe: str,
        granularity: str = "None",
        group_by: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        cost_type: str = "ActualCost",
    ) -> dict[str, Any]:
        if timeframe not in VALID_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {sorted(VALID_TIMEFRAMES)}")
        if granularity not in VALID_GRANULARITIES:
            raise ValueError(f"granularity must be one of {sorted(VALID_GRANULARITIES)}")
        if timeframe == "Custom" and not (start_date and end_date):
            raise ValueError("start_date and end_date are required when timeframe='Custom'")

        dataset: dict[str, Any] = {
            "granularity": granularity,
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
        }
        if group_by:
            dataset["grouping"] = [{"type": "Dimension", "name": name} for name in group_by]

        body: dict[str, Any] = {"type": cost_type, "timeframe": timeframe, "dataset": dataset}
        if timeframe == "Custom":
            body["timePeriod"] = {
                "from": f"{start_date}T00:00:00+00:00",
                "to": f"{end_date}T23:59:59+00:00",
            }

        url = f"{MANAGEMENT_ENDPOINT}{scope}/providers/Microsoft.CostManagement/query"
        headers = {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}
        resp = self._request_with_retry(
            "POST", url, headers=headers,
            params={"api-version": COST_MGMT_API_VERSION}, json_body=body,
        )
        if not resp.is_success:
            raise AzureCostClientError(f"Cost Management API returned {resp.status_code}: {resp.text[:2000]}")
        return resp.json()

    def list_subscriptions(self) -> list[dict[str, str]]:
        url = f"{MANAGEMENT_ENDPOINT}/subscriptions"
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        resp = self._http.get(url, headers=headers, params={"api-version": "2022-12-01"})
        if not resp.is_success:
            raise AzureCostClientError(f"Failed to list subscriptions ({resp.status_code}): {resp.text[:2000]}")
        data = resp.json()
        return [
            {"subscriptionId": i["subscriptionId"], "displayName": i.get("displayName", ""), "state": i.get("state", "")}
            for i in data.get("value", [])
        ]

    def _get(self, url: str, params: dict | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        resp = self._request_with_retry("GET", url, headers=headers, params=params)
        if not resp.is_success:
            raise AzureCostClientError(f"Billing API returned {resp.status_code}: {resp.text[:2000]}")
        return resp.json()

    def get_billing_accounts(self) -> list[dict[str, Any]]:
        data = self._get(f"{MANAGEMENT_ENDPOINT}/providers/Microsoft.Billing/billingAccounts",
                         {"api-version": "2020-05-01"})
        return data.get("value", [])

    def get_billing_periods(self, subscription_id: str, top: int = 12) -> list[dict[str, Any]]:
        data = self._get(
            f"{MANAGEMENT_ENDPOINT}/subscriptions/{subscription_id}/providers/Microsoft.Billing/billingPeriods",
            {"api-version": "2018-03-01-preview", "$top": top},
        )
        return data.get("value", [])

    def get_invoices(self, billing_account_name: str, top: int = 12) -> list[dict[str, Any]]:
        data = self._get(
            f"{MANAGEMENT_ENDPOINT}/providers/Microsoft.Billing/billingAccounts/{billing_account_name}/invoices",
            {"api-version": "2020-05-01", "$top": top},
        )
        return data.get("value", [])

    def get_usage_details(self, subscription_id: str, start_date: str, end_date: str, top: int = 100) -> list[dict[str, Any]]:
        data = self._get(
            f"{MANAGEMENT_ENDPOINT}/subscriptions/{subscription_id}/providers/Microsoft.Consumption/usageDetails",
            {
                "api-version": "2023-05-01",
                "$filter": f"properties/usageStart ge '{start_date}' AND properties/usageEnd le '{end_date}'",
                "$top": top,
            },
        )
        return data.get("value", [])

    def get_budgets(self, subscription_id: str) -> list[dict[str, Any]]:
        data = self._get(
            f"{MANAGEMENT_ENDPOINT}/subscriptions/{subscription_id}/providers/Microsoft.Consumption/budgets",
            {"api-version": "2023-05-01"},
        )
        return data.get("value", [])

    def get_price_sheet(self, subscription_id: str) -> dict[str, Any]:
        return self._get(
            f"{MANAGEMENT_ENDPOINT}/subscriptions/{subscription_id}/providers/Microsoft.Consumption/pricesheets/default",
            {"api-version": "2023-05-01"},
        )


def _parse(raw: dict[str, Any]) -> list[dict[str, Any]]:
    props = raw.get("properties", raw)
    columns = [c["name"] for c in props.get("columns", [])]
    rows = props.get("rows", [])
    return [dict(zip(columns, row)) for row in rows]


def _round_costs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        if isinstance(row.get("Cost"), (int, float)):
            row["Cost"] = round(row["Cost"], 2)
    return rows


# --------------------------------------------------------------------------
# Per-tenant client registry
# Reads up to two credential sets from env vars (suffix "" and "_2").
# Keys are Azure tenant IDs so each request is routed to the right credentials.
# --------------------------------------------------------------------------

_clients: dict[str, AzureCostClient] = {}


def _register_tenant(suffix: str = "") -> None:
    t = os.environ.get(f"AZURE_TENANT_ID{suffix}")
    c = os.environ.get(f"AZURE_CLIENT_ID{suffix}")
    s = os.environ.get(f"AZURE_CLIENT_SECRET{suffix}")
    if t and c and s:
        _clients[t] = AzureCostClient(t, c, s)
        logger.info("Registered Azure credentials for tenant %s", t)


_register_tenant("")    # primary:   AZURE_TENANT_ID  / AZURE_CLIENT_ID  / AZURE_CLIENT_SECRET
_register_tenant("_2")  # secondary: AZURE_TENANT_ID_2 / AZURE_CLIENT_ID_2 / AZURE_CLIENT_SECRET_2

# Local dev fallback: if no env vars are set, use DefaultAzureCredential (az login)
_fallback_client: AzureCostClient | None = AzureCostClient() if not _clients else None
if _fallback_client:
    logger.warning("No explicit Azure credentials found — using DefaultAzureCredential (local dev only)")


def _get_client() -> AzureCostClient:
    """Return the AzureCostClient matching the current request's tenant, or fallback."""
    if _clients:
        tenant = current_tenant.get()
        return _clients.get(tenant) or next(iter(_clients.values()))
    assert _fallback_client is not None
    return _fallback_client


# --------------------------------------------------------------------------
# MCP server + tools
# --------------------------------------------------------------------------

mcp = FastMCP(
    name="Azure Billing",
    instructions=(
        "Tools for Azure Cost Management and Billing APIs (actual spend, invoices, budgets, usage). "
        "All tools are read-only. If the user doesn't specify a subscription, "
        "call list_subscriptions first. For invoice queries, call get_billing_accounts first."
    ),
)

_R = ToolAnnotations(readOnlyHint=True)


def _error(msg: str) -> dict:
    return {"error": msg}


@mcp.tool(annotations=_R)
def list_subscriptions() -> dict:
    """List Azure subscriptions the service principal can access."""
    logger.info("list_subscriptions called (tenant=%s)", current_tenant.get() or "unknown")
    try:
        return {"subscriptions": _get_client().list_subscriptions()}
    except AzureCostClientError as exc:
        return _error(str(exc))


@mcp.tool(annotations=_R)
def query_costs(
    subscription_id: str,
    resource_group: str | None = None,
    timeframe: str = "MonthToDate",
    granularity: str = "None",
    group_by: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    cost_type: str = "ActualCost",
) -> dict:
    """
    General-purpose Azure actual-cost query.

    timeframe: "MonthToDate", "BillingMonthToDate", "TheLastMonth",
        "TheLastBillingMonth", "WeekToDate", or "Custom".
    granularity: "None", "Daily", or "Monthly".
    group_by: dimension names, e.g. ["ServiceName"], ["ResourceGroupName"],
        ["ResourceId"], ["ResourceLocation"], ["MeterCategory"].
    start_date / end_date: required (YYYY-MM-DD) only when timeframe="Custom".
    """
    try:
        client = _get_client()
        scope = client.build_scope(subscription_id, resource_group)
        raw = client.query_costs(scope, timeframe, granularity, group_by, start_date, end_date, cost_type)
    except (AzureCostClientError, ValueError) as exc:
        return _error(str(exc))
    return {"rows": _round_costs(_parse(raw))}


@mcp.tool(annotations=_R)
def get_cost_by_service(
    subscription_id: str,
    resource_group: str | None = None,
    timeframe: str = "MonthToDate",
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Break down actual Azure spend by service for the given period."""
    try:
        client = _get_client()
        scope = client.build_scope(subscription_id, resource_group)
        raw = client.query_costs(scope, timeframe, "None", ["ServiceName"], start_date, end_date)
    except (AzureCostClientError, ValueError) as exc:
        return _error(str(exc))
    rows = _round_costs(_parse(raw))
    rows.sort(key=lambda r: r.get("Cost", 0), reverse=True)
    return {"rows": rows}


@mcp.tool(annotations=_R)
def get_cost_by_resource_group(
    subscription_id: str,
    timeframe: str = "MonthToDate",
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Break down actual Azure spend by resource group for the given period."""
    try:
        client = _get_client()
        scope = client.build_scope(subscription_id)
        raw = client.query_costs(scope, timeframe, "None", ["ResourceGroupName"], start_date, end_date)
    except (AzureCostClientError, ValueError) as exc:
        return _error(str(exc))
    rows = _round_costs(_parse(raw))
    rows.sort(key=lambda r: r.get("Cost", 0), reverse=True)
    return {"rows": rows}


@mcp.tool(annotations=_R)
def get_daily_cost_trend(
    subscription_id: str,
    resource_group: str | None = None,
    timeframe: str = "MonthToDate",
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Day-by-day actual cost time series for the given period."""
    try:
        client = _get_client()
        scope = client.build_scope(subscription_id, resource_group)
        raw = client.query_costs(scope, timeframe, "Daily", None, start_date, end_date)
    except (AzureCostClientError, ValueError) as exc:
        return _error(str(exc))
    rows = _round_costs(_parse(raw))
    rows.sort(key=lambda r: r.get("UsageDate", 0))
    return {"rows": rows}


@mcp.tool(annotations=_R)
def get_top_resources_by_cost(
    subscription_id: str,
    resource_group: str | None = None,
    timeframe: str = "MonthToDate",
    start_date: str | None = None,
    end_date: str | None = None,
    top_n: int = 10,
) -> dict:
    """Most expensive individual resources for the given period, highest first."""
    try:
        client = _get_client()
        scope = client.build_scope(subscription_id, resource_group)
        raw = client.query_costs(scope, timeframe, "None", ["ResourceId"], start_date, end_date)
    except (AzureCostClientError, ValueError) as exc:
        return _error(str(exc))
    rows = _round_costs(_parse(raw))
    rows.sort(key=lambda r: r.get("Cost", 0), reverse=True)
    return {"rows": rows[:top_n]}


# --------------------------------------------------------------------------
# Billing API tools
# --------------------------------------------------------------------------

@mcp.tool(annotations=_R)
def get_billing_accounts() -> dict:
    """List all Azure billing accounts accessible to the service principal."""
    logger.info("get_billing_accounts called (tenant=%s)", current_tenant.get() or "unknown")
    try:
        return {"billingAccounts": _get_client().get_billing_accounts()}
    except AzureCostClientError as exc:
        return _error(str(exc))


@mcp.tool(annotations=_R)
def get_billing_periods(
    subscription_id: str,
    top: int = 12,
) -> dict:
    """List recent billing periods for a subscription (most recent first)."""
    logger.info("get_billing_periods called for subscription %s", subscription_id)
    try:
        return {"billingPeriods": _get_client().get_billing_periods(subscription_id, top)}
    except AzureCostClientError as exc:
        return _error(str(exc))


@mcp.tool(annotations=_R)
def get_invoices(
    billing_account_name: str,
    top: int = 12,
) -> dict:
    """List invoices for a billing account. Call get_billing_accounts first to get the billing account name."""
    logger.info("get_invoices called for billing account %s", billing_account_name)
    try:
        return {"invoices": _get_client().get_invoices(billing_account_name, top)}
    except AzureCostClientError as exc:
        return _error(str(exc))


@mcp.tool(annotations=_R)
def get_usage_details(
    subscription_id: str,
    start_date: str,
    end_date: str,
    top: int = 100,
) -> dict:
    """
    Get detailed usage records for a subscription within a date range.
    start_date / end_date: required, format YYYY-MM-DD.
    top: max number of records to return (default 100).
    """
    logger.info("get_usage_details called for subscription %s (%s to %s)", subscription_id, start_date, end_date)
    try:
        return {"usageDetails": _get_client().get_usage_details(subscription_id, start_date, end_date, top)}
    except AzureCostClientError as exc:
        return _error(str(exc))


@mcp.tool(annotations=_R)
def get_budgets(subscription_id: str) -> dict:
    """List all cost budgets configured for a subscription, including current spend vs limit."""
    logger.info("get_budgets called for subscription %s", subscription_id)
    try:
        return {"budgets": _get_client().get_budgets(subscription_id)}
    except AzureCostClientError as exc:
        return _error(str(exc))


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

def create_app():
    """Build the FastMCP HTTP app with the bearer-token capture middleware."""
    return mcp.http_app(path="/mcp", middleware=[Middleware(BearerTokenMiddleware)])


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    logger.info("Starting Azure Billing MCP server on http://0.0.0.0:%d/mcp", port)
    uvicorn.run(create_app(), host="0.0.0.0", port=port, log_level=os.environ.get("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()

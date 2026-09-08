"""Synthetic local tools for the fictional support agent."""

from pydantic import BaseModel, Field


class LookupOrderInput(BaseModel):
    """Input for the synthetic order lookup tool."""

    order_id: str = Field(pattern=r"^DEMO-[0-9]+$")


class StorePolicyInput(BaseModel):
    """Input for the synthetic store policy tool."""

    topic: str = Field(min_length=1, max_length=100)


def lookup_order(order_id: str) -> dict[str, str]:
    """Return static fictional order data."""
    orders = {
        "DEMO-42": {"status": "shipped", "estimated_delivery": "2026-09-10"},
        "DEMO-77": {"status": "processing", "estimated_delivery": "2026-09-14"},
    }
    return {"order_id": order_id, **orders.get(order_id, {"status": "not_found"})}


def get_store_policy(topic: str) -> dict[str, str]:
    """Return a static fictional policy statement."""
    policies = {
        "returns": "Unused fictional items may be returned within 30 days.",
        "shipping": "Synthetic standard shipping takes three to five business days.",
    }
    return {"topic": topic, "policy": policies.get(topic.casefold(), "No demo policy found.")}

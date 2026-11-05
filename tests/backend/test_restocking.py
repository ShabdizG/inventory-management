"""
Tests for restocking order API endpoints.
"""
from datetime import datetime, timedelta

import pytest

from mock_data import restocking_orders


@pytest.fixture(autouse=True)
def clean_restocking_orders():
    """Restocking orders live in memory, so isolate each test."""
    restocking_orders.clear()
    yield
    restocking_orders.clear()


def forecast_by_sku(client, sku):
    return next(f for f in client.get("/api/demand").json() if f["item_sku"] == sku)


class TestRestockingEndpoints:
    """Test suite for restocking order endpoints."""

    def test_demand_forecasts_include_cost_and_lead_time(self, client):
        """Test that forecasts carry the fields restocking needs."""
        data = client.get("/api/demand").json()
        assert len(data) > 0

        for forecast in data:
            assert isinstance(forecast["unit_cost"], (int, float))
            assert forecast["unit_cost"] > 0
            assert isinstance(forecast["lead_time_days"], int)
            assert forecast["lead_time_days"] > 0

    def test_get_restocking_orders_empty(self, client):
        """Test that no orders exist before any are submitted."""
        response = client.get("/api/restocking-orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_restocking_order(self, client):
        """Test submitting an order uses server-side cost and lead time."""
        wdg = forecast_by_sku(client, "WDG-001")
        bearing = forecast_by_sku(client, "BRG-102")

        response = client.post("/api/restocking-orders", json={
            "items": [
                {"item_sku": "WDG-001", "quantity": 10},
                {"item_sku": "BRG-102", "quantity": 4},
            ]
        })
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"] == "RST-0001"
        assert order["status"] == "Submitted"
        assert len(order["items"]) == 2

        expected_total = 10 * wdg["unit_cost"] + 4 * bearing["unit_cost"]
        assert abs(order["total_value"] - expected_total) < 0.01

    def test_restocking_order_lead_time_is_longest_item(self, client):
        """Test that the order lead time and delivery date follow the slowest item."""
        wdg = forecast_by_sku(client, "WDG-001")
        bearing = forecast_by_sku(client, "BRG-102")
        assert wdg["lead_time_days"] != bearing["lead_time_days"]

        order = client.post("/api/restocking-orders", json={
            "items": [
                {"item_sku": "WDG-001", "quantity": 1},
                {"item_sku": "BRG-102", "quantity": 1},
            ]
        }).json()

        longest = max(wdg["lead_time_days"], bearing["lead_time_days"])
        assert order["lead_time_days"] == longest

        placed = datetime.fromisoformat(order["order_date"])
        expected = datetime.fromisoformat(order["expected_delivery"])
        assert expected - placed == timedelta(days=longest)

    def test_create_restocking_order_unknown_sku(self, client):
        """Test that an SKU outside the forecasts is rejected."""
        response = client.post("/api/restocking-orders", json={
            "items": [{"item_sku": "NOPE-999", "quantity": 1}]
        })
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
        assert client.get("/api/restocking-orders").json() == []

    @pytest.mark.parametrize("quantity", [0, -5])
    def test_create_restocking_order_invalid_quantity(self, client, quantity):
        """Test that non-positive quantities fail validation."""
        response = client.post("/api/restocking-orders", json={
            "items": [{"item_sku": "WDG-001", "quantity": quantity}]
        })
        assert response.status_code == 422

    def test_create_restocking_order_empty_items(self, client):
        """Test that an order with no items fails validation."""
        response = client.post("/api/restocking-orders", json={"items": []})
        assert response.status_code == 422

    def test_get_restocking_orders_newest_first(self, client):
        """Test that submitted orders are listed newest first."""
        for sku in ["WDG-001", "BRG-102"]:
            client.post("/api/restocking-orders", json={
                "items": [{"item_sku": sku, "quantity": 1}]
            })

        data = client.get("/api/restocking-orders").json()
        assert [o["order_number"] for o in data] == ["RST-0002", "RST-0001"]

    def test_restocking_orders_do_not_affect_sales_orders(self, client):
        """Test that restock spend is kept out of sales orders and dashboard totals."""
        before_orders = len(client.get("/api/orders").json())
        before_summary = client.get("/api/dashboard/summary").json()

        client.post("/api/restocking-orders", json={
            "items": [{"item_sku": "WDG-001", "quantity": 100}]
        })

        assert len(client.get("/api/orders").json()) == before_orders
        assert client.get("/api/dashboard/summary").json() == before_summary

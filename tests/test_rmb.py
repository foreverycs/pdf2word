"""Tests for RMB uppercase conversion and office catalog entry."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from office import AmountError, to_rmb_upper
from tools import TOOL_REGISTRY, tools_by_category


@pytest.mark.parametrize(
    "amount,expected",
    [
        ("0", "人民币零元整"),
        ("0.00", "人民币零元整"),
        ("0.01", "人民币零元零壹分"),
        ("0.05", "人民币零元零伍分"),
        ("0.10", "人民币零元壹角"),
        ("0.1", "人民币零元壹角"),
        ("1", "人民币壹元整"),
        ("10", "人民币壹拾元整"),
        ("11", "人民币壹拾壹元整"),
        ("20", "人民币贰拾元整"),
        ("100", "人民币壹佰元整"),
        ("101", "人民币壹佰零壹元整"),
        ("1001", "人民币壹仟零壹元整"),
        ("1101", "人民币壹仟壹佰零壹元整"),
        ("10000", "人民币壹万元整"),
        ("10001", "人民币壹万零壹元整"),
        ("100000000", "人民币壹亿元整"),
        ("1234.56", "人民币壹仟贰佰叁拾肆元伍角陆分"),
        ("1,234.56", "人民币壹仟贰佰叁拾肆元伍角陆分"),
        ("￥100", "人民币壹佰元整"),
        ("¥1 000.50", "人民币壹仟元伍角"),
    ],
)
def test_to_rmb_upper_cases(amount, expected):
    r = to_rmb_upper(amount)
    assert r["result"] == expected


def test_without_prefix():
    r = to_rmb_upper("12.3", prefix=False)
    assert r["result"] == "壹拾贰元叁角"
    assert r["prefix"] is False


def test_round_half_up():
    # 1.005 → 1.01 (half-up to fen)
    r = to_rmb_upper("1.005")
    assert r["amount"] == "1.01"
    assert r["result"] == "人民币壹元零壹分"


def test_negative_rejected():
    with pytest.raises(AmountError):
        to_rmb_upper("-1")
    with pytest.raises(AmountError):
        to_rmb_upper(-3.5)


def test_invalid_rejected():
    with pytest.raises(AmountError):
        to_rmb_upper("")
    with pytest.raises(AmountError):
        to_rmb_upper("abc")
    with pytest.raises(AmountError):
        to_rmb_upper("1.2.3")


def test_int_and_decimal_input():
    assert to_rmb_upper(100)["result"] == "人民币壹佰元整"
    from decimal import Decimal

    assert to_rmb_upper(Decimal("2.5"))["result"] == "人民币贰元伍角"


def test_registry_has_office_and_rmb():
    cats = tools_by_category()
    ids = {c["id"] for c in cats}
    assert "office" in ids
    slugs = {t["slug"] for t in TOOL_REGISTRY}
    assert "rmb" in slugs
    rmb = next(t for t in TOOL_REGISTRY if t["slug"] == "rmb")
    assert rmb["category"] == "office"
    assert rmb["route"] == "/tools/rmb"


def test_rmb_page_and_convert_api():
    from app import app

    client = TestClient(app)
    page = client.get("/tools/rmb")
    assert page.status_code == 200
    assert "人民币" in page.text

    r = client.post(
        "/tools/rmb/convert",
        data={"amount": "1234.56", "prefix": "true"},
    )
    assert r.status_code == 200
    assert r.json()["result"] == "人民币壹仟贰佰叁拾肆元伍角陆分"

    r2 = client.post(
        "/tools/rmb/convert",
        data={"amount": "10", "prefix": "false"},
    )
    assert r2.status_code == 200
    assert r2.json()["result"] == "壹拾元整"

    bad = client.post("/tools/rmb/convert", data={"amount": "nope"})
    assert bad.status_code == 400


def test_office_category_and_alias():
    from app import app

    client = TestClient(app)
    office = client.get("/c/office")
    assert office.status_code == 200
    assert "办公工具" in office.text
    assert "/tools/rmb" in office.text

    alias = client.get("/office", follow_redirects=False)
    assert alias.status_code in (307, 302)
    assert alias.headers["location"] == "/c/office"

    home = client.get("/")
    assert home.status_code == 200
    assert "办公工具" in home.text

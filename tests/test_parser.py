import pytest
from decimal import Decimal
from app.crawler.parser import parse_orders, pagination_offsets, SourceError
from tests.helpers import html

def test_groups_multi_item_without_summing_and_reordered_headers():
    rows = [("7", "90071992547409930", "SPFY3", "173.09")] * 2
    orders = parse_orders(html(rows, headers=["Grand Total", "", "Order ID", "Shopify Store", "#"]))
    assert len(orders) == 1
    assert orders[0].amount == Decimal("173.09")
    assert orders[0].shopify_order_id == "90071992547409930"
    assert orders[0].store_name == "SPFY3 name"
    assert orders[0].display_number == "ORDER-90071992547409930"

def test_conflicting_duplicate_fails():
    with pytest.raises(SourceError):
        parse_orders(html([("1","2","S","10"),("1","2","S","11")]))

@pytest.mark.parametrize("amount", ["", "NA", "NaN", "1,23", "-1", "$10", "Infinity"])
def test_invalid_money_fails_without_marking_seen(amount):
    with pytest.raises(SourceError):
        parse_orders(html([("1","2","S",amount)]))

def test_empty_valid_table_and_invalid_login():
    assert parse_orders(html()) == []
    with pytest.raises(SourceError):
        parse_orders('<input id="Username">')

def test_missing_identity_fails():
    with pytest.raises(SourceError):
        parse_orders(html([("1","","S","10")]))

def test_purchase_date_is_normalized_for_scope_expansion():
    source = html([("1","2","S","10")]).replace("<td>Grand Total</td>", "<td>Grand Total</td><td>Purchase</td>").replace("<td>10</td>", "<td>10</td><td>26-09-2026</td>")
    assert parse_orders(source)[0].purchase_date == "2026-09-26"

def test_pagination_reads_onclick_and_href_no_fixed_page_size():
    paging = '<a onclick="pagingSubmit_0(\'?page=manager_order_shopify&amp;offset=100\')">2</a><a href="?page=manager_order_shopify&amp;offset=200">3</a>'
    assert pagination_offsets(html(paging=paging)) == {100, 200}

def test_unknown_pagination_fails_closed():
    with pytest.raises(SourceError):
        pagination_offsets(html(paging='<a onclick="unknown()">2</a>'))

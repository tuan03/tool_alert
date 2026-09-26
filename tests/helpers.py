from html import escape

def html(rows=(), paging='<span>[1]</span>', headers=None):
    headers = headers or ["#", "", "Shopify Store", "Order ID", "Grand Total"]
    body = ""
    for internal, order, shop, amount in rows:
        values = {"#": "1", "": f'<input class="shopify-order-check" value="{internal}" data-order-number="{order}" data-shop-code="{shop}">',
                  "Shopify Store": f'<a>{escape(shop)} name</a><br>{escape(shop)}',
                  "Order ID": f'<a>ORDER-{order}<br>{order}</a>', "Grand Total": amount}
        body += "<tr>" + "".join(f"<td>{values[h]}</td>" for h in headers) + "</tr>"
    return '<form id="shopify-order-search-form"></form><table><tr id="header_order_shopify">' + "".join(f"<td>{h}</td>" for h in headers) + "</tr>" + body + '</table><div id="pagenav_bar">' + paging + "</div>"

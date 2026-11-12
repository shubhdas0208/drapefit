"""Checkout handoff (D50): one permalink per merchant, stock and price re-checked."""
import dataclasses

from drapefit import pipeline, render
from drapefit.catalog import CatalogError
from drapefit.checkout import CartLine, build_checkouts, permalink, storefront_host, variant_number

from .conftest import FakeCatalog, load_search


def sized_option(product, label, size="M", item_index=0):
    variant = dict(product["variants"][0])
    variant["id"] = f"{variant['id']}-{size}"
    variant["options"] = [o for o in variant.get("options", []) if o.get("name") != "Size"] + [
        {"name": "Size", "label": size}
    ]
    return pipeline.make_option(label, item_index, product, variant)


def products_from_two_sellers():
    products = load_search("01-grey-turtleneck-men")
    by_seller = {}
    for p in products:
        host = storefront_host(pipeline.make_option("A1", 0, p, p["variants"][0]))
        if host:
            by_seller.setdefault(host, []).append(p)
    hosts = [h for h, ps in by_seller.items()]
    many = next(h for h in hosts if len(by_seller[h]) >= 2)
    other = next(h for h in hosts if h != many)
    return products, by_seller[many][:2], by_seller[other][:1]


def test_variant_number_and_permalink():
    assert variant_number("gid://shopify/ProductVariant/43297406484615") == "43297406484615"
    assert variant_number("gid://shopify/ProductVariant/43297406484615-M") is None
    assert variant_number("") is None


async def test_one_permalink_per_merchant_with_quantities():
    products, same_seller, other_seller = products_from_two_sellers()
    catalog = FakeCatalog(products)
    # real numeric variant ids so permalinks can be built
    a1 = dataclasses.replace(sized_option(same_seller[0], "A1"), variant_id="gid://shopify/ProductVariant/111")
    a2 = dataclasses.replace(sized_option(same_seller[1], "A2"), variant_id="gid://shopify/ProductVariant/222")
    b1 = dataclasses.replace(sized_option(other_seller[0], "B1", item_index=1),
                             variant_id="gid://shopify/ProductVariant/333")

    async def get_product(product_id, selected=None, fresh=False):  # fresh API data matching the stored options
        option = next(o for o in (a1, a2, b1) if o.product_id == product_id)
        return {"variants": [{"id": option.variant_id, "availability": {"available": True},
                              "price": {"amount": option.price_paise, "currency": "INR"}}]}

    catalog.get_product = get_product
    lines = [CartLine(1, a1, 1), CartLine(2, b1, 1), CartLine(3, a2, 2)]
    checkouts, unlinkable = await build_checkouts(catalog, lines)
    assert unlinkable == []
    assert len(checkouts) == 2
    first = checkouts[0]
    assert first.permalink == f"https://{first.host}/cart/111:1,222:2"
    assert first.total_paise == a1.price_paise + 2 * a2.price_paise
    assert checkouts[1].permalink.endswith("/cart/333:1")


async def test_price_change_is_flagged_with_fresh_api_price_and_sold_out_dropped():
    products, same_seller, _ = products_from_two_sellers()
    a1 = dataclasses.replace(sized_option(same_seller[0], "A1"), variant_id="gid://shopify/ProductVariant/111")
    a2 = dataclasses.replace(sized_option(same_seller[1], "A2"), variant_id="gid://shopify/ProductVariant/222")
    live = {a1.product_id: (True, a1.price_paise + 10000), a2.product_id: (False, a2.price_paise)}

    async def get_product(product_id, selected=None, fresh=False):
        available, price = live[product_id]
        option = a1 if product_id == a1.product_id else a2
        return {"variants": [{"id": option.variant_id, "availability": {"available": available},
                              "price": {"amount": price, "currency": "INR"}}]}

    catalog = FakeCatalog(products)
    catalog.get_product = get_product
    [checkout], _ = await build_checkouts(catalog, [CartLine(1, a1, 1), CartLine(2, a2, 1)])
    statuses = {c.line.option.label: c.status for c in checkout.lines}
    assert statuses == {"A1": "price_changed", "A2": "sold_out"}
    assert checkout.permalink.endswith("/cart/111:1")  # sold-out line left out of the link
    assert checkout.total_paise == a1.price_paise + 10000
    text = render.checkout_text(checkout)
    assert render.inr(a1.price_paise + 10000) in text and f"price changed from {render.inr(a1.price_paise)}" in text
    assert "<s>A2" in text and "sold out" in text


async def test_catalog_error_keeps_line_unchecked_at_shown_price():
    products, same_seller, _ = products_from_two_sellers()
    a1 = dataclasses.replace(sized_option(same_seller[0], "A1"), variant_id="gid://shopify/ProductVariant/111")

    async def get_product(product_id, selected=None, fresh=False):
        raise CatalogError("get_product: HTTP 503")

    catalog = FakeCatalog(products)
    catalog.get_product = get_product
    [checkout], _ = await build_checkouts(catalog, [CartLine(1, a1, 1)])
    assert checkout.lines[0].status == "unchecked" and checkout.total_paise == a1.price_paise
    assert checkout.permalink.endswith("/cart/111:1")


async def test_all_sold_out_means_no_link():
    products, same_seller, _ = products_from_two_sellers()
    a1 = dataclasses.replace(sized_option(same_seller[0], "A1"), variant_id="gid://shopify/ProductVariant/111")

    async def get_product(product_id, selected=None, fresh=False):
        return {"variants": []}

    catalog = FakeCatalog(products)
    catalog.get_product = get_product
    [checkout], _ = await build_checkouts(catalog, [CartLine(1, a1, 1)])
    assert checkout.permalink is None and "Nothing left to buy" in render.checkout_text(checkout)


async def test_unsafe_host_is_not_linked():
    products, same_seller, _ = products_from_two_sellers()
    bad = dataclasses.replace(sized_option(same_seller[0], "A1"), product_url="http://evil.example/x",
                              variant_id="gid://shopify/ProductVariant/111")
    checkouts, unlinkable = await build_checkouts(FakeCatalog(products), [CartLine(1, bad, 1)])
    assert checkouts == [] and [l.option.label for l in unlinkable] == ["A1"]


def test_permalink_skips_non_numeric_ids():
    assert permalink("x.com", ()) is None


async def test_unchecked_line_is_flagged_to_the_user():
    products, same_seller, _ = products_from_two_sellers()
    a1 = dataclasses.replace(sized_option(same_seller[0], "A1"), variant_id="gid://shopify/ProductVariant/111")

    async def get_product(product_id, selected=None, fresh=False):
        raise CatalogError("down")

    catalog = FakeCatalog(products)
    catalog.get_product = get_product
    [checkout], _ = await build_checkouts(catalog, [CartLine(1, a1, 1)])
    assert "couldn't re-check stock and price" in render.checkout_text(checkout)


def test_options_carry_seller_policy_links_from_the_api():
    from .conftest import load_fixture

    product = load_fixture("get_product-01-grey-turtleneck-men-selected.json")["result"]["structuredContent"]["product"]
    option = pipeline.make_option("A1", 0, product, product["variants"][0])
    assert option.shipping_policy_url.startswith("https://checkout.shopify.com/")
    assert option.refund_policy_url.startswith("https://checkout.shopify.com/")
    assert "shipping policy</a>" in render.policy_links(option) and "returns</a>" in render.policy_links(option)


def test_old_stored_options_without_policy_fields_still_load():
    from drapefit.models import Option

    products, same_seller, _ = products_from_two_sellers()
    stored = dataclasses.asdict(sized_option(same_seller[0], "A1"))
    stored.pop("shipping_policy_url"), stored.pop("refund_policy_url")
    assert Option(**stored).shipping_policy_url is None


def test_checkout_footer_explains_where_delivery_details_come_from():
    text = render.checkout_footer(2)
    assert "delivery date and shipping cost" in text and "never see your address" in text

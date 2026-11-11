from drapefit import config, matching, pipeline
from drapefit.models import ItemSpec

from .conftest import FakeCatalog, item, load_search


def spec_item(**kw) -> ItemSpec:
    return ItemSpec.model_validate(item(**kw))


async def test_grey_turtleneck_m_returns_grounded_sized_options():
    catalog = FakeCatalog(load_search("01-grey-turtleneck-men"))
    result = await pipeline.run_item(catalog, 0, spec_item(), 2000)
    assert 1 <= len(result.options) <= config.OPTIONS_PER_ITEM
    assert [o.label for o in result.options] == [f"A{i + 1}" for i in range(len(result.options))]
    for option in result.options:
        assert option.currency == "INR"
        assert option.price_paise <= 2000 * 100
        assert option.available
        assert "A:M" in matching.size_tokens(option.size_label)


async def test_search_query_and_filters_are_built_from_spec():
    catalog = FakeCatalog(load_search("01-grey-turtleneck-men"))
    await pipeline.run_item(catalog, 0, spec_item(fit="slim"), 2000)
    assert catalog.search_calls == [("grey slim turtleneck men", 2000, "men")]


async def test_inr_cap_is_exact_in_paise():
    variant = {"availability": {"available": True}, "price": {"amount": 200000, "currency": "INR"}}
    assert pipeline.variant_passes(variant, 2000)
    over = {**variant, "price": {"amount": 200001, "currency": "INR"}}
    assert not pipeline.variant_passes(over, 2000)
    usd = {**variant, "price": {"amount": 1500, "currency": "USD"}}
    assert not pipeline.variant_passes(usd, 2000)
    sold_out = {**variant, "availability": {"available": False}}
    assert not pipeline.variant_passes(sold_out, 2000)
    assert pipeline.variant_passes(variant, None)


async def test_seller_cap_keeps_at_most_three_per_seller_in_order():
    products = load_search("01-grey-turtleneck-men")
    capped = pipeline.cap_per_seller(products)
    counts: dict[str, int] = {}
    for p in capped:
        counts[pipeline.seller_key(p)] = counts.get(pipeline.seller_key(p), 0) + 1
    assert max(counts.values()) <= config.MAX_PER_SELLER
    assert capped == [p for p in products if p in capped]  # relevance order preserved


async def test_colour_filter_drops_other_colours():
    products = load_search("01-grey-turtleneck-men")
    it = spec_item()
    kept = [p for p in products if pipeline.hit_passes(p, it, 2000)]
    titles = " ".join(p["title"].lower() for p in kept)
    assert kept and "wine" not in titles and "coffee" not in titles


async def test_sold_out_size_is_skipped_and_never_shown():
    products = load_search("01-grey-turtleneck-men")
    first = next(p for p in products if pipeline.hit_passes(p, spec_item(), 2000))
    catalog = FakeCatalog(products, sold_out={(first["id"], "M")})
    result = await pipeline.run_item(catalog, 0, spec_item(), 2000)
    assert first["id"] not in {o.product_id for o in result.options}


async def test_detail_calls_are_capped_per_item():
    products = load_search("01-grey-turtleneck-men")
    every = {(p["id"], "M") for p in products}
    catalog = FakeCatalog(products, sold_out=every)
    result = await pipeline.run_item(catalog, 0, spec_item(), 2000)
    assert result.options == ()
    assert len(catalog.detail_calls) <= config.MAX_DETAIL_CALLS_PER_ITEM


async def test_no_size_requested_uses_search_variant_without_detail_calls():
    catalog = FakeCatalog(load_search("16-mustard-saree-women"))
    it = spec_item(query_terms="saree", category="ethnic", color="mustard", size=None, gender="women",
                   size_system="unknown", max_price_inr=3000)
    result = await pipeline.run_item(catalog, 0, it, 3000)
    assert len(result.options) == config.OPTIONS_PER_ITEM
    assert catalog.detail_calls == []


async def test_waist_size_on_chinos():
    catalog = FakeCatalog(load_search("05-navy-chinos-men"))
    it = spec_item(query_terms="chinos", category="bottom", color="navy", size="32", size_system="waist_in")
    result = await pipeline.run_item(catalog, 1, it, 2000)
    assert result.options and all(o.label.startswith("B") for o in result.options)
    assert all(o.size_label == "32" or "32" in o.size_label for o in result.options)


async def test_product_without_size_options_reads_matrix_first():
    product = load_search("01-grey-turtleneck-men")[0]
    no_sizes = {**product, "options": [o for o in product["options"] if o["name"] != "Size"]}
    detail = {**product}  # get_product(plain) returns the size matrix

    class Cat(FakeCatalog):
        async def get_product(self, product_id, selected=None):
            if not selected:
                self.detail_calls.append((product_id, None))
                return detail
            return await super().get_product(product_id, selected)

    catalog = Cat([no_sizes])
    catalog.by_id[product["id"]] = product
    it = spec_item()
    assert pipeline.hit_passes(no_sizes, it, 2000)
    result = await pipeline.run_item(catalog, 0, it, 2000)
    assert catalog.detail_calls[0] == (product["id"], None)
    assert len(result.options) == 1


async def test_wrong_variant_returned_for_selection_is_rejected():
    products = load_search("01-grey-turtleneck-men")

    class Liar(FakeCatalog):
        async def get_product(self, product_id, selected=None):
            prod = await super().get_product(product_id, selected)
            prod["variants"][0]["options"] = [{"name": "Size", "label": "S"}]  # store ignored our selection
            return prod

    result = await pipeline.run_item(Liar(products), 0, spec_item(), 2000)
    assert result.options == ()


async def test_events_report_funnel_counts_and_unmapped_sizes():
    products = load_search("01-grey-turtleneck-men")
    product = next(p for p in products if pipeline.hit_passes(p, spec_item(), 2000))
    weird = {**product, "options": [{"name": "Size", "values": [{"label": "Regular"}, {"label": "Tall"}]}]}
    events = []

    async def sink(type_, payload):
        events.append((type_, payload))

    await pipeline.run_item(FakeCatalog([weird]), 0, spec_item(), 2000, on_event=sink)
    types = [t for t, _ in events]
    assert "size_unmapped" in types and "item_searched" in types
    searched = dict(events)["item_searched"]
    assert searched["hits"] == 1 and searched["shown"] == 0


async def test_recorded_get_product_fixture_matches_pipeline_assumptions():
    from .conftest import load_fixture

    plain = load_fixture("get_product-01-grey-turtleneck-men.json")["result"]["structuredContent"]["product"]
    assert pipeline._available_size_labels(plain) == ["S", "M", "L", "XL", "2XL"]
    selected = load_fixture("get_product-01-grey-turtleneck-men-selected.json")["result"]["structuredContent"]["product"]
    variant = selected["variants"][0]
    assert pipeline._option_label(variant, "size") == "M"
    assert pipeline.variant_passes(variant, 2000)


async def test_selected_variant_in_wrong_colour_is_rejected():
    products = load_search("01-grey-turtleneck-men")

    class WrongColour(FakeCatalog):
        async def get_product(self, product_id, selected=None):
            prod = await super().get_product(product_id, selected)
            prod["title"] = "Turtleneck"
            prod["variants"][0]["options"] = [{"name": "Color", "label": "Wine"}] + [
                o for o in prod["variants"][0]["options"] if o["name"] == "Size"
            ]
            return prod

    result = await pipeline.run_item(WrongColour(products), 0, spec_item(), 2000)
    assert result.options == ()

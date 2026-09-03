"""
The LangChain agent
===================

Hybrid design — the LLM does judgement, code does the fetching and the numbers:

    URL -> [code] route  -> [tool] render or run actor -> deterministic fields
                                                       -> raw evidence
        -> [LLM] read the evidence, fill the fuzzy fields, emit ProductDraft
        -> [code] overlay the deterministic fields -> ProductData

Why not let the model do everything? Because a language model transcribing
prices, image URLs and spec tables is slow, expensive and occasionally
creative. And why not skip the model? Because variants, seller blocks,
shipping terms and promotions live in free text that no parser generalises
over. Each side does what it is good at.

The agent owns three tools and decides which to use:
    inspect_url        — what strategy does this URL need? (free, no network)
    fetch_product_page — Playwright render + deterministic parse
    fetch_via_apify    — run the routed Apify actor

so a blocked page can be retried through Apify inside the same reasoning loop.
"""

import json
from typing import Any

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool

from .config import (
    AGENT_MAX_STEPS,
    ANTHROPIC_MAX_TOKENS,
    ANTHROPIC_MODEL,
    INCLUDE_VARIANTS,
    TARGET_COUNTRY,
    ExtractionError,
    require_anthropic_key,
    summarize_usage,
)
from .normalization import (
    build_product,
    draft_to_fields,
    merge_partials,
    overlay_reliable,
    summarize,
)
from .pipeline import gather_source, to_product
from .routing import detect_route, supported_routes
from .schema import ProductData, ProductDraft, ProductSummary
from .sources import SourceResult

SYSTEM_PROMPT = """You extract structured product data from e-commerce pages (G.A.M.E. framework).

G — GOAL: Return ONE standardized product record for the URL the user gives you,
    filled in as completely as the evidence allows. The caller finally receives
    only name, description, category and image_url — so `title`, `category` and
    `short_description` are the fields that must be right above all.

A — ACTIONS:
    1. Call inspect_url(url) to learn which strategy the URL needs.
    2. Call the tool it recommends: fetch_product_page (Playwright) for normal
       sites, fetch_via_apify for marketplaces that block scraping.
    3. If a tool reports a block, a CAPTCHA, or obviously empty data, try the
       other tool ONCE before giving up.
    4. Read `reliable_fields` (already parsed, trustworthy) and `evidence`
       (page text or the actor's raw JSON), then produce the structured answer.
       Work through `evidence` line by line — it is the only place the fields
       below appear, and it is easy to stop reading too early:
{variants_rule}         - delivery and returns wording ("Free Standard Delivery over $75",
           "30-day returns", "Ships from China") -> `shipping`
         - discount/coupon/deal banners -> `promotions`
         - store or seller name, ratings of the seller -> `seller`
       reliable_fields NEVER contains shipping or promotions: if the evidence
       mentions them and you leave them empty, the answer is wrong.

M — MEMORY: One URL per conversation. Never reuse data from another product.

E — ENVIRONMENT: Rules for the answer.
    * Copy values; never invent them. If the page does not state something,
      leave it null/empty — an empty field is correct, a guess is a bug.
    * title: the page's title exactly as printed. Do not clean it up.
    * name: YOU extract this from the title — the PRODUCT NAME alone. Cut the
      store/site name and everything after the separator (| - – — ::), the SEO
      and marketing tails ("Buy online", "Free Shipping", "Official Store",
      "-20%"), and trailing option or reference qualifiers ("- Black, 3XL",
      "(Pack of 2)", "ref. A2A1K"). Keep 1-8 words that genuinely name the
      product. Drop the call to action and the delivery/payment claims shops
      glue onto the title, in ANY language ("Get", "Buy now", "Commandez
      maintenant", "اطلب الآن", "free delivery", "التوصيل بالمجان",
      "الدفع عند الاستلام"). Examples:
        "BMotivated | Gym Wear - Free shipping"        -> "BMotivated"
        "Arrival T-Shirt - Black | Gymshark"           -> "Arrival T-Shirt"
        "Echo Dot (5th Gen, 2022) - Smart speaker…"    -> "Echo Dot"
        "اطلب الآن Get BMotivated التوصيل بالمجان"      -> "BMotivated"
      Keep the product's own words though: "New Balance 574" and "NOW Foods
      Vitamin C" start with their real names, not with a call to action.
      Never return an empty name when a title exists.
    * description: the product's own description, not navigation text or
      reviews. Trim boilerplate.
    * short_description: YOU write this — one paragraph of 2-4 sentences
      (~70 words max) that tells someone who cannot see the page what this
      product is: what it is and its brand, its price with the currency, the
      two or three characteristics that actually matter (material, capacity,
      compatibility, fit…), and whether it is in stock. Plain prose, no bullet
      points, no slogans, no invented facts. Write it in the language of the
      page. Describe the product as a whole: do NOT single out one option of
      the listing ("in the 3XL size variant", "the blue one") unless the URL
      itself opens on that option — a size or colour picked from one offer in
      the page markup is not the product's identity.
    * category: the most specific category the page states (breadcrumb, product
      type). If none is stated, infer a short, obvious one from the product
      itself (e.g. "Men's T-shirts", "Wireless earbuds") — never leave it null
      when the product is identifiable.
    * specifications: a flat key -> value map from the spec table/bullets.
    * availability: exactly one of in_stock, out_of_stock, preorder, backorder,
      limited, unknown. availability_text is the wording the page actually
      printed — leave it null if the page never says.
    * identifiers: copy any UPC / EAN / GTIN / MPN / ASIN / ISBN / model number
      you see (they are often in the spec table) into this map.
    * Prices are numbers (24.99), currency is an ISO code (USD, EUR, MAD).
      Report the price EXACTLY as the source shows it for a shopper in
      {country} — never convert between currencies and never "correct" a
      currency you find surprising. If the page prints MAD, the answer is MAD.
    * Put anything interesting that has no dedicated field into metadata.
{variants_scope}"""

# The variants rule is swapped in/out: by default one URL yields ONE product,
# not its whole SKU matrix (an AliExpress listing alone is 40 rows).
_VARIANTS_ON = """         - option lists ("Select a size / XS S M L XL", colour swatches) ->
           one `variants` entry per option, name="Size"/"Color", value="XL"
"""
_VARIANTS_OFF = ""
_SCOPE_ON = """    * variants: list the selectable options of THIS product only.
"""
_SCOPE_OFF = """    * variants: leave EMPTY. Describe only the product the URL points at —
      the exact colour/size/model it opens on. Do not list the listing's other
      options, and do not turn a variant picker into extra products. If the
      selected option is part of the identity (e.g. "Black, 3XL"), keep it in
      title/specifications instead.
"""

USER_TEMPLATE = """Extract the product data for this URL:
{url}

Routing hint: {routing}
Shopper country: {country} — the fetch tools already browse from there, so the
prices you receive are the ones this shopper actually pays."""


# ---------------------------------------------------------------------------
# 1. Tools
# ---------------------------------------------------------------------------
def _tool_payload(result: SourceResult) -> str:
    """What a fetch tool hands back to the model."""
    return json.dumps(
        {
            "strategy": result.strategy,
            "source": result.source,
            "final_url": result.final_url,
            "reliable_fields": result.fields,
            "evidence": result.context,
            "warnings": result.warnings,
        },
        ensure_ascii=False,
        default=str,
    )


def build_tools(collected: list[SourceResult], *, force_actor: str | None = None,
                **playwright_options) -> list:
    """Create the tool set for ONE extraction run.

    The tools append every successful SourceResult to `collected`, so after the
    agent finishes we still hold the exact parsed values and can overlay them on
    the model's answer. `force_actor` pins every fetch to one Apify adapter.
    """

    @tool
    def inspect_url(url: str) -> str:
        """Decide how a product URL must be scraped. Returns the domain, the
        detected platform, the strategy (playwright or apify), the Apify actor
        to use if any, and why. Call this first; it makes no network request."""
        route = detect_route(url)
        strategy = "apify" if force_actor else route.strategy
        return json.dumps({
            "url": route.url,
            "domain": route.domain,
            "platform": route.platform,
            "strategy": strategy,
            "actor": force_actor or route.actor,
            "fallback_actor": route.fallback_actor,
            "reason": "actor pinned by the caller" if force_actor else route.reason,
            "recommended_tool": ("fetch_via_apify" if strategy == "apify"
                                 else "fetch_product_page"),
        })

    @tool
    async def fetch_product_page(url: str) -> str:
        """Render a product page with Playwright (real browser, JS executed) and
        return already-parsed reliable_fields plus the cleaned page text as
        evidence. Use for ordinary shops. Returns an 'error' key if the site
        blocks automated browsers — then try fetch_via_apify."""
        if force_actor:
            return json.dumps({"error": f"caller pinned actor '{force_actor}'",
                               "hint": "use fetch_via_apify"})
        try:
            result = await gather_source(url, allow_fallback=False, **playwright_options)
        except ExtractionError as exc:
            return json.dumps({"error": str(exc), "hint": "try fetch_via_apify"})
        collected.append(result)
        return _tool_payload(result)

    @tool
    async def fetch_via_apify(url: str, actor_key: str = "") -> str:
        """Scrape a product URL with a hosted Apify actor (residential proxies,
        anti-bot handling). Use for marketplaces such as Amazon, Temu, Walmart,
        AliExpress and eBay, or when Playwright is blocked. Leave actor_key
        empty to use the routed actor."""
        route = detect_route(url)
        actor = force_actor or actor_key or route.actor or route.fallback_actor
        try:
            result = await gather_source(url, route=route, force_actor=actor)
        except ExtractionError as exc:
            return json.dumps({"error": str(exc)})
        collected.append(result)
        return _tool_payload(result)

    return [inspect_url, fetch_product_page, fetch_via_apify]


# ---------------------------------------------------------------------------
# 2. Agent construction
# ---------------------------------------------------------------------------
def build_agent(tools: list, model: str = ANTHROPIC_MODEL, country: str = TARGET_COUNTRY,
                include_variants: bool = INCLUDE_VARIANTS):
    """create_agent is the current LangChain entry point for tool-calling
    agents (langgraph.prebuilt.create_react_agent is deprecated in v1).
    response_format makes the final answer a validated ProductDraft.

    The key is checked here rather than left to the client constructor: a
    ConfigError is mapped to an explicit "not configured" response, whereas the
    client's own exception is not, and would reach the caller as a bare 500.
    """
    require_anthropic_key()
    system_prompt = SYSTEM_PROMPT.format(
        country=country,
        variants_rule=_VARIANTS_ON if include_variants else _VARIANTS_OFF,
        variants_scope=_SCOPE_ON if include_variants else _SCOPE_OFF,
    )
    return create_agent(
        # No temperature: the current Claude models reject sampling parameters.
        #
        # PROMPT CACHING, two breakpoints, and they do different jobs.
        #
        # 1. `cache_control` at the top level of the request auto-places a
        #    breakpoint on the last cacheable block of every call. An agent loop
        #    resends the whole conversation on every step, so step N+1 reads back
        #    step N's prefix -- system, tool schemas, and the fetched page, which
        #    alone runs to MAX_PAGE_TEXT_CHARS -- at 0.1x instead of 1x. This is
        #    what makes a multi-step extraction stop costing the square of its
        #    step count.
        # 2. The explicit breakpoint on the system block below caches the STATIC
        #    prefix -- system prompt plus the three tool schemas -- which is
        #    byte-identical across extractions (`country` and the variants rules
        #    are fixed by configuration, and the URL lives in the user message,
        #    after the cut). Without it the automatic breakpoint would sit after
        #    the first user message, whose URL differs every run, and no second
        #    extraction could ever read the prefix back.
        #
        # Both are free wins: the payload sent is unchanged, only its billing is.
        # A write costs 1.25x and a read 0.1x, so the pair pays for itself on the
        # second step of the very first extraction. Entries live 5 minutes; that
        # covers a loop's own steps always, and back-to-back extractions often.
        # `summarize_usage` reports reads and writes apart, so the effect is
        # visible in the CLI without any extra instrumentation.
        ChatAnthropic(
            model=model,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            model_kwargs={"cache_control": {"type": "ephemeral"}},
        ),
        tools,
        system_prompt=SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        ),
        response_format=ProductDraft,
    )


# ---------------------------------------------------------------------------
# 3. Public entry points
# ---------------------------------------------------------------------------
async def extract_product(url: str, **options) -> ProductSummary:
    """URL -> the five delivered fields.

        {"name", "description", "category", "image_url", "source_url"}

    This is the normal entry point. `source_url` is echoed back exactly as it
    was given. Everything the pipeline extracted is still available through
    `extract_product_data()` when the full record is wanted.
    """
    product = await extract_product_data(url, **options)
    return summarize(product, source_url=url)


async def extract_product_data(url: str, *, use_agent: bool = True,
                               model: str = ANTHROPIC_MODEL, force_actor: str | None = None,
                               on_event=None, **playwright_options) -> ProductData:
    """URL -> standardized ProductData.

    use_agent=False runs the deterministic pipeline only (no Anthropic key
    needed, no token cost); the schema of the result is identical either way.
    force_actor pins extraction to one Apify adapter, bypassing the routing table.
    `on_event(kind, payload)` receives progress notifications for the CLI.
    """
    route = detect_route(url)
    notify = on_event or (lambda *_: None)
    notify("route", route)

    if not use_agent:
        result = await gather_source(url, route=route, force_actor=force_actor,
                                     **playwright_options)
        notify("source", result)
        return to_product(result, route.url)

    collected: list[SourceResult] = []
    agent = build_agent(
        build_tools(collected, force_actor=force_actor, **playwright_options),
        model=model,
    )

    with get_usage_metadata_callback() as usage:
        try:
            state = await agent.ainvoke(
                {"messages": [("human", USER_TEMPLATE.format(url=route.url,
                                                             routing=route.describe(),
                                                             country=TARGET_COUNTRY))]},
                config={"recursion_limit": AGENT_MAX_STEPS},
            )
            draft: ProductDraft | None = state.get("structured_response")
        except Exception as exc:                   # noqa: BLE001 - see fallback below
            notify("agent_error", exc)
            draft = None
            if not collected:
                # The model never got usable data: fall back to pure code so the
                # caller still receives a record instead of an exception.
                result = await gather_source(url, route=route, force_actor=force_actor,
                                             **playwright_options)
                collected.append(result)

    # Emitted outside the try on purpose: a run that ends in an exception, or one cut
    # short by recursion_limit, has already been billed for every step it took. Those
    # are the expensive runs, so they are exactly the ones worth reporting.
    consumption = summarize_usage(usage.usage_metadata)
    if consumption:
        notify("usage", consumption)

    for result in collected:
        notify("source", result)

    if not collected:
        raise ExtractionError(
            f"No data could be retrieved for {url} — the agent produced no successful fetch."
        )

    # Deterministic values from every tool call the agent made (first wins).
    reliable = merge_partials([r.fields for r in collected])
    soft = {key for r in collected for key in r.soft_fields}
    primary = collected[-1]
    warnings = [w for r in collected for w in r.warnings]

    if draft is None:
        warnings.append("LLM normalization unavailable — deterministic fields only")
        fields = reliable
    else:
        fields = overlay_reliable(draft_to_fields(draft), reliable, soft)

    fields["warnings"] = warnings + list(fields.get("warnings") or [])
    return _finalize(fields, url=route.url, result=primary)


def _finalize(fields: dict[str, Any], *, url: str, result: SourceResult) -> ProductData:
    return build_product(
        fields,
        url=url,
        final_url=result.final_url,
        strategy=result.strategy,
        source=result.source,
    )


def routing_help() -> str:
    return "Registered routes:\n  " + "\n  ".join(supported_routes())

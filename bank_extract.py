"""Reads a product page into plain parts: structured data, Shopify data, text.

extract(html, url) -> Page with:
  name, brand, description, images, offers (price, currency, availability,
  sku, gtin, colour, size), breadcrumbs, structured_text, page_text.

Structured data first (schema.org Product / ProductGroup / Offer in JSON-LD,
then Open Graph product tags), then the visible page text. Nothing is
interpreted here: no fibre parsing, no guessing. That happens in bank_facts.
"""
import html as _html
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

AVAILABILITY = {
    "instock": "in_stock", "in_stock": "in_stock", "onlineonly": "in_stock",
    "limitedavailability": "low_stock", "outofstock": "out_of_stock",
    "soldout": "out_of_stock", "discontinued": "out_of_stock",
    "preorder": "preorder", "presale": "preorder", "backorder": "made_to_order",
    "madetoorder": "made_to_order",
}


@dataclass
class Offer:
    price: Optional[float] = None
    currency: Optional[str] = None
    availability: str = "unknown"
    sku: Optional[str] = None
    gtin: Optional[str] = None
    colour: Optional[str] = None
    size: Optional[str] = None
    sale_from: Optional[float] = None       # the price before a sale, if shown
    source: str = ""                        # json-ld / shopify / og / text


@dataclass
class Page:
    url: str
    name: Optional[str] = None
    brand: Optional[str] = None
    description: str = ""
    images: List[str] = field(default_factory=list)
    offers: List[Offer] = field(default_factory=list)
    breadcrumbs: List[str] = field(default_factory=list)
    is_shopify: bool = False
    structured_text: str = ""               # descriptions and materials from structured data
    page_text: str = ""                     # visible text, one block per line
    sources: List[str] = field(default_factory=list)

    @property
    def all_text(self) -> str:
        """Everything a quote can come from."""
        return "\n".join(x for x in (self.name or "", self.structured_text, self.page_text) if x)


def _num(v) -> Optional[float]:
    """'1,299.00' -> 1299.0, '249,99' -> 249.99, 'C$47.99' -> 47.99."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.,]", "", str(v))
    if not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if re.search(r",\d{2}$", s) else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _avail(v) -> str:
    if not v:
        return "unknown"
    key = re.sub(r"[^a-z_]", "", str(v).rsplit("/", 1)[-1].lower())
    return AVAILABILITY.get(key, "unknown")


def _text_of(fragment: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", fragment)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|dt|dd|section|article|span class=\"line\")>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    lines = [re.sub(r"[ \t ]+", " ", l).strip() for l in s.splitlines()]
    out, seen = [], set()
    for l in lines:
        if l and l not in seen:
            out.append(l)
            seen.add(l)
    return "\n".join(out)


def _jsonld_blocks(page_html: str) -> List[Any]:
    out = []
    for m in re.finditer(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page_html):
        raw = m.group(1).strip()
        try:
            out.append(json.loads(raw))
        except ValueError:
            try:                            # a common mistake: trailing commas
                out.append(json.loads(re.sub(r",\s*([}\]])", r"\1", raw)))
            except ValueError:
                continue
    return out


def _walk(node):
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        yield node
        for k in ("@graph", "hasVariant", "itemListElement", "mainEntity"):
            if k in node:
                yield from _walk(node[k])


def _types(node) -> List[str]:
    t = node.get("@type", [])
    return [str(x) for x in (t if isinstance(t, list) else [t])]


def _brand_name(b) -> Optional[str]:
    if isinstance(b, dict):
        return b.get("name")
    if isinstance(b, list) and b:
        return _brand_name(b[0])
    return b if isinstance(b, str) else None


def _offers_from(node, colour=None, size=None) -> List[Offer]:
    raw = node.get("offers")
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for o in items:
        if not isinstance(o, dict):
            continue
        if "AggregateOffer" in _types(o) and not o.get("offers"):
            out.append(Offer(price=_num(o.get("lowPrice") or o.get("price")),
                             currency=o.get("priceCurrency"), availability=_avail(o.get("availability")),
                             colour=colour, size=size, source="json-ld"))
            continue
        for oo in (o.get("offers") if isinstance(o.get("offers"), list) else [o]):
            spec = oo.get("priceSpecification") or {}
            if isinstance(spec, list):
                spec = spec[0] if spec else {}
            out.append(Offer(
                price=_num(oo.get("price") or spec.get("price")),
                currency=(oo.get("priceCurrency") or spec.get("priceCurrency") or "").upper() or None,
                availability=_avail(oo.get("availability")),
                sku=oo.get("sku") or node.get("sku"),
                gtin=oo.get("gtin13") or oo.get("gtin12") or oo.get("gtin14") or oo.get("gtin8") or oo.get("gtin"),
                colour=colour, size=size, source="json-ld"))
    return out


def _from_jsonld(page: Page, blocks):
    for node in (n for b in blocks for n in _walk(b)):
        types = _types(node)
        if "BreadcrumbList" in types:
            crumbs = []
            for i in node.get("itemListElement", []):
                if isinstance(i, dict):
                    item = i.get("item")
                    crumbs.append(i.get("name") or (item.get("name") if isinstance(item, dict) else None))
            page.breadcrumbs = [c for c in crumbs if c]
        if not ({"Product", "ProductGroup", "IndividualProduct"} & set(types)):
            continue
        page.name = page.name or node.get("name")
        page.brand = page.brand or _brand_name(node.get("brand")) or _brand_name(node.get("manufacturer"))
        desc = node.get("description") or ""
        bits = [desc] if desc else []
        for k in ("material", "color", "size", "pattern"):
            v = node.get(k)
            if v:
                bits.append(f"{k.title()}: {v if isinstance(v, str) else ', '.join(map(str, v))}")
        if bits:
            page.structured_text += ("\n" if page.structured_text else "") + _text_of("\n".join(bits))
        imgs = node.get("image") or []
        for i in (imgs if isinstance(imgs, list) else [imgs]):
            u = i.get("url") if isinstance(i, dict) else i
            if isinstance(u, str) and u not in page.images:
                page.images.append(u)
        page.offers += _offers_from(node, colour=node.get("color"), size=node.get("size"))
        if "json-ld" not in page.sources:
            page.sources.append("json-ld")


def _from_og(page: Page, page_html: str):
    def meta(prop):
        m = re.search(r'(?is)<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']*)["\']' % re.escape(prop), page_html) \
            or re.search(r'(?is)<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(prop), page_html)
        return _html.unescape(m.group(1)).strip() if m else None
    page.name = page.name or meta("og:title")
    page.brand = page.brand or meta("product:brand") or meta("og:brand")
    img = meta("og:image")
    if img and img not in page.images:
        page.images.append(img)
    if not page.offers:
        price = _num(meta("product:price:amount") or meta("og:price:amount"))
        if price is not None:
            page.offers.append(Offer(price=price,
                                     currency=(meta("product:price:currency") or meta("og:price:currency") or "").upper() or None,
                                     availability=_avail(meta("product:availability") or meta("og:availability")),
                                     source="og"))
            page.sources.append("og")


def extract(page_html: str, url: str) -> Page:
    page = Page(url=url)
    page.is_shopify = bool(re.search(r"cdn\.shopify\.com|Shopify\.theme|shopify-section", page_html or ""))
    _from_jsonld(page, _jsonld_blocks(page_html or ""))
    _from_og(page, page_html or "")
    body = re.search(r"(?is)<body[^>]*>(.*)</body>", page_html or "")
    page.page_text = _text_of(body.group(1) if body else (page_html or ""))[:60000]
    if not page.name:
        m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", page_html or "") or re.search(r"(?is)<title>(.*?)</title>", page_html or "")
        page.name = _text_of(m.group(1)).strip() if m else None
    return page


def shopify_json_url(url: str) -> Optional[str]:
    """The public Shopify product data address for a /products/ page."""
    parts = urlsplit(url)
    m = re.search(r"(/(?:[a-z]{2}(?:-[a-z]{2})?/)?products/[^/?#]+)", parts.path, re.I)
    if not m:
        return None
    return urlunsplit((parts.scheme, parts.netloc, m.group(1) + ".js", "", ""))


def add_shopify(page: Page, data: Dict[str, Any]):
    """Merge Shopify product JSON (the .js endpoint) into a page. Prices there are in cents."""
    if not isinstance(data, dict) or "variants" not in data:
        return
    page.name = page.name or data.get("title")
    page.brand = page.brand or data.get("vendor")
    desc = _text_of(data.get("description") or data.get("body_html") or "")
    if desc and desc not in page.structured_text:
        page.structured_text += ("\n" if page.structured_text else "") + desc
    opts = [o.get("name", "").lower() if isinstance(o, dict) else str(o).lower() for o in data.get("options", [])]
    shop_offers = []
    for v in data.get("variants", []):
        values = [v.get("option1"), v.get("option2"), v.get("option3")]
        colour = size = None
        for name, val in zip(opts, values):
            if name in ("color", "colour"):
                colour = val
            elif name == "size":
                size = val
        price = v.get("price")
        price = price / 100 if isinstance(price, int) else _num(price)
        before = v.get("compare_at_price")
        before = before / 100 if isinstance(before, int) else _num(before)
        shop_offers.append(Offer(price=price, currency=None,
                                 availability="in_stock" if v.get("available") else "out_of_stock",
                                 sku=v.get("sku"), gtin=v.get("barcode"), colour=colour, size=size,
                                 sale_from=before if before and price and before > price else None,
                                 source="shopify"))
    if shop_offers:
        currency = next((o.currency for o in page.offers if o.currency), None)
        for o in shop_offers:
            o.currency = currency
        page.offers = shop_offers
        page.sources.append("shopify")
    for img in data.get("images", []):
        u = img if isinstance(img, str) else (img or {}).get("src")
        if u:
            u = "https:" + u if u.startswith("//") else u
            if u not in page.images:
                page.images.append(u)

"""
Home card — the photo and line on the app's home screen, changed from a web page.

WHY. Changing the home photo used to mean hosting an image somewhere, copying
its link and pasting it into a Railway variable. This replaces all of that with
one page: open /admin/home in a browser, pick a photo, press Save. The photo is
stored in Postgres and served by this backend, and every phone shows it the
next time Filo is opened. No App Store update, no Railway, no hosting.

SLOTS
  fall, winter, spring, summer   one per season; the app shows the current one
  now                            an override for a launch or a holiday — beats
                                 the season until it is cleared

Each slot holds a photo (resized to ≤1600px JPEG in the browser before upload,
so rows stay small), an optional alt text for VoiceOver, and optional copy
(eyebrow + title). Anything left blank falls back to the season defaults in
main.py.

The admin page is public HTML but does nothing without the ADMIN_TOKEN; every
write goes through the same x-filo-admin check as the other /internal routes.
"""
import base64
import logging
from typing import Optional, Dict, Any

from pydantic import BaseModel, Field

import events

log = logging.getLogger("filo.homecard")

SLOTS = ("now", "fall", "winter", "spring", "summer")
MAX_BYTES = 2_500_000          # after base64 decoding; the page sends ~300–600 KB
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS home_cards (
    slot          TEXT        PRIMARY KEY,
    image         BYTEA,
    content_type  TEXT,
    alt           TEXT,
    eyebrow       TEXT,
    title         TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def init_schema() -> bool:
    conn = events._conn()
    if conn is None:
        return False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
        return True
    except Exception as exc:            # noqa: BLE001
        log.warning("homecard: schema not created (%s)", exc)
        return False
    finally:
        conn.close()


class CardIn(BaseModel):
    image_b64: Optional[str] = Field(default=None, max_length=4_000_000)
    content_type: Optional[str] = "image/jpeg"
    alt: Optional[str] = Field(default=None, max_length=200)
    eyebrow: Optional[str] = Field(default=None, max_length=40)
    title: Optional[str] = Field(default=None, max_length=60)


def _clean(text: Optional[str]) -> Optional[str]:
    text = (text or "").strip()
    return text or None


def save(slot: str, card: CardIn) -> Dict[str, Any]:
    """Create or update a slot. A missing image keeps the one already there."""
    if slot not in SLOTS:
        raise ValueError("Unknown slot.")
    data = None
    if card.image_b64:
        raw = card.image_b64.split(",", 1)[-1]          # tolerate a data: URL
        try:
            data = base64.b64decode(raw, validate=True)
        except Exception:                               # noqa: BLE001
            raise ValueError("That image couldn't be read.")
        if len(data) > MAX_BYTES:
            raise ValueError("That image is too large — keep it under 2.5 MB.")
        if (card.content_type or "image/jpeg") not in ALLOWED_TYPES:
            raise ValueError("Use a JPEG, PNG or WebP image.")
    conn = events._conn()
    if conn is None:
        raise RuntimeError("no database")
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM home_cards WHERE slot=%s", (slot,))
                exists = cur.fetchone() is not None
                if not exists and data is None:
                    raise ValueError("Add a photo first.")
                if exists:
                    if data is not None:
                        cur.execute("""UPDATE home_cards SET image=%s, content_type=%s,
                                       alt=%s, eyebrow=%s, title=%s, updated_at=NOW()
                                       WHERE slot=%s""",
                                    (data, card.content_type or "image/jpeg",
                                     _clean(card.alt), _clean(card.eyebrow),
                                     _clean(card.title), slot))
                    else:
                        cur.execute("""UPDATE home_cards SET alt=%s, eyebrow=%s, title=%s,
                                       updated_at=NOW() WHERE slot=%s""",
                                    (_clean(card.alt), _clean(card.eyebrow),
                                     _clean(card.title), slot))
                else:
                    cur.execute("""INSERT INTO home_cards (slot, image, content_type, alt,
                                                           eyebrow, title)
                                   VALUES (%s,%s,%s,%s,%s,%s)""",
                                (slot, data, card.content_type or "image/jpeg",
                                 _clean(card.alt), _clean(card.eyebrow), _clean(card.title)))
    finally:
        conn.close()
    return {"ok": True, "slot": slot}


def clear(slot: str) -> Dict[str, Any]:
    if slot not in SLOTS:
        raise ValueError("Unknown slot.")
    conn = events._conn()
    if conn is None:
        raise RuntimeError("no database")
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM home_cards WHERE slot=%s", (slot,))
    finally:
        conn.close()
    return {"ok": True, "slot": slot}


def meta(slot: str) -> Optional[Dict[str, Any]]:
    """Copy and version for a slot, without the image bytes. None if empty or no DB."""
    conn = events._conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT alt, eyebrow, title, EXTRACT(EPOCH FROM updated_at)::BIGINT,
                                  image IS NOT NULL
                           FROM home_cards WHERE slot=%s""", (slot,))
            row = cur.fetchone()
    except Exception as exc:            # noqa: BLE001
        log.info("homecard: read failed (%s)", exc)
        return None
    finally:
        conn.close()
    if not row:
        return None
    alt, eyebrow, title, version, has_image = row
    return {"alt": alt, "eyebrow": eyebrow, "title": title,
            "version": int(version), "has_image": bool(has_image)}


def all_meta() -> Dict[str, Optional[Dict[str, Any]]]:
    return {s: meta(s) for s in SLOTS}


def image(slot: str):
    """(bytes, content_type) or None."""
    if slot not in SLOTS:
        return None
    conn = events._conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT image, content_type FROM home_cards WHERE slot=%s", (slot,))
            row = cur.fetchone()
    except Exception as exc:            # noqa: BLE001
        log.info("homecard: image read failed (%s)", exc)
        return None
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    return bytes(row[0]), row[1] or "image/jpeg"


# ------------------------------------------------------------------ admin page

ADMIN_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Filo · Home card</title>
<style>
:root{--bone:#f8f5f0;--ink:#1c1a18;--stone:#6b625b;--taupe:#a89c90;--greige:#e6dfd6;--ox:#6e1f24}
*{box-sizing:border-box}
body{margin:0;background:var(--bone);color:var(--ink);font:15px/1.45 -apple-system,Helvetica,Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 16px 60px}
h1{font:400 34px Georgia,serif;margin:0 0 4px}
.sub{color:var(--stone);margin:0 0 22px}
.eb{font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--stone)}
.token{display:flex;gap:8px;align-items:center;margin-bottom:26px;flex-wrap:wrap}
input,button{font:inherit}
input[type=text],input[type=password]{border:1px solid var(--greige);background:#fff;border-radius:10px;padding:9px 11px;width:100%}
.token input{max-width:320px}
button{border:0;border-radius:999px;padding:9px 16px;background:var(--ink);color:#fff;cursor:pointer}
button.ghost{background:transparent;color:var(--stone);border:1px solid var(--greige)}
button:disabled{opacity:.5;cursor:default}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:18px}
.slot{background:#fff;border:1px solid var(--greige);border-radius:18px;padding:14px}
.slot.now{border-color:var(--ox)}
.slot h2{font:400 22px Georgia,serif;margin:0}
.slot .hint{color:var(--taupe);font-size:12px;margin:2px 0 10px}
.live{color:var(--ox);font-size:11px;letter-spacing:1.5px;font-weight:600}
.card{position:relative;aspect-ratio:4/5;border-radius:14px;overflow:hidden;background:var(--greige);margin-bottom:10px;cursor:pointer}
.card img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.card .grad{position:absolute;inset:0;background:linear-gradient(to bottom,transparent 45%,rgba(0,0,0,.6))}
.card .copy{position:absolute;left:12px;right:12px;bottom:12px;color:#fff}
.card .copy .e{font-size:9px;letter-spacing:1.6px;font-weight:600}
.card .copy .t{font:400 22px Georgia,serif;margin-top:3px}
.card .empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--stone);font-size:13px;text-align:center;padding:20px}
label{display:block;font-size:12px;color:var(--stone);margin:8px 0 3px}
.row{display:flex;gap:8px;margin-top:12px}
.msg{font-size:12px;margin-top:8px;min-height:16px;color:var(--stone)}
.msg.err{color:#a3262e}
.rules{margin-top:30px;color:var(--stone);font-size:13px;max-width:680px}
</style></head><body><div class="wrap">
<div class="eb">Filo admin</div>
<h1>Home card</h1>
<p class="sub">Tap a card to pick a photo, then Save. Phones show it the next time Filo is opened.</p>
<div class="token">
  <input id="tok" type="password" placeholder="Admin token (from Railway → ADMIN_TOKEN)" autocomplete="off">
  <button id="unlock">Unlock</button><span id="tokmsg" class="msg"></span>
</div>
<div class="grid" id="grid"></div>
<div class="rules">
  <p><b>Photos:</b> portrait (about 4:5), subject in the top two-thirds so the words sit on the darker bottom.
  Use only photos you shot or licensed — no Pinterest or Google images — and no brand logos.</p>
  <p><b>Now</b> beats the season until you clear it. Leave the words blank to use the season's default line.</p>
</div>
</div>
<script>
const SLOTS=[["now","Now","Launch or holiday — overrides the season"],["fall","Fall","Sep – Nov"],["winter","Winter","Dec – Feb"],["spring","Spring","Mar – May"],["summer","Summer","Jun – Aug"]];
let TOKEN="";try{TOKEN=localStorage.getItem("filo.admin")||""}catch(e){}
const $=s=>document.querySelector(s);$("#tok").value=TOKEN;
const pending={};
function api(method,path,body){return fetch(path,{method,headers:{"content-type":"application/json","x-filo-admin":TOKEN},body:body?JSON.stringify(body):undefined}).then(async r=>{if(!r.ok){let d="Something went wrong ("+r.status+")";try{d=(await r.json()).detail||d}catch(e){};if(r.status===404)d="Wrong admin token.";throw new Error(d)}return r.json()})}
function shrink(file){return new Promise((res,rej)=>{const img=new Image();img.onload=()=>{const max=1600;let w=img.width,h=img.height;const k=Math.min(1,max/Math.max(w,h));w=Math.round(w*k);h=Math.round(h*k);const c=document.createElement("canvas");c.width=w;c.height=h;c.getContext("2d").drawImage(img,0,0,w,h);res(c.toDataURL("image/jpeg",0.85))};img.onerror=()=>rej(new Error("That file isn't an image."));img.src=URL.createObjectURL(file)})}
function render(state){const g=$("#grid");g.innerHTML="";for(const [slot,name,hint] of SLOTS){const m=state.slots[slot];const d=state.defaults[slot]||{};const live=state.live===slot;
const el=document.createElement("div");el.className="slot"+(slot==="now"?" now":"");
const src=pending[slot]?pending[slot]:(m&&m.has_image?"/home-image/"+slot+"?v="+m.version:"");
el.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:baseline"><h2>${name}</h2>${live?'<span class="live">LIVE NOW</span>':''}</div><div class="hint">${hint}</div>
<div class="card">${src?`<img src="${src}"><div class="grad"></div>`:`<div class="empty">Tap to add a photo</div>`}
${src?`<div class="copy"><div class="e"></div><div class="t"></div></div>`:''}</div>
<input type="file" accept="image/*" hidden>
<label>Small line (e.g. COAT SEASON)</label><input type="text" class="eyebrow" maxlength="40" placeholder="${d.eyebrow||''}">
<label>Big line</label><input type="text" class="title" maxlength="60" placeholder="${d.title||''}">
<label>Describe the photo (for VoiceOver)</label><input type="text" class="alt" maxlength="200" placeholder="e.g. A woman checking a coat's lining">
<div class="row"><button class="save">Save</button>${m?'<button class="ghost clear">Clear</button>':''}</div><div class="msg"></div>`;
const q=s=>el.querySelector(s),file=q("input[type=file]");
q(".eyebrow").value=(m&&m.eyebrow)||"";q(".title").value=(m&&m.title)||"";q(".alt").value=(m&&m.alt)||"";
const paint=()=>{const e=q(".copy .e"),t=q(".copy .t");if(e){e.textContent=((q(".eyebrow").value||d.eyebrow||"").toUpperCase()+" · SCAN");t.textContent=q(".title").value||d.title||""}};paint();
q(".eyebrow").oninput=paint;q(".title").oninput=paint;
q(".card").onclick=()=>file.click();
file.onchange=async()=>{const f=file.files[0];if(!f)return;try{pending[slot]=await shrink(f);render(state)}catch(e){q(".msg").textContent=e.message;q(".msg").className="msg err"}};
q(".save").onclick=async()=>{const b=q(".save");b.disabled=true;q(".msg").className="msg";q(".msg").textContent="Saving…";
try{await api("POST","/admin/home/"+slot,{image_b64:pending[slot]||null,content_type:"image/jpeg",eyebrow:q(".eyebrow").value,title:q(".title").value,alt:q(".alt").value});delete pending[slot];await load();}
catch(e){q(".msg").textContent=e.message;q(".msg").className="msg err";b.disabled=false}};
const c=q(".clear");if(c)c.onclick=async()=>{if(!window.confirm("Clear "+name+"?"))return;try{await api("DELETE","/admin/home/"+slot);await load()}catch(e){q(".msg").textContent=e.message;q(".msg").className="msg err"}};
g.appendChild(el)}}
async function load(){if(!TOKEN){$("#tokmsg").textContent="Enter your admin token to start.";return}
try{const s=await api("GET","/admin/home/state");$("#tokmsg").textContent="";render(s)}catch(e){$("#tokmsg").textContent=e.message;$("#tokmsg").className="msg err"}}
$("#unlock").onclick=()=>{TOKEN=$("#tok").value.trim();try{localStorage.setItem("filo.admin",TOKEN)}catch(e){};load()};
load();
</script></body></html>"""

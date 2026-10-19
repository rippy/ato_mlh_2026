#!/usr/bin/env python3
"""Tiny local web UI to human-verify machine gluten-free claims in annotations.csv.

    python3 verify_ui.py            # then open http://localhost:8001
    python3 verify_ui.py --port 8002

Walks through each machine claim (gf_status yes/limited, verified != yes, not human-checked),
shows the quote and the surrounding text fetched live from evidence_url, and lets you
Confirm / Change status / Reject. Only this tool (a human click) writes verified=yes.
Stdlib + curl only.
"""
import argparse
import csv
import datetime
import html
import json
import os
import re
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "annotations.csv")
UA = "gf-finder-hackathon/0.1 (human verification; contact jkrippy@gmail.com)"
LOCK = threading.Lock()
CTX_CACHE = {}


def load():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


def save(fields, rows):
    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, CSV_PATH)


def needs_review(r):
    return (r["gf_status"] in ("yes", "limited") and r["verified"] != "yes"
            and not r["checked_by"].startswith("human"))


FAIL_RE = re.compile(r"JavaScript|HTTP 40|Could not|not found on the pages")


def needs_manual(r):
    """Machine could not read the site (JS-only, blocked, bad quote): a human may still find GF info."""
    return (r["gf_status"] == "unknown" and r["evidence_url"] and not r["checked_by"].startswith("human")
            and bool(FAIL_RE.search(r["evidence_note"])))


def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def page_text(url):
    p = subprocess.run(["curl", "-sL", "--max-time", "15", "-A", UA, url],
                       capture_output=True, timeout=20)
    raw = p.stdout.decode("utf-8", "replace")
    raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return norm(html.unescape(raw))


def context(row):
    """Fetch the evidence page and return (found, html_snippet)."""
    key = row["hsisid"]
    if key in CTX_CACHE:
        return CTX_CACHE[key]
    url = row["evidence_url"]
    if not url:
        return CTX_CACHE.setdefault(key, (False, "No evidence_url on this row."))
    try:
        text = page_text(url)
    except Exception as e:  # network trouble is information for the human, not a crash
        return False, "Could not fetch page: %s" % html.escape(str(e))
    quote = norm(row["evidence_note"].split('"')[1] if '"' in row["evidence_note"] else row["evidence_note"])
    i = text.find(quote) if quote else -1
    found = i >= 0
    if not found:
        # fall back to the first gluten mention so the human still sees something
        m = re.search(r"gluten|celiac|\bgf\b", text)
        if not m:
            res = (False, "Page fetched (%d chars) but no gluten / celiac / GF mention. "
                          "It may be JS-rendered or a PDF: use the link." % len(text))
            CTX_CACHE[key] = res
            return res
        i, quote = m.start(), m.group(0)
    a, b = max(0, i - 300), min(len(text), i + len(quote) + 300)
    snip = (html.escape(text[a:i]) + "<mark>" + html.escape(text[i:i + len(quote)]) + "</mark>"
            + html.escape(text[i + len(quote):b]))
    res = (found, ("…" if a else "") + snip + ("…" if b < len(text) else ""))
    CTX_CACHE[key] = res
    return res


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Verify gluten-free claims</title>
<style>
 body{font:16px/1.45 system-ui,sans-serif;max-width:860px;margin:1.5rem auto;padding:0 1rem;color:#1b1b1b}
 header{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap}
 .bar{height:8px;background:#e5e5e5;border-radius:4px;margin:.5rem 0 1rem}.bar i{display:block;height:8px;background:#2e8b57;border-radius:4px}
 .card{border:1px solid #ccc;border-radius:10px;padding:1rem 1.25rem}
 h2{margin:.1rem 0}.meta{color:#555;font-size:.9rem}
 blockquote{margin:.8rem 0;padding:.5rem .9rem;border-left:4px solid #888;background:#f6f6f6}
 .ctx{font-size:.92rem;background:#fffdf0;border:1px solid #e6dfa8;border-radius:6px;padding:.6rem .8rem;margin:.5rem 0}
 mark{background:#ffe066}.warn{color:#a15c00}.ok{color:#17703d}
 button,a.btn{font:inherit;padding:.55rem 1rem;border-radius:8px;border:1px solid #888;background:#fff;cursor:pointer;text-decoration:none;color:inherit;display:inline-block}
 .go{background:#2e8b57;color:#fff;border-color:#2e8b57}.no{background:#b3261e;color:#fff;border-color:#b3261e}
 .row{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:.9rem;align-items:center}
 input,select{font:inherit;padding:.4rem}
 kbd{background:#eee;border:1px solid #bbb;border-radius:4px;padding:0 .3rem;font-size:.8rem}
</style>
<header><h1 style="margin:0">Verify GF claims</h1>
<label>Your name <input id=who size=14 placeholder="e.g. jon"></label></header>
<div class=bar><i id=prog></i></div>
<div id=app>Loading…</div>
<p class=meta>Keys: <kbd>Y</kbd> confirm · <kbd>N</kbd> reject · <kbd>S</kbd> skip · <a href="/?mode=failed">second pass (sites the AI could not read)</a>. Open the link and read the quote on the live page before confirming.</p>
<div class=row><button onclick="rebuild()">Rebuild map.html</button><span id=rb class=meta></span></div>
<script>
let queue=[], i=0, total=0, done=0;
const MODE=new URLSearchParams(location.search).get('mode')||'claims';
const $=id=>document.getElementById(id);
$('who').value=localStorage.who||''; $('who').oninput=e=>localStorage.who=e.target.value;
async function load(){const r=await (await fetch('/api/queue?mode='+MODE)).json(); queue=r.queue; total=r.total; done=r.done; i=0; show();}
function esc(s){return s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function show(){
  $('prog').style.width=(total? 100*done/total:100)+'%';
  if(i>=queue.length){$('app').innerHTML='<div class=card><h2>All reviewed 🎉</h2><p>'+done+' of '+total+' machine claims have a human decision. Rebuild the map below.</p></div>';return;}
  const r=queue[i];
  if(MODE==='failed') return showManual(r);
  $('app').innerHTML=`<div class=card>
   <div class=meta>${done} of ${total} done · reviewing ${i+1} of ${queue.length} remaining</div>
   <h2>${esc(r.name)}</h2><div class=meta>${esc(r.address)} · ${r.dist_m} m from the Convention Center</div>
   <p>Machine claim: <b>${r.gf_status}</b> (${esc(r.checked_by)})</p>
   <blockquote>${esc(r.evidence_note)}</blockquote>
   <div class=row><a class=btn href="${esc(r.evidence_url)}" target=_blank rel=noopener>Open evidence page ↗</a></div>
   <div class=ctx id=ctx>Fetching page text…</div>
   <div class=row>
     <label>Your verdict <select id=st><option value=yes>yes – GF options</option><option value=limited>limited – some GF / cross-contact</option><option value=only>only – fully GF</option></select></label>
     <input id=note size=36 placeholder="optional note">
   </div>
   <div class=row>
     <button class=go onclick="act('confirm')">✔ Confirm (Y)</button>
     <button class=no onclick="act('reject')">✘ Reject – not GF (N)</button>
     <button onclick="act('skip')">Skip (S)</button></div></div>`;
  $('st').value=r.gf_status;
  const c=await (await fetch('/api/context?id='+r.hsisid)).json();
  if(queue[i]===r) $('ctx').innerHTML=(c.found?'<span class=ok>✔ Quote found on page.</span> ':'<span class=warn>⚠ Exact quote not found.</span> ')+'<br>'+c.html;
}
async function showManual(r){
  $('app').innerHTML=`<div class=card>
   <div class=meta>${done} of ${total} done · ${queue.length-i} remaining · second pass: the AI could not read this site</div>
   <h2>${esc(r.name)}</h2><div class=meta>${esc(r.address)} · ${r.dist_m} m</div>
   <blockquote>${esc(r.evidence_note)}</blockquote>
   <div class=row><a class=btn href="${esc(r.evidence_url)}" target=_blank rel=noopener>Open site ↗</a>
     <span class=meta>Look at the menu / allergen info, then copy the exact words.</span></div>
   <div class=ctx id=ctx>Fetching page text…</div>
   <div class=row><label>What you found <select id=st><option value=yes>yes – GF options</option><option value=limited>limited</option><option value=only>only – fully GF</option><option value=no>no – says not GF</option></select></label></div>
   <div class=row><input id=quote size=70 placeholder="Paste the exact quote from the page"></div>
   <div class=row><button class=go onclick="act('manual')">✔ Save as verified</button>
     <button onclick="act('skip')">Nothing found / skip (S)</button></div></div>`;
  const c=await (await fetch('/api/context?id='+r.hsisid)).json();
  if(queue[i]===r) $('ctx').innerHTML=(c.found?'':'<span class=warn>No exact match; showing first gluten mention if any.</span><br>')+c.html;
}
async function act(a){
  const r=queue[i];
  if(a!=='skip'){
    if(!$('who').value.trim()){alert('Enter your name first (top right).');$('who').focus();return;}
    const res=await fetch('/api/decide',{method:'POST',body:JSON.stringify({hsisid:r.hsisid,action:a,status:$('st').value,note:MODE==='failed'&&a!=='skip'?$('quote').value:$('note').value,who:$('who').value.trim()})});
    if(!res.ok){alert(await res.text());return;}
    done++;
  }
  i++; show();
}
async function rebuild(){$('rb').textContent='building…';const r=await (await fetch('/api/rebuild',{method:'POST'})).json();$('rb').textContent=r.ok?'map.html rebuilt ✔':'failed: '+r.out;}
document.addEventListener('keydown',e=>{if(/INPUT|SELECT/.test(e.target.tagName))return;
  ({y:'confirm',n:'reject',s:'skip'})[e.key.toLowerCase()]&&act(({y:'confirm',n:'reject',s:'skip'})[e.key.toLowerCase()]);});
load();
</script>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            return self.send(PAGE, "text/html")
        _, rows = load()
        if u.path == "/api/queue":
            humans = [r for r in rows if r["checked_by"].startswith("human") and r["gf_status"] in ("yes", "limited", "only", "no")]
            failed = urllib.parse.parse_qs(u.query).get("mode") == ["failed"]
            q = [r for r in rows if (needs_manual(r) if failed else needs_review(r))]
            return self.send(json.dumps({"queue": q, "total": len(q) + len(humans), "done": len(humans)}))
        if u.path == "/api/context":
            hid = urllib.parse.parse_qs(u.query).get("id", [""])[0]
            row = next((r for r in rows if r["hsisid"] == hid), None)
            if not row:
                return self.send("{}", code=404)
            found, snip = context(row)
            return self.send(json.dumps({"found": found, "html": snip}))
        self.send("not found", "text/plain", 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/rebuild":
            p = subprocess.run(["python3", os.path.join(HERE, "gf_map.py")], cwd=HERE,
                               capture_output=True, text=True)
            return self.send(json.dumps({"ok": p.returncode == 0, "out": (p.stdout + p.stderr)[-400:]}))
        if self.path != "/api/decide":
            return self.send("not found", "text/plain", 404)
        with LOCK:
            fields, rows = load()
            row = next((r for r in rows if r["hsisid"] == body["hsisid"]), None)
            if not row:
                return self.send("unknown hsisid", "text/plain", 404)
            today = datetime.date.today().isoformat()
            who = re.sub(r"[^\w .-]", "", body["who"])[:40]
            note = body.get("note", "").strip()
            if body["action"] == "manual":
                if body["status"] not in ("yes", "limited", "only", "no") or not note:
                    return self.send("Paste the quote first.", "text/plain", 400)
                row["gf_status"] = body["status"]
                row["verified"] = "yes"
                row["source"] = "human"
                row["evidence_note"] = '"%s" (human-read)' % note.strip('"')
            elif body["action"] == "confirm":
                row["gf_status"] = body["status"] if body["status"] in ("yes", "limited", "only") else row["gf_status"]
                row["verified"] = "yes"
                row["source"] = "human"
                row["evidence_note"] = row["evidence_note"] + (" | human: " + note if note else "")
            else:  # reject: keep the audit trail, drop the claim
                row["evidence_note"] = ("Human rejected machine claim '%s'. " % row["gf_status"]
                                        + (note + " " if note else "") + "Was: " + row["evidence_note"])
                row["gf_status"] = "no"
                row["verified"] = "yes"
                row["source"] = "human"
            row["checked_by"] = "human:" + who
            row["checked_on"] = today
            save(fields, rows)
        self.send(json.dumps({"ok": True}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()
    fields, rows = load()
    print("%d claims to review. Open http://localhost:%d" % (sum(needs_review(r) for r in rows), args.port))
    ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()


if __name__ == "__main__":
    main()

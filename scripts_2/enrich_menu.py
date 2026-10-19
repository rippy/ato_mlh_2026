#!/usr/bin/env python3
"""Fill in gluten-free status in annotations.csv by reading each restaurant's own web pages.

Choose how the pages are judged with --backend (or the ENRICH_BACKEND environment variable):

    rules    No AI. Keyword rules over the page text. Instant, deterministic, a good baseline.
    claude   Claude Code CLI in headless mode (`claude -p`, tools off). Uses your Claude login.
             Env: CLAUDE_MODEL (default haiku), CLAUDE_BIN (default claude).
    apfel    Apple's on-device model through the `apfel` CLI. Small model, 4096-token window
             (input + output), so only about 2,600 characters of page text are sent.
             Env: APFEL_BIN (default apfel).
    openai   Any OpenAI-compatible server: `apfel --serve`, Ollama, LM Studio, DigitalOcean...
             Env: LLM_BASE (default http://localhost:11434), LLM_MODEL (default
             apple-foundation-model), LLM_KEY (optional; MODEL_ACCESS_KEY also works).

    python3 enrich_menu.py --backend rules --dry-run     # see which pages would be read
    python3 enrich_menu.py --backend claude --check      # one tiny test call
    python3 enrich_menu.py --backend claude --limit 5    # try a few first
    python3 enrich_menu.py --backend claude --workers 4  # everything, 4 restaurants at a time
    python3 enrich_menu.py --backend rules --redo        # rerun machine rows with another backend

A row is processed when it has evidence_url, a blank gf_status and a blank checked_by.
Results are written as verified=no (DASHED pins). To confirm one: open evidence_url, check the
quote, then set verified=yes (or fix gf_status). Human-verified rows are never touched.

Safeguards against made-up answers:
  * only text near the words gluten / celiac / GF is sent to a model;
  * a model must return a quote, and the quote must appear word for word on the page,
    otherwise the answer is downgraded to unknown (the rules backend quotes by construction);
  * pages with no such words are marked unknown without calling anything;
  * PDF menus and pages that need JavaScript cannot be read: marked unknown, check by hand.
Each restaurant's site is read lightly: the page you listed plus at most 2 same-site links whose
text or address mentions gluten, allergen, dietary or menu. 1 second between requests per
restaurant, identified in the User-Agent. No other crawling.
"""
import argparse
import concurrent.futures
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import date
from pathlib import Path

import gf_map

UA = "ato_mlh_2026-hackathon/0.1 (https://github.com/rippy/ato_mlh_2026)"
PATTERN = re.compile(r"gluten|celiac|coeliac|\bgf\b", re.I)
WINDOW = 350          # characters kept on each side of a match
MAX_EXTRA_PAGES = 2   # same-site menu/allergen links followed per restaurant
PAUSE_S = 1.0         # pause between page requests for one restaurant
LINK_RE = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"'#][^"']*)["'][^>]*>(.*?)</a>""", re.I | re.S)
STATUSES = ("only", "yes", "limited", "no", "unknown")
BUDGET = {"claude": 12000, "openai": 6000, "apfel": 2600}   # characters of page text sent to the model
DEFAULT_WORKERS = {"claude": 3, "rules": 4, "openai": 1, "apfel": 1}
MACHINE_PREFIXES = ("llm:", "rules", "auto")
LOCK = threading.Lock()


# --------------------------------------------------------------------------- web pages -> text
def fetch_raw(url):
    """Return (raw_html, final_url, problem). Exactly one of raw_html / problem is None."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "page"
        cmd = ["curl", "-sSL", "--compressed", "--max-time", "30", "--max-filesize", "3000000",
               "-A", UA, "-o", str(out), "-w", "%{http_code}\t%{content_type}\t%{url_effective}", url]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return None, url, f"could not fetch page ({r.stderr.strip()[:120]})"
        code, _, rest = r.stdout.partition("\t")
        ctype, _, final = rest.partition("\t")
        final = final.strip() or url
        if not code.startswith("2"):
            return None, final, f"page returned HTTP {code}"
        if "pdf" in ctype.lower() or final.lower().split("?")[0].endswith(".pdf"):
            return None, final, "menu is a PDF; read it by hand"
        return out.read_bytes().decode("utf-8", errors="replace"), final, None


def to_text(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return " ".join(html.unescape(raw).split())


def menu_links(raw, base):
    """Same-site links that look like a menu / allergen / gluten page, best first."""
    host = urllib.parse.urlparse(base).netloc.lower().removeprefix("www.")
    scored = {}
    for href, inner in LINK_RE.findall(raw):
        href = html.unescape(href.strip())
        if href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urllib.parse.urljoin(base, href).split("#")[0]
        p = urllib.parse.urlparse(full)
        if p.scheme not in ("http", "https") or p.netloc.lower().removeprefix("www.") != host:
            continue
        if p.path.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip")):
            continue
        label = (to_text(inner) + " " + p.path).lower()
        score = (3 if "gluten" in label else
                 2 if re.search(r"allerg|dietary|celiac|nutrition", label) else
                 1 if "menu" in label else 0)
        if score and full.rstrip("/") != base.rstrip("/"):
            scored[full] = max(scored.get(full, 0), score)
    return sorted(scored, key=lambda u: -scored[u])[:MAX_EXTRA_PAGES]


def gather(url):
    """Return ([(page_url, text), ...], problem). Exactly one of them is None."""
    raw, base, problem = fetch_raw(url)
    if problem:
        return None, problem
    text = to_text(raw)
    if len(text) < 200:
        return None, "page has almost no readable text (probably needs JavaScript); read it by hand"
    pages = [(base, text)]
    for link in menu_links(raw, base):
        time.sleep(PAUSE_S)
        raw2, final2, prob2 = fetch_raw(link)
        if prob2 or raw2 is None:
            continue
        t2 = to_text(raw2)
        if len(t2) >= 200:
            pages.append((final2, t2))
    return pages, None


def excerpts(text, limit=6000):
    """Text windows around gluten / celiac / GF mentions, merged and capped. Returns (excerpt, hit_count)."""
    spans = []
    for m in PATTERN.finditer(text):
        a, b = max(0, m.start() - WINDOW), min(len(text), m.end() + WINDOW)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    joined = " ... ".join(text[a:b] for a, b in spans)
    return joined[:limit], len(spans)


def combined_excerpts(pages, limit):
    """Excerpts from every page that mentions gluten, each labelled with its address. Returns (text, hits)."""
    with_hits = [(u, t) for u, t in pages if PATTERN.search(t)]
    if not with_hits:
        return "", 0
    per_page = max(600, limit // len(with_hits))
    parts, total = [], 0
    for page_url, text in with_hits:
        ex, hits = excerpts(text, per_page)
        parts.append(f"[page: {page_url}]\n{ex}")
        total += hits
    return "\n\n".join(parts)[:limit], total


def squash(s):
    """Lowercase letters and digits only, so a quote still matches if spacing or punctuation differs."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


# --------------------------------------------------------------------------- backend: rules (no AI)
RULES = [   # (status, pattern). Per sentence the first matching rule wins.
    ("no", re.compile(r"(do(es)?\s+not|don'?t|doesn'?t|cannot|can'?t|unable\s+to|\bno)\s+(currently\s+)?"
                      r"(offer|have|serve|provide|accommodate)\s+[^.]{0,40}gluten"
                      r"|\bno\s+gluten[- ]?free\s+(options?|items?|menu)|\bnot\s+gluten[- ]?free", re.I)),
    ("only", re.compile(r"100\s*%\s*gluten[- ]?free|(entirely|completely|totally|exclusively)\s+gluten[- ]?free"
                        r"|dedicated\s+gluten[- ]?free\s+(kitchen|facility|bakery|restaurant|cafe|space)"
                        r"|we\s+are\s+(a\s+)?gluten[- ]?free\s+(restaurant|bakery|kitchen|cafe|eatery)", re.I)),
    ("yes", re.compile(r"gluten[- ]?free\s+(menu|options?|items?|dishes|selections?|pasta|bread|buns?|crusts?|"
                       r"pizza|choices|offerings)|(menu|options?|items?|dishes)\s+(is|are)\s+(available\s+)?gluten[- ]?free"
                       r"|\bgf\s+(menu|options?|items?|buns?|crusts?|pasta|bread)|celiac[- ]friendly"
                       r"|(offer|have|serve|provide)s?\s+(a\s+)?gluten[- ]?free", re.I)),
    ("limited", re.compile(r"gluten|celiac|coeliac", re.I)),
]
RULE_PRIORITY = {"only": 0, "yes": 1, "no": 2, "limited": 3}


def classify_rules(pages, url):
    """Return (status, note, called, evidence_url). The quote is always a real slice of a page."""
    best = None   # (priority, status, quote, page_url)
    for page_url, text in pages:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if not PATTERN.search(sentence):
                continue
            for status, pat in RULES:
                m = pat.search(sentence)
                if not m:
                    continue
                quote = sentence
                if len(quote) > 320:
                    quote = quote[max(0, m.start() - 160): m.end() + 160]
                cand = (RULE_PRIORITY[status], status, quote.strip(), page_url)
                if best is None or cand[0] < best[0]:
                    best = cand
                break
    if best is None:
        return ("unknown", f"No mention of gluten, celiac or GF on the {len(pages)} page(s) checked "
                "(the menu may be a PDF, an image or another site).", False, url)
    _, status, quote, page_url = best
    return status, f'"{quote}" (keyword rule, not AI)', False, page_url


# --------------------------------------------------------------------------- backends: models
def llm_claude(system, user):
    cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p", user,
           "--model", os.environ.get("CLAUDE_MODEL", "haiku"),
           "--system-prompt", system, "--output-format", "json",
           "--no-session-persistence", "--disable-slash-commands", "--tools", ""]
    with tempfile.TemporaryDirectory() as cwd:      # empty directory: no project CLAUDE.md / .mcp.json is loaded
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               cwd=cwd, timeout=180)
        except FileNotFoundError:
            raise RuntimeError("`claude` not found on PATH (set CLAUDE_BIN)")
        except subprocess.TimeoutExpired:
            raise RuntimeError("claude timed out after 180s")
    if r.returncode != 0:
        raise RuntimeError(f"claude exit {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return r.stdout
    if isinstance(data, dict):
        if data.get("is_error"):
            raise RuntimeError(f"claude error: {str(data.get('result'))[:300]}")
        return str(data.get("result", ""))
    return r.stdout


def llm_apfel(system, user):
    cmd = [os.environ.get("APFEL_BIN", "apfel"), "-s", system, user]
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        raise RuntimeError("`apfel` not found on PATH (set APFEL_BIN)")
    except subprocess.TimeoutExpired:
        raise RuntimeError("apfel timed out after 180s")
    if r.returncode != 0:
        raise RuntimeError(f"apfel exit {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def openai_conf():
    return (os.environ.get("LLM_BASE", "http://localhost:11434").rstrip("/"),
            os.environ.get("LLM_MODEL", "apple-foundation-model"),
            os.environ.get("LLM_KEY") or os.environ.get("MODEL_ACCESS_KEY", ""))


def openai_call(method, path, body=None):
    base, _, key = openai_conf()
    cmd = ["curl", "-sS", "--max-time", "120", "-X", method, f"{base}{path}", "-H", "Content-Type: application/json",
           "-w", "\n%{http_code}"]
    if key:
        cmd += ["-H", f"Authorization: Bearer {key}"]
    if body is not None:
        cmd += ["--data-binary", "@-"]
    r = subprocess.run(cmd, input=json.dumps(body) if body is not None else None, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl failed: {r.stderr.strip()[:200]}")
    payload, _, code = r.stdout.rpartition("\n")
    return int(code or 0), payload


def llm_openai(system, user):
    _, model, _ = openai_conf()
    code, payload = openai_call("POST", "/v1/chat/completions", {
        "model": model, "temperature": 0, "max_completion_tokens": 400,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    if code == 200:
        return json.loads(payload)["choices"][0]["message"]["content"]
    raise RuntimeError(f"HTTP {code}: {payload[:200]}")


BACKENDS = {"claude": llm_claude, "apfel": llm_apfel, "openai": llm_openai}


def call_llm(backend, system, user, tries=3):
    last = None
    for attempt in range(1, tries + 1):
        try:
            return BACKENDS[backend](system, user)
        except RuntimeError as e:
            last = e
            if "not found" in str(e) or attempt == tries:
                break
            time.sleep(3 * attempt)
    raise last


SYSTEM = ("You read excerpts from a restaurant's own website and report whether the restaurant offers "
          "gluten-free food. Use ONLY the provided text. Reply with a single JSON object and nothing else.")


def build_prompt(name, url, excerpt):
    return (f"Restaurant: {name}\nSite: {url}\n\nExcerpts from its pages that mention gluten, celiac or GF:\n"
            f"---\n{excerpt}\n---\n\n"
            'Return JSON with exactly these keys:\n'
            '"gf_status": one of\n'
            '  "only"    - the whole menu is gluten-free,\n'
            '  "yes"     - there is a dedicated gluten-free menu, or items are clearly marked gluten-free,\n'
            '  "limited" - only a vague accommodation ("ask your server", "we cannot guarantee") or very few items,\n'
            '  "no"      - the text says there are no gluten-free options,\n'
            '  "unknown" - the excerpts do not say.\n'
            '"quote": ONE sentence copied EXACTLY from the excerpts that supports your answer ("" if unknown).\n'
            '"cross_contact": a short note if the text mentions cross-contact or celiac safety, otherwise "".')


def parse_reply(reply):
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(reply[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def classify_llm(backend, name, url, pages, limit):
    """Return (status, note, called_model, evidence_url)."""
    ex, hits = combined_excerpts(pages, limit)
    if not hits:
        return ("unknown", f"No mention of gluten, celiac or GF on the {len(pages)} page(s) checked "
                "(the menu may be a PDF, an image or another site).", False, url)
    reply = call_llm(backend, SYSTEM, build_prompt(name, url, ex))
    data = parse_reply(reply)
    if data is None:
        return "unknown", f"Model reply was not valid JSON: {reply[:120]!r}", True, url
    status = str(data.get("gf_status", "")).strip().lower()
    quote = str(data.get("quote", "")).strip()
    extra = str(data.get("cross_contact", "")).strip()
    if status not in STATUSES:
        return "unknown", f"Model returned unusable status {status!r}", True, url
    if status == "unknown":
        return "unknown", "Pages mention gluten but the model could not tell what is offered.", True, url
    page = next((u for u, t in pages if quote and squash(quote) in squash(t)), None)
    if page is None:
        return ("unknown", f"Model said {status!r} but its quote was not found on the pages, so it was "
                "discarded. Read the pages by hand.", True, url)
    note = f'"{quote}"' + (f" Cross-contact note: {extra}" if extra else "")
    return status, note, True, page


# --------------------------------------------------------------------------- csv update
def needs_work(row, redo=False):
    if not row.get("evidence_url", "").strip():
        return False
    if redo:
        who = row.get("checked_by", "").strip()
        return row.get("verified", "").lower() not in ("yes", "y", "true", "1") and (
            not who or who.startswith(MACHINE_PREFIXES))
    return not row.get("gf_status", "").strip() and not row.get("checked_by", "").strip()


def save_result(hsisid, url, updates, redo):
    """Re-read the CSV, update just this row, write it back (so a teammate's edits are not clobbered)."""
    with LOCK:
        rows, fields = gf_map.read_annotation_rows()
        for r in rows:
            if r.get("hsisid") == hsisid and r.get("evidence_url") == url and needs_work(r, redo):
                r.update(updates)
                break
        gf_map.write_annotation_rows(rows, fields)


def check_backend(backend):
    print(f"Checking backend {backend!r} ...")
    if backend == "rules":
        print("rules needs nothing: no model is called.")
        return
    reply = call_llm(backend, "You are a test. Reply with exactly the word OK and nothing else.", "Say OK.")
    print(f"Reply: {reply.strip()[:200]!r}")
    print("Backend works." if reply.strip() else "Empty reply: something is wrong.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=("rules", "claude", "apfel", "openai"),
                    default=os.environ.get("ENRICH_BACKEND", "claude"))
    ap.add_argument("--check", action="store_true", help="make one tiny test call to the backend, then exit")
    ap.add_argument("--list-models", action="store_true", help="(openai backend) list model IDs, then exit")
    ap.add_argument("--dry-run", action="store_true", help="fetch pages and show excerpts; call nothing, change nothing")
    ap.add_argument("--limit", type=int, help="process at most N rows")
    ap.add_argument("--workers", type=int, help="restaurants processed at the same time")
    ap.add_argument("--max-chars", type=int, help="characters of page text sent to the model")
    ap.add_argument("--redo", action="store_true", help="also reprocess rows a machine filled in earlier "
                                                       "(never human-verified rows)")
    args = ap.parse_args()
    backend = args.backend

    if args.list_models:
        code, payload = openai_call("GET", "/v1/models")
        if code != 200:
            sys.exit(f"HTTP {code}: {payload[:300]}")
        for m in json.loads(payload).get("data", []):
            print(m.get("id"))
        return
    if args.check:
        try:
            return check_backend(backend)
        except RuntimeError as e:
            sys.exit(f"Backend {backend!r} failed: {e}")

    limit = args.max_chars or BUDGET.get(backend, 12000)
    workers = max(1, 1 if args.dry_run else (args.workers or DEFAULT_WORKERS[backend]))
    rows, _ = gf_map.read_annotation_rows()
    todo = [r for r in rows if needs_work(r, args.redo)]
    if args.limit:
        todo = todo[:args.limit]
    how = "" if args.dry_run else f" with backend {backend}" + (f", {workers} at a time" if workers > 1 else "")
    print(f"{len(todo)} rows to process{how}")
    if not todo:
        print("Nothing to do: rows need an evidence_url, a blank gf_status and a blank checked_by "
              "(or use --redo for machine-filled rows).")
        return

    tally, done = {}, [0]

    def work(r):
        name, url, hs = r.get("name") or r.get("hsisid"), r["evidence_url"].strip(), r.get("hsisid", "")
        pages, problem = gather(url)
        ev_url, called = url, False
        if problem:
            if args.dry_run:
                return name, url, f"would mark unknown: {problem}"
            status, note = "unknown", problem[0].upper() + problem[1:] + "."
        elif args.dry_run:
            lines = [f"pages read: {len(pages)}"]
            for page_url, text in pages:
                ex, hits = excerpts(text)
                m = PATTERN.search(ex)
                around = ex[max(0, m.start() - 100): m.end() + 140] if m else ""
                lines.append(f"  {hits} mention(s) at {page_url}" + (f": {around!r}" if m else ""))
            return name, url, "\n      ".join(lines)
        else:
            try:
                if backend == "rules":
                    status, note, called, ev_url = classify_rules(pages, url)
                else:
                    status, note, called, ev_url = classify_llm(backend, name, url, pages, limit)
            except RuntimeError as e:
                return name, url, f"BACKEND ERROR (row left unchanged, rerun later): {e}"
        who = ("rules" if backend == "rules" else f"llm:{backend}") if (called or backend == "rules" and not problem) else "auto"
        src = "rules" if who == "rules" else ("llm" if who.startswith("llm") else "auto")
        save_result(hs, r["evidence_url"], {
            "gf_status": status, "verified": "no", "source": src, "evidence_url": ev_url,
            "evidence_note": note, "checked_by": who, "checked_on": date.today().isoformat()}, args.redo)
        with LOCK:
            tally[status] = tally.get(status, 0) + 1
        return name, url, f"-> {status}: {note[:150]}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, r) for r in todo]
        for fut in concurrent.futures.as_completed(futures):
            with LOCK:
                done[0] += 1
                n = done[0]
            try:
                name, url, msg = fut.result()
            except Exception as e:      # one bad site must not stop the run
                name, url, msg = "?", "", f"ERROR {type(e).__name__}: {e}"
            print(f"[{n}/{len(todo)}] {name}  {url}\n      {msg}", flush=True)

    if tally:
        print("\nDone: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
        print("These are machine suggestions (verified=no). Open each evidence_url, check the quote, then "
              "set verified=yes in annotations.csv.\nNext: python3 fetch_data.py && python3 gf_map.py")


if __name__ == "__main__":
    main()

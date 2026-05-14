#!/usr/bin/env python3
"""
Meta Pages client — publish, schedule and manage organic posts on Facebook Pages.

Usage: python3 meta_pages_client.py <command> [options]

Env vars required:
  META_SYSTEM_USER_TOKEN  — System User token (page tokens fetched dynamically)
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"


# ── Auth & HTTP ───────────────────────────────────────────────────────────────

def _get_token() -> str:
    token = os.environ.get("META_SYSTEM_USER_TOKEN", "").strip()
    if not token:
        print(json.dumps({"error": "META_SYSTEM_USER_TOKEN not set in .env"}))
        sys.exit(1)
    return token


def _get(path: str, params: dict = None, token_override: str = None) -> dict:
    token = token_override or _get_token()
    p = {"access_token": token, **(params or {})}
    url = f"{BASE_URL}/{path}?{urllib.parse.urlencode(p)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        print(json.dumps({"error": body}))
        sys.exit(1)


def _post_req(path: str, payload: dict, token_override: str = None) -> dict:
    token = token_override or _get_token()
    payload = {"access_token": token, **payload}
    data = urllib.parse.urlencode(payload).encode()
    url = f"{BASE_URL}/{path}"
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        print(json.dumps({"error": body}))
        sys.exit(1)


def _delete_req(path: str, token_override: str = None) -> dict:
    token = token_override or _get_token()
    data = urllib.parse.urlencode({"access_token": token}).encode()
    req = urllib.request.Request(f"{BASE_URL}/{path}", data=data, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        print(json.dumps({"error": body}))
        sys.exit(1)


# ── Page resolution ───────────────────────────────────────────────────────────

_pages_cache: list[dict] | None = None


def _get_pages() -> list[dict]:
    global _pages_cache
    if _pages_cache is None:
        result = _get("me/accounts", {"fields": "id,name,access_token,category,tasks", "limit": 50})
        _pages_cache = result.get("data", [])
    return _pages_cache


def _resolve_page(page_ref: str) -> tuple[str, str]:
    """Return (page_id, page_access_token) given a page ID or name substring."""
    pages = _get_pages()
    for p in pages:
        if p["id"] == page_ref:
            return p["id"], p["access_token"]
    ref_lower = page_ref.lower()
    matches = [p for p in pages if ref_lower in p["name"].lower()]
    if len(matches) == 1:
        return matches[0]["id"], matches[0]["access_token"]
    if len(matches) > 1:
        names = [f"{p['name']} ({p['id']})" for p in matches]
        print(json.dumps({"error": f"Ambiguous: '{page_ref}' matches {names}. Use the page ID."}))
        sys.exit(1)
    avail = [f"{p['name']} ({p['id']})" for p in pages]
    print(json.dumps({"error": f"Page not found: '{page_ref}'", "available": avail}))
    sys.exit(1)


def _parse_brt(time_str: str) -> int:
    """Parse YYYY-MM-DDTHH:MM:SS (BRT = UTC-3) and return UTC Unix timestamp."""
    dt_str = time_str.replace(" ", "T")
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        brt = timezone(timedelta(hours=-3))
        dt = dt.replace(tzinfo=brt)
    return int(dt.timestamp())


def _out(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_list_pages(args):
    pages = _get_pages()
    _out([{
        "id": p["id"],
        "name": p["name"],
        "category": p.get("category", ""),
        "can_post": "CREATE_CONTENT" in p.get("tasks", []),
        "can_manage": "MANAGE" in p.get("tasks", []),
    } for p in pages])


def cmd_post(args):
    page_id, page_token = _resolve_page(args.page)
    payload = {"message": args.message}
    if args.link:
        payload["link"] = args.link
    result = _post_req(f"{page_id}/feed", payload, token_override=page_token)
    _out({**result, "page_id": page_id, "status": "published"})


def cmd_schedule(args):
    page_id, page_token = _resolve_page(args.page)
    scheduled_ts = _parse_brt(args.time)
    now_ts = int(datetime.now(timezone.utc).timestamp())

    if scheduled_ts < now_ts + 600:
        print(json.dumps({"error": "Scheduled time must be at least 10 minutes in the future."}))
        sys.exit(1)
    if scheduled_ts > now_ts + 180 * 86400:
        print(json.dumps({"error": "Scheduled time cannot be more than 6 months in the future."}))
        sys.exit(1)

    payload = {
        "message": args.message,
        "published": "false",
        "scheduled_publish_time": str(scheduled_ts),
    }
    if args.link:
        payload["link"] = args.link

    result = _post_req(f"{page_id}/feed", payload, token_override=page_token)
    _out({
        **result,
        "page_id": page_id,
        "status": "scheduled",
        "scheduled_for_brt": args.time,
        "scheduled_unix_utc": scheduled_ts,
    })


def cmd_post_image(args):
    page_id, page_token = _resolve_page(args.page)
    payload = {"url": args.image_url}
    if args.message:
        payload["caption"] = args.message
    if args.no_story:
        payload["no_story"] = "true"
    result = _post_req(f"{page_id}/photos", payload, token_override=page_token)
    _out({**result, "page_id": page_id, "status": "published"})


def cmd_schedule_image(args):
    """Schedule a photo post for future publication."""
    page_id, page_token = _resolve_page(args.page)
    scheduled_ts = _parse_brt(args.time)
    now_ts = int(datetime.now(timezone.utc).timestamp())

    if scheduled_ts < now_ts + 600:
        print(json.dumps({"error": "Scheduled time must be at least 10 minutes in the future."}))
        sys.exit(1)

    payload = {
        "url": args.image_url,
        "published": "false",
        "scheduled_publish_time": str(scheduled_ts),
    }
    if args.message:
        payload["caption"] = args.message

    result = _post_req(f"{page_id}/photos", payload, token_override=page_token)
    _out({
        **result,
        "page_id": page_id,
        "status": "scheduled",
        "scheduled_for_brt": args.time,
        "scheduled_unix_utc": scheduled_ts,
    })


def cmd_list_posts(args):
    page_id, page_token = _resolve_page(args.page)
    params = {
        "fields": "id,message,story,created_time,full_picture,permalink_url,"
                  "likes.summary(true),comments.summary(true),shares",
        "limit": args.limit,
    }
    data = _get(f"{page_id}/feed", params, token_override=page_token)
    posts = []
    for p in data.get("data", []):
        posts.append({
            "id": p.get("id"),
            "message": (p.get("message") or p.get("story") or "")[:150],
            "created_time": p.get("created_time"),
            "url": p.get("permalink_url"),
            "image": p.get("full_picture"),
            "likes": p.get("likes", {}).get("summary", {}).get("total_count", 0),
            "comments": p.get("comments", {}).get("summary", {}).get("total_count", 0),
            "shares": p.get("shares", {}).get("count", 0),
        })
    _out(posts)


def cmd_list_scheduled(args):
    page_id, page_token = _resolve_page(args.page)
    params = {
        "fields": "id,message,scheduled_publish_time,created_time",
        "is_published": "false",
    }
    data = _get(f"{page_id}/promotable_posts", params, token_override=page_token)
    _out(data.get("data", []))


def cmd_delete_post(args):
    _, page_token = _resolve_page(args.page)
    result = _delete_req(args.post_id, token_override=page_token)
    _out({**result, "post_id": args.post_id, "status": "deleted"})


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Meta Pages — organic post management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  list-pages
  post           --page "Ateliê" --message "Olá mundo!"
  post           --page 1058585030667680 --message "Post com link" --link https://example.com
  schedule       --page "Ateliê" --message "Post agendado" --time "2026-05-15T18:00:00"
  post-image     --page "Ateliê" --image-url https://example.com/img.jpg --message "Legenda"
  schedule-image --page "Ateliê" --image-url https://example.com/img.jpg --time "2026-05-15T18:00:00"
  list-posts     --page "Ateliê" --limit 5
  list-scheduled --page "Ateliê"
  delete-post    --page "Ateliê" --post-id 1058585030667680_123456789
""")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list-pages", help="List all accessible pages")

    p = sub.add_parser("post", help="Publish a text post now")
    p.add_argument("--page", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--link")

    p = sub.add_parser("schedule", help="Schedule a text post (BRT timezone)")
    p.add_argument("--page", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--time", required=True, metavar="YYYY-MM-DDTHH:MM:SS",
                   help="Publication time in BRT (min +10min, max +6months)")
    p.add_argument("--link")

    p = sub.add_parser("post-image", help="Publish a photo post from a public URL")
    p.add_argument("--page", required=True)
    p.add_argument("--image-url", dest="image_url", required=True)
    p.add_argument("--message", help="Caption")
    p.add_argument("--no-story", dest="no_story", action="store_true")

    p = sub.add_parser("schedule-image", help="Schedule a photo post from a public URL")
    p.add_argument("--page", required=True)
    p.add_argument("--image-url", dest="image_url", required=True)
    p.add_argument("--message", help="Caption")
    p.add_argument("--time", required=True, metavar="YYYY-MM-DDTHH:MM:SS")

    p = sub.add_parser("list-posts", help="List recent published posts")
    p.add_argument("--page", required=True)
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("list-scheduled", help="List scheduled (unpublished) posts")
    p.add_argument("--page", required=True)

    p = sub.add_parser("delete-post", help="Delete a post by ID")
    p.add_argument("--page", required=True)
    p.add_argument("--post-id", dest="post_id", required=True)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    {
        "list-pages":     cmd_list_pages,
        "post":           cmd_post,
        "schedule":       cmd_schedule,
        "post-image":     cmd_post_image,
        "schedule-image": cmd_schedule_image,
        "list-posts":     cmd_list_posts,
        "list-scheduled": cmd_list_scheduled,
        "delete-post":    cmd_delete_post,
    }[args.command](args)


if __name__ == "__main__":
    main()

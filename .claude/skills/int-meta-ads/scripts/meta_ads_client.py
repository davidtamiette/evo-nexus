#!/usr/bin/env python3
"""
Meta Ads API client — campaigns, adsets, ads, metrics and campaign creation.

Usage: python3 meta_ads_client.py <command> [options]

Env vars required:
  META_SYSTEM_USER_TOKEN  — access token
  META_AD_ACCOUNT_ID      — e.g. act_724108254032481
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

# ── Config ───────────────────────────────────────────────────────────────────

API_VERSION = "v19.0"
BASE_URL     = f"https://graph.facebook.com/{API_VERSION}"


def _get_config() -> tuple[str, str]:
    token      = os.environ.get("META_SYSTEM_USER_TOKEN", "").strip()
    account_id = os.environ.get("META_AD_ACCOUNT_ID", "").strip()
    errors = []
    if not token:
        errors.append("META_SYSTEM_USER_TOKEN")
    if not account_id:
        errors.append("META_AD_ACCOUNT_ID")
    if errors:
        print(json.dumps({"error": f"Missing env vars: {', '.join(errors)}"}))
        sys.exit(1)
    return token, account_id


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _get(path: str, params: dict = None) -> dict:
    token, _ = _get_config()
    p = {"access_token": token, **(params or {})}
    url = f"{BASE_URL}/{path}?{urllib.parse.urlencode(p)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        print(json.dumps({"error": body}))
        sys.exit(1)


def _post(path: str, payload: dict) -> dict:
    token, _ = _get_config()
    payload["access_token"] = token
    data = urllib.parse.urlencode(payload).encode()
    url = f"{BASE_URL}/{path}"
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        print(json.dumps({"error": body}))
        sys.exit(1)


def _out(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_campaigns(args):
    """List campaigns — default: ACTIVE only."""
    token, account_id = _get_config()
    statuses = args.status if args.status else ["ACTIVE"]
    params = {
        "fields": "id,name,status,objective,daily_budget,lifetime_budget,spend_cap,start_time,stop_time,created_time",
        "filtering": json.dumps([{"field": "effective_status", "operator": "IN", "value": statuses}]),
        "limit": args.limit,
    }
    _out(_get(f"{account_id}/campaigns", params))


def cmd_adsets(args):
    """List adsets for a campaign."""
    params = {
        "fields": "id,name,status,daily_budget,lifetime_budget,targeting,billing_event,optimization_goal,start_time,end_time",
        "limit": args.limit,
    }
    _out(_get(f"{args.campaign_id}/adsets", params))


def cmd_ads(args):
    """List ads for a campaign or adset."""
    parent = args.adset_id or args.campaign_id
    params = {
        "fields": "id,name,status,creative{id,name,body,title,image_url},effective_status",
        "limit": args.limit,
    }
    _out(_get(f"{parent}/ads", params))


def cmd_insights(args):
    """Get performance metrics for a campaign, adset or ad."""
    since = args.since or (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    until = args.until or datetime.now().strftime("%Y-%m-%d")
    params = {
        "fields": "campaign_name,adset_name,ad_name,impressions,reach,clicks,spend,cpc,cpm,ctr,actions,cost_per_action_type",
        "time_range": json.dumps({"since": since, "until": until}),
        "level": args.level,
        "limit": args.limit,
    }
    _out(_get(f"{args.object_id}/insights", params))


def cmd_account_insights(args):
    """Get account-level insights (overview)."""
    _, account_id = _get_config()
    since = args.since or (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    until = args.until or datetime.now().strftime("%Y-%m-%d")
    params = {
        "fields": "impressions,reach,clicks,spend,cpc,cpm,ctr",
        "time_range": json.dumps({"since": since, "until": until}),
        "level": "account",
    }
    _out(_get(f"{account_id}/insights", params))


def cmd_create_campaign(args):
    """Create a new campaign."""
    _, account_id = _get_config()
    payload = {
        "name":      args.name,
        "objective": args.objective,
        "status":    args.status,
    }
    if args.daily_budget:
        payload["daily_budget"] = str(int(float(args.daily_budget) * 100))
    if args.lifetime_budget:
        payload["lifetime_budget"] = str(int(float(args.lifetime_budget) * 100))
        if not args.end_time:
            print(json.dumps({"error": "lifetime_budget requires --end-time (YYYY-MM-DD)"}))
            sys.exit(1)
        payload["end_time"] = args.end_time
    if args.spend_cap:
        payload["spend_cap"] = str(int(float(args.spend_cap) * 100))
    if args.special_ad_categories:
        payload["special_ad_categories"] = json.dumps(args.special_ad_categories)
    else:
        payload["special_ad_categories"] = json.dumps([])

    result = _post(f"{account_id}/campaigns", payload)
    _out(result)


def cmd_update_campaign(args):
    """Update campaign status or budget."""
    payload = {}
    if args.status:
        payload["status"] = args.status
    if args.daily_budget:
        payload["daily_budget"] = str(int(float(args.daily_budget) * 100))
    if args.name:
        payload["name"] = args.name
    if not payload:
        print(json.dumps({"error": "Nothing to update — provide --status, --daily-budget or --name"}))
        sys.exit(1)
    _out(_post(args.campaign_id, payload))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Meta Ads API client")
    sub = parser.add_subparsers(dest="command")

    # campaigns
    p = sub.add_parser("campaigns", help="List campaigns")
    p.add_argument("--status", nargs="+", default=["ACTIVE"],
                   choices=["ACTIVE", "PAUSED", "DELETED", "ARCHIVED"],
                   help="Filter by status (default: ACTIVE)")
    p.add_argument("--limit", type=int, default=50)

    # adsets
    p = sub.add_parser("adsets", help="List adsets for a campaign")
    p.add_argument("campaign_id")
    p.add_argument("--limit", type=int, default=50)

    # ads
    p = sub.add_parser("ads", help="List ads")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--campaign-id")
    g.add_argument("--adset-id")
    p.add_argument("--limit", type=int, default=50)

    # insights
    p = sub.add_parser("insights", help="Performance metrics for campaign/adset/ad")
    p.add_argument("object_id", help="Campaign, adset or ad ID")
    p.add_argument("--level", default="campaign", choices=["campaign", "adset", "ad"])
    p.add_argument("--since", help="YYYY-MM-DD (default: 30 days ago)")
    p.add_argument("--until", help="YYYY-MM-DD (default: today)")
    p.add_argument("--limit", type=int, default=50)

    # account-insights
    p = sub.add_parser("account-insights", help="Account-level overview metrics")
    p.add_argument("--since", help="YYYY-MM-DD (default: 30 days ago)")
    p.add_argument("--until", help="YYYY-MM-DD (default: today)")

    # create-campaign
    p = sub.add_parser("create-campaign", help="Create a new campaign")
    p.add_argument("--name", required=True)
    p.add_argument("--objective", required=True,
                   choices=["OUTCOME_AWARENESS", "OUTCOME_ENGAGEMENT", "OUTCOME_LEADS",
                             "OUTCOME_SALES", "OUTCOME_TRAFFIC", "OUTCOME_APP_PROMOTION"],
                   help="Campaign objective")
    p.add_argument("--status", default="PAUSED", choices=["ACTIVE", "PAUSED"])
    p.add_argument("--daily-budget", dest="daily_budget", help="Daily budget in BRL (e.g. 50.00)")
    p.add_argument("--lifetime-budget", dest="lifetime_budget", help="Lifetime budget in BRL")
    p.add_argument("--end-time", dest="end_time", help="End date YYYY-MM-DD (required with lifetime-budget)")
    p.add_argument("--spend-cap", dest="spend_cap", help="Spending cap in BRL")
    p.add_argument("--special-ad-categories", dest="special_ad_categories", nargs="*",
                   choices=["CREDIT", "EMPLOYMENT", "HOUSING", "ISSUES_ELECTIONS_POLITICS"],
                   help="Required for regulated industries (leave empty if none)")

    # update-campaign
    p = sub.add_parser("update-campaign", help="Update campaign status or budget")
    p.add_argument("campaign_id")
    p.add_argument("--status", choices=["ACTIVE", "PAUSED", "DELETED", "ARCHIVED"])
    p.add_argument("--daily-budget", dest="daily_budget", help="New daily budget in BRL")
    p.add_argument("--name", help="New campaign name")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    cmds = {
        "campaigns":        cmd_campaigns,
        "adsets":           cmd_adsets,
        "ads":              cmd_ads,
        "insights":         cmd_insights,
        "account-insights": cmd_account_insights,
        "create-campaign":  cmd_create_campaign,
        "update-campaign":  cmd_update_campaign,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()

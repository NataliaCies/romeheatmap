#!/usr/bin/env python3
"""Post-deployment healthcheck.

Usage:
    python scripts/healthcheck.py https://your-api.up.railway.app
    python scripts/healthcheck.py https://your-api.up.railway.app --full
"""
import argparse
import asyncio
import sys
import httpx

CHECKS = []


def check(name):
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn
    return decorator


@check("API reachable")
async def check_reachable(client, base):
    r = await client.get(f"{base}/api/v1/health", timeout=10)
    assert r.status_code == 200
    return f"status={r.json()['status']}"


@check("Redis connected")
async def check_redis(client, base):
    r = await client.get(f"{base}/api/v1/health", timeout=10)
    s = r.json().get("services", {}).get("redis", "unknown")
    assert s == "ok"
    return "redis=ok"


@check("Districts list")
async def check_districts(client, base):
    r = await client.get(f"{base}/api/v1/climate/districts", timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert len(d) == 10
    return f"{len(d)} districts"


@check("Overview endpoint")
async def check_overview(client, base):
    r = await client.get(f"{base}/api/v1/climate/overview", timeout=30)
    assert r.status_code == 200
    data = r.json()
    source = data.get("data_source", "?")
    tmax = data.get("tmax_celsius", "?")
    return f"source={source} tmax={tmax}\u00b0C"


@check("District detail")
async def check_district(client, base):
    r = await client.get(f"{base}/api/v1/climate/districts/centro", timeout=30)
    assert r.status_code == 200
    data = r.json()
    return f"LST={data['mean_lst_celsius']}\u00b0C score={data['livability_score']}"


@check("Compare endpoint")
async def check_compare(client, base):
    r = await client.get(f"{base}/api/v1/climate/compare?a=centro&b=villa_borghese", timeout=30)
    assert r.status_code == 200
    return f"\u0394 LST={r.json()['delta_lst_celsius']:+.1f}\u00b0C"


@check("Cache stats")
async def check_cache(client, base):
    r = await client.get(f"{base}/api/v1/cache/stats", timeout=10)
    d = r.json()
    return f"hits={d['hits']} rate={d['hit_rate_pct']}%"


@check("Satellite status")
async def check_satellite(client, base):
    r = await client.get(f"{base}/api/v1/satellite/status", timeout=15)
    d = r.json()
    if not d.get("configured"):
        return "\u26a0 Copernicus not configured (fallback only)"
    if not d.get("authenticated"):
        return f"\u26a0 Auth failed: {d.get('error_message', '')[:40]}"
    return f"\u2713 configured, {d.get('scenes_last_30_days', 0)} scenes last 30 days"


async def run_checks(base_url: str, full: bool) -> int:
    base = base_url.rstrip("/")
    print(f"\n{'='*55}")
    print(f"  Rome Climate API \u2014 Post-Deployment Healthcheck")
    print(f"  Target: {base}")
    print(f"{'='*55}\n")
    checks = CHECKS if full else CHECKS[:5]
    failures = 0
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for name, fn in checks:
            try:
                detail = await fn(client, base)
                print(f"  \u2705 {name:<30} {detail}")
            except Exception as exc:
                print(f"  \u274c {name:<30} {exc}")
                failures += 1
    total = len(checks)
    print(f"\n  {'All checks passed' if failures == 0 else f'{failures}/{total} failed'}\n")
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post-deployment healthcheck")
    parser.add_argument("url", help="API base URL e.g. https://your-api.up.railway.app")
    parser.add_argument("--full", action="store_true", help="Run all checks including satellite")
    args = parser.parse_args()
    failures = asyncio.run(run_checks(args.url, args.full))
    sys.exit(0 if failures == 0 else 1)

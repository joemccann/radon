"""Bounded originating-publisher collectors. Missing evidence never creates a zero."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

URLS = {
    "openrouter": "https://openrouter.ai/api/v1/datasets/rankings-daily",
    "vercel": "https://vercel.com/api/ai/leaderboard-export",
    "gpu-rental": "https://gpurentalprices.com/api/latest.json",
    "artificial-analysis": "https://artificialanalysis.ai/api/v2/data/llms/models",
    "eia": "https://api.eia.gov/v2/electricity/rto/region-sub-ba-data/data/",
    "vast": "https://console.vast.ai/api/v0/bundles/",
}
ISSUERS = {
    "MSFT": "0000789019",
    "AMZN": "0001018724",
    "GOOGL": "0001652044",
    "META": "0001326801",
    "ORCL": "0001341439",
    "NVDA": "0001045810",
    "DELL": "0001571996",
}
FACT_TAGS = {
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "ProceedsFromSaleOfPropertyPlantAndEquipment",
    "InventoryNet",
    "AccountsReceivableNetCurrent",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "LongTermDebtCurrent",
    "LongTermDebtNoncurrent",
    "CashAndCashEquivalentsAtCarryingValue",
}


class SourceError(ValueError):
    """Safe fixed-message collection failure; never exposes HTTP bodies or keys."""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise SourceError("Invalid public evidence URL")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def number(value):
    if isinstance(value, bool):
        raise SourceError("Boolean is not a measurement")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise SourceError("Non-finite or negative source measurement")
    return parsed


def observation(
    source,
    indicator,
    series,
    value,
    unit,
    start,
    end,
    digest,
    fetched,
    *,
    published=None,
    metadata=None,
    url=None,
    cohort="publisher-v1",
    measurement="observed",
    methodology_version="1",
):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SourceError("Measurement must be a finite number")
    return dict(
        indicator_id=indicator,
        series_id=series,
        source_id=source,
        value=value,
        unit=unit,
        period_start=start,
        period_end=end,
        published_at=published,
        fetched_at=fetched,
        source_url=public_url(url or URLS[source]),
        raw_hash=digest,
        methodology_version=methodology_version,
        cohort_version=cohort,
        lineage_group="gpu-aggregator" if source == "gpu-rental" else source,
        measurement=measurement,
        metadata=metadata or {},
    )


class Transport:
    """Single-process request ceiling; persistent account-wide local OR budget."""

    def __init__(self, archive: Path, *, session=None, max_requests=100, timeout=25, budget_path=None):
        self.archive = Path(archive)
        self.session = session or requests.Session()
        self.max_requests = max_requests
        self.requests = 0
        self.timeout = timeout
        self.deadline = time.monotonic() + 500
        self.budget_path = Path(budget_path or Path.home() / ".radon/ai-cycle/openrouter-budget.json")

    def _or_budget(self):
        import fcntl

        self.budget_path.parent.mkdir(parents=True, exist_ok=True)
        with self.budget_path.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            content = handle.read()
            try:
                state = json.loads(content) if content else {}
            except ValueError:
                raise SourceError("OpenRouter local quota ledger is invalid; repair before retry") from None
            today = datetime.now(timezone.utc).date().isoformat()
            used = state.get("used", 0) if state.get("date") == today else 0
            if used >= 450:
                raise SourceError("Local OpenRouter daily request reserve reached; retry tomorrow")
            delay = 2.1 - (time.time() - state.get("last_request", 0))
            if delay > 0:
                time.sleep(delay)
            handle.seek(0)
            handle.truncate(0)
            json.dump({"date": today, "used": used + 1, "last_request": time.time()}, handle)
            handle.truncate()

    def fetch(self, url, *, params=None, headers=None, body=None):
        if time.monotonic() >= self.deadline:
            raise SourceError("Per-run time budget exhausted")
        if self.requests >= self.max_requests:
            raise SourceError("Per-run request budget exhausted")
        if urlsplit(url).hostname == "openrouter.ai":
            self._or_budget()
        self.requests += 1
        try:
            response = self.session.request(
                "POST" if body is not None else "GET",
                url,
                params=params,
                headers=headers,
                json=body,
                timeout=min(self.timeout, max(1, self.deadline - time.monotonic())),
                stream=True,
            )
            if response.status_code != 200:
                raise SourceError(f"Publisher returned HTTP {response.status_code}")
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                if time.monotonic() >= self.deadline:
                    raise SourceError("Per-run time budget exhausted")
                size += len(chunk)
                if size > 20_000_000:
                    raise SourceError("Publisher response exceeds 20 MB limit")
                chunks.append(chunk)
            raw = b"".join(chunks)
            payload = json.loads(raw)
        except requests.RequestException:
            raise SourceError("Publisher transport failed") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise SourceError("Publisher response is not JSON") from None
        finally:
            if "response" in locals():
                response.close()
        digest = hashlib.sha256(raw).hexdigest()
        self.archive.mkdir(parents=True, exist_ok=True)
        path = self.archive / (digest + ".json")
        if not path.exists():
            path.write_bytes(raw)
        return payload, digest, now_iso()


def parse_openrouter(payload, digest, fetched, start, end):
    result, seen = [], set()
    for row in payload["data"]:
        day, model = row["date"], row["model_permaslug"]
        if not start <= day <= end or day >= fetched[:10]:
            continue
        if (day, model) in seen:
            raise SourceError("Duplicate daily model row")
        seen.add((day, model))
        tokens = row["total_tokens"]
        if isinstance(tokens, bool) or not str(tokens).isdigit():
            raise SourceError("Token count must be a non-negative integer")
        result.append(
            observation(
                "openrouter",
                "D1",
                model,
                int(tokens),
                "tokens",
                day,
                day,
                digest,
                fetched,
                metadata={
                    "entity": model,
                    "label": model,
                    "paid_classification": "unknown"
                    if model == "other"
                    else ("free_variant" if model.endswith(":free") else "visible_non_free_variant"),
                    "publisher_as_of": payload.get("meta", {}).get("as_of"),
                    "truncated": model != "other",
                    "private_traffic_excluded": True,
                    "license": "CC BY 4.0",
                },
            )
        )
    return result


def parse_vercel(payload, digest, fetched, start, end):
    result, sums, seen = [], defaultdict(float), set()
    dataset = payload["dataset"]
    if dataset not in ("models", "labs"):
        raise SourceError("Vercel dataset is not a historical share series")
    for row in payload["rows"]:
        day, metric = row["date"], row["metric"]
        if metric not in ("tokens", "requests", "spend") or payload["modality"] != "text":
            raise SourceError("Vercel metric or modality schema changed")
        if not start <= day <= end or day >= fetched[:10]:
            continue
        key = (day, metric, row["name"])
        if key in seen:
            raise SourceError("Duplicate Vercel share row")
        seen.add(key)
        share = number(row["share_percent"])
        if share > 100:
            raise SourceError("Share exceeds 100 percent")
        sums[(day, metric)] += share
        result.append(
            observation(
                "vercel",
                "D3",
                f"{dataset}.{metric}.{row['name']}",
                share,
                "%",
                day,
                day,
                digest,
                fetched,
                metadata={
                    "entity": row["name"],
                    "label": f"{row['name']} {metric} share",
                    "metric": metric,
                    "dataset": dataset,
                    "modality": payload["modality"],
                    "license": payload.get("license"),
                    "definition": "Gateway share; not market volume or dollar ASP",
                },
            )
        )
    if any(abs(total - 100) > 0.1 for total in sums.values()):
        raise SourceError("Vercel daily share totals do not reconcile")
    return result


def parse_gpu(payload, digest, fetched):
    day = payload["date"]
    result = []
    for row in payload["offers"]:
        if not any(chip in row["gpu"].lower() for chip in ("h100", "h200", "b200")):
            continue
        cohort = {
            key: row.get(key)
            for key in ("provider", "gpu", "vram_gb", "kind", "region", "gpu_count", "interconnect", "tenancy", "term")
        }
        eligible = all(cohort[key] is not None for key in cohort)
        series = hashlib.sha256(json.dumps(cohort, sort_keys=True).encode()).hexdigest()[:20]
        result.append(
            observation(
                "gpu-rental",
                "C1",
                series,
                number(row["usd_hr"]),
                "USD/GPU-hour",
                day,
                day,
                digest,
                fetched,
                published=payload.get("generated_at"),
                cohort=series,
                metadata={
                    **cohort,
                    "label": f"{row['provider']} {row['gpu']} asking price",
                    "upstream_url": public_url(row["source_url"]),
                    "upstream_fetched_at": row.get("fetched_at"),
                    "cohort_eligible": eligible,
                    "definition": "Published asking price; incomplete cohorts excluded from matched-price index",
                    "license": "CC BY 4.0",
                },
            )
        )
    return result


def parse_sec(payload, digest, fetched, entity, start, end):
    result = []
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{ISSUERS[entity]}.json"
    for tag, item in payload["facts"].get("us-gaap", {}).items():
        if tag not in FACT_TAGS:
            continue
        for fact in item.get("units", {}).get("USD", []):
            if (
                fact.get("form") not in ("10-K", "10-Q")
                or not start <= fact["end"] <= end
                or fact.get("filed", "9999") > fetched[:10]
            ):
                continue
            result.append(
                observation(
                    "sec",
                    "F1" if entity not in ("NVDA", "DELL") else "H2",
                    f"{entity}.{tag}",
                    fact["val"],
                    "USD",
                    fact.get("start", fact["end"]),
                    fact["end"],
                    digest,
                    fetched,
                    published=fact["filed"] + "T23:59:59Z",
                    url=url,
                    cohort="sec-us-gaap-v1",
                    metadata={
                        "entity": entity,
                        "label": f"{entity} {item.get('label', tag)}",
                        "tag": tag,
                        "form": fact["form"],
                        "accession": fact["accn"],
                        "fiscal_year": fact.get("fy"),
                        "fiscal_period": fact.get("fp"),
                        "duration": "ytd" if fact.get("start") else "instant",
                        "verified_statement": entity in ("MSFT", "AMZN", "GOOGL", "META", "ORCL")
                        and tag
                        in (
                            "NetCashProvidedByUsedInOperatingActivities",
                            "PaymentsToAcquireProductiveAssets"
                            if entity == "AMZN"
                            else "PaymentsToAcquirePropertyPlantAndEquipment",
                        ),
                        "statement_check": "Reviewed issuer taxonomy mapping; later rows schema-validated, not individually audited",
                        "mapping_version": "cash-flow-tags-v1",
                        "cash_capex_definition": "Gross productive-asset purchases, before sales and incentives"
                        if entity == "AMZN"
                        else "Cash PP&E purchases; finance lease principal excluded",
                        "publication_precision": "day; conservative end-of-day",
                        "definition": item.get("description", ""),
                        "taxonomy": "us-gaap",
                    },
                )
            )
    return result


def parse_aa(payload, digest, fetched, basket):
    if not basket:
        raise SourceError("Fixed model membership must be explicitly configured")
    models = {m["slug"]: m for m in payload["data"]}
    if not set(basket).issubset(models):
        raise SourceError("Required fixed-model basket member missing")
    cohort = hashlib.sha256(json.dumps(sorted(basket)).encode()).hexdigest()[:16]
    result = []
    for slug in basket:
        price = models[slug]["pricing"]
        inp, out = number(price["price_1m_input_tokens"]), number(price["price_1m_output_tokens"])
        result.append(
            observation(
                "artificial-analysis",
                "C3",
                slug,
                inp + out,
                "USD/task-bundle",
                fetched[:10],
                fetched[:10],
                digest,
                fetched,
                cohort=cohort,
                metadata={
                    "entity": slug,
                    "label": slug,
                    "input_price": inp,
                    "output_price": out,
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "required_members": sorted(basket),
                    "cohort_members": sorted(basket),
                    "coverage_numerator": len(basket),
                    "coverage_denominator": len(basket),
                    "definition": "Fixed 1M input + 1M output uncached list-price bundle",
                    "license": "Internal attribution; redistribution rights must be confirmed",
                },
            )
        )
    return result


def parse_eia(payload, digest, fetched):
    result = []
    response = payload["response"]
    if int(response.get("total", len(response["data"]))) > len(response["data"]):
        raise SourceError("EIA window truncated; use a shorter window")
    for row in response["data"]:
        if row["parent"] != "PJM" or row["subba"] != "DOM" or row.get("value-units") != "megawatthours":
            raise SourceError("EIA geography or unit changed")
        # Form EIA-930 General Instructions (p.3): timestamps are hour ENDING UTC.
        # https://www.eia.gov/survey/form/eia_930/instructions.pdf
        end = datetime.strptime(row["period"], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
        start = end - timedelta(hours=1)
        if end > datetime.fromisoformat(fetched.replace("Z", "+00:00")):
            continue
        result.append(
            observation(
                "eia",
                "P1",
                "PJM.DOM.hourly-load",
                number(row["value"]),
                "MWh/hour",
                start.isoformat(),
                end.isoformat(),
                digest,
                fetched,
                methodology_version="eia-hour-ending-v2",
                metadata={
                    "label": "DOM observed hourly grid load",
                    "timestamp_convention": "hour-ending UTC",
                    "publisher_period": row["period"],
                    "methodology_source_url": "https://www.eia.gov/survey/form/eia_930/instructions.pdf",
                    "supersedes_methodology_version": "1",
                    "parent": "PJM",
                    "subba": "DOM",
                    "definition": "One-hour energy; numerically equivalent to interval-average MW. Not AI load.",
                    "weather_adjusted": False,
                },
            )
        )
    return result


def parse_disclosures(payload, digest, fetched):
    """Explicit reviewed semantic ingestion; no memo values or guessed tags."""
    result = []
    for item in payload["observations"]:
        if (
            item.get("verified") is not True
            or not item.get("verified_by")
            or not item.get("source_excerpt")
            or not item.get("definition")
        ):
            raise SourceError("Disclosure requires reviewer, definition and cited excerpt")
        if item["indicator_id"] not in ("H1", "H2", "F1", "F2", "P2"):
            raise SourceError("Unsupported disclosure indicator")
        published = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        if published.tzinfo is None or published > datetime.fromisoformat(fetched.replace("Z", "+00:00")):
            raise SourceError("Disclosure publication must be a known past UTC timestamp")
        result.append(
            observation(
                "issuer-disclosures",
                item["indicator_id"],
                item["series_id"],
                item["value"],
                item["unit"],
                item["period_start"],
                item["period_end"],
                digest,
                fetched,
                published=item["published_at"],
                url=item["source_url"],
                cohort=item.get("cohort_version", "issuer-disclosure-v1"),
                measurement=item.get("measurement", "observed"),
                metadata={key: value for key, value in item.items() if key not in ("value", "source_url")},
            )
        )
    return result


def parse_apps(payload, digest, fetched, day):
    result = []
    if day >= fetched[:10]:
        raise SourceError("App history requires a completed UTC day")
    seen = set()
    for item in payload["data"]:
        entity = str(item["app_id"])
        if entity in seen:
            raise SourceError("Duplicate app identifier")
        seen.add(entity)
        for field, unit in [("total_tokens", "tokens"), ("total_requests", "requests")]:
            if isinstance(item[field], bool) or not str(item[field]).isdigit():
                raise SourceError("App count must be a non-negative integer")
            result.append(
                observation(
                    "openrouter",
                    "D2",
                    f"app.{entity}.{field}",
                    int(item[field]),
                    unit,
                    day,
                    day,
                    digest,
                    fetched,
                    url="https://openrouter.ai/api/v1/datasets/app-rankings",
                    metadata={
                        "entity": entity,
                        "label": item["app_name"] + " " + unit,
                        "rank": item["rank"],
                        "truncated": True,
                        "population": "Returned top-100 public app cohort only",
                        "publisher_as_of": payload.get("meta", {}).get("as_of"),
                        "license": "CC BY 4.0",
                    },
                )
            )
    return result


def parse_vast(payload, digest, fetched):
    import statistics

    offers = payload["offers"]
    if not isinstance(offers, list):
        raise SourceError("Vast offer schema changed")
    seen, machines, prices = set(), {}, []
    for item in offers:
        if item["id"] in seen:
            raise SourceError("Duplicate Vast offer identifier")
        seen.add(item["id"])
        if (
            item.get("verification") != "verified"
            or not str(item.get("geolocation", "")).endswith("US")
            or item.get("is_bid") is not False
            or item.get("rentable") is not True
            or item.get("rented") is True
            or number(item["reliability"]) < 0.99
            or item["gpu_name"] != "H100_SXM"
        ):
            raise SourceError("Vast response violated fixed cohort")
        count = number(item["num_gpus"])
        if count <= 0 or not count.is_integer():
            raise SourceError("Invalid GPU bundle size")
        machines[item["machine_id"]] = max(machines.get(item["machine_id"], 0), count)
        prices.append(number(item["dph_total"]) / count)
    metadata = {
        "label": "Verified rentable H100 SXM listings",
        "definition": "Available listed supply, never fleet utilization; same-machine alternative bundles deduplicated",
        "truncated": len(offers) >= 1000,
        "reliability_min": 0.99,
        "geography": "US",
        "gpu": "H100_SXM",
    }
    values = [
        ("offers", len(offers), "offers"),
        ("machines", len(machines), "machines"),
        ("gpus", sum(machines.values()), "GPUs"),
    ]
    if prices:
        values.append(("median-ask", statistics.median(prices), "USD/GPU-hour"))
    return [
        observation(
            "vast",
            "C2",
            "US.H100_SXM." + metric,
            value,
            unit,
            fetched[:10],
            fetched[:10],
            digest,
            fetched,
            cohort="US.H100_SXM.verified.99.on-demand.v1",
            metadata={**metadata, "label": metadata["label"] + " " + metric},
        )
        for metric, value, unit in values
    ]


def collect_source(source, transport, start, end, *, env=None, basket=()):
    env = os.environ if env is None else env
    if source == "openrouter":
        key = env.get("OPENROUTER_API_KEY")
        if not key:
            raise SourceError("OPENROUTER_API_KEY is not configured")
        rows = parse_openrouter(
            *transport.fetch(
                URLS[source], params={"start_date": start, "end_date": end}, headers={"Authorization": f"Bearer {key}"}
            ),
            start,
            end,
        )
        current = date.fromisoformat(start)
        while current <= date.fromisoformat(end):
            day = current.isoformat()
            rows.extend(
                parse_apps(
                    *transport.fetch(
                        "https://openrouter.ai/api/v1/datasets/app-rankings",
                        params={"start_date": day, "end_date": day, "sort": "popular", "limit": 100},
                        headers={"Authorization": f"Bearer {key}"},
                    ),
                    day,
                )
            )
            current += timedelta(days=1)
        return rows
    if source == "vercel":
        result = []
        for dataset in ("models", "labs"):
            result.extend(
                parse_vercel(
                    *transport.fetch(
                        URLS[source], params={"dataset": dataset, "modality": "text", "from": start, "to": end}
                    ),
                    start,
                    end,
                )
            )
        return result
    if source == "gpu-rental":
        return parse_gpu(*transport.fetch(URLS[source]))
    if source == "artificial-analysis":
        key = env.get("ARTIFICIAL_ANALYSIS_API_KEY")
        if not key:
            raise SourceError("ARTIFICIAL_ANALYSIS_API_KEY is not configured")
        return parse_aa(*transport.fetch(URLS[source], headers={"x-api-key": key}), basket)
    if source == "vast":
        key = env.get("VAST_API_KEY")
        if not key:
            raise SourceError("VAST_API_KEY is not configured")
        return parse_vast(
            *transport.fetch(
                URLS[source],
                headers={"Authorization": f"Bearer {key}"},
                body={
                    "limit": 1000,
                    "type": "ondemand",
                    "verified": {"eq": True},
                    "rentable": {"eq": True},
                    "rented": {"eq": False},
                    "reliability": {"gte": 0.99},
                    "gpu_name": {"eq": "H100_SXM"},
                    "geolocation": {"in": ["US"]},
                },
            )
        )
    if source == "sec":
        identity = env.get("SEC_USER_AGENT")
        if not identity or "@" not in identity:
            raise SourceError("SEC_USER_AGENT must identify the application and contact email")
        rows = []
        for entity, cik in ISSUERS.items():
            rows.extend(
                parse_sec(
                    *transport.fetch(
                        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers={"User-Agent": identity}
                    ),
                    entity,
                    start,
                    end,
                )
            )
            time.sleep(0.15)
        return rows
    if source == "eia":
        key = env.get("EIA_API_KEY")
        if not key:
            raise SourceError("EIA_API_KEY is not configured")
        return parse_eia(
            *transport.fetch(
                URLS[source],
                params={
                    "api_key": key,
                    "frequency": "hourly",
                    "data[0]": "value",
                    "facets[parent][]": "PJM",
                    "facets[subba][]": "DOM",
                    "start": start + "T01",
                    "end": (date.fromisoformat(end) + timedelta(days=1)).isoformat() + "T00",
                    "length": 5000,
                    "sort[0][column]": "period",
                    "sort[0][direction]": "desc",
                },
            )
        )
    raise SourceError(
        {
            "portkey": "Automated access and completed-day methodology unverified",
            "noaa": "Weather station coverage unverified; residual disabled",
            "vast": "Authenticated offer schema and entitlement not verified",
            "lambda": "Direct HTML pricing semantics require a verified cohort import",
            "issuer-disclosures": "Requires reviewed issuer disclosure import; unattended release retrieval is not enabled",
        }.get(source, "Collector is not configured")
    )

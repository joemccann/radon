"""Bounded originating-publisher collectors. Missing evidence never creates a zero."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from email.utils import parsedate_to_datetime
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
    "noaa": "https://www.ncei.noaa.gov/access/services/data/v1",
    "open-design-arena": "https://open-design.ai/llm-arena-for-design/",
}
GPU_HISTORY_INDEX = "https://api.github.com/repos/adriannutiu/gpu-rental-prices/contents/data/snapshots"
NOAA_DOM_STATIONS = (
    "USW00093738",  # Washington Dulles
    "USW00013743",  # Washington National
    "USW00093721",  # Baltimore/Washington
    "USW00013740",  # Richmond
)
ISSUERS = {
    "MSFT": "0000789019",
    "AMZN": "0001018724",
    "GOOGL": "0001652044",
    "META": "0001326801",
    "ORCL": "0001341439",
    "NVDA": "0001045810",
    "DELL": "0001571996",
    "SMCI": "0001375365",
    "MU": "0000723125",
    "TSM": "0001046179",
}
HARDWARE_ISSUERS = {"NVDA", "DELL", "SMCI", "MU", "TSM"}
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
IFRS_FACT_TAGS = {
    "CashFlowsFromUsedInOperatingActivities",
    "Inventories",
    "CurrentTradeReceivables",
    "Revenue",
    "RevenueFromContractsWithCustomers",
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
}


class SourceError(ValueError):
    """Safe fixed-message collection failure; never exposes HTTP bodies or keys."""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def archive_raw(archive: Path, raw: bytes) -> str:
    """Durably publish raw evidence only after its digest is verified."""
    digest = hashlib.sha256(raw).hexdigest()
    archive = Path(archive)
    archive.mkdir(parents=True, exist_ok=True)
    path = archive / f"{digest}.json"
    try:
        if path.read_bytes() == raw:
            return digest
    except FileNotFoundError:
        pass

    fd, temporary = tempfile.mkstemp(dir=archive, prefix=".raw-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if hashlib.sha256(Path(temporary).read_bytes()).hexdigest() != digest:
            raise SourceError("Raw evidence digest verification failed")
        os.replace(temporary, path)
        directory = os.open(archive, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return digest


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


def temperature_celsius(value):
    if isinstance(value, bool):
        raise SourceError("Boolean is not a temperature measurement")
    parsed = float(value)
    if not math.isfinite(parsed) or not -100 <= parsed <= 100:
        raise SourceError("Temperature measurement is outside physical validation bounds")
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

    def _download(self, url, *, params=None, headers=None, body=None):
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
            return b"".join(chunks), dict(getattr(response, "headers", {}) or {})
        except requests.RequestException:
            raise SourceError("Publisher transport failed") from None
        finally:
            if "response" in locals():
                response.close()

    def fetch(self, url, *, params=None, headers=None, body=None):
        raw, _headers = self._download(url, params=params, headers=headers, body=body)
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise SourceError("Publisher response is not JSON") from None
        digest = archive_raw(self.archive, raw)
        return payload, digest, now_iso()

    def fetch_html(self, url, *, headers=None):
        raw, response_headers = self._download(url, headers=headers)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SourceError("Publisher response is not HTML text") from None
        digest = archive_raw(self.archive, raw)
        return text, digest, now_iso(), {"last_modified": response_headers.get("Last-Modified")}


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
    taxonomies = (("us-gaap", FACT_TAGS, ("10-K", "10-Q")), ("ifrs-full", IFRS_FACT_TAGS, ("20-F",)))
    for taxonomy, tags, forms in taxonomies:
        for tag, item in payload["facts"].get(taxonomy, {}).items():
            if tag not in tags:
                continue
            for fact in item.get("units", {}).get("USD", []):
                if (
                    fact.get("form") not in forms
                    or not start <= fact["end"] <= end
                    or fact.get("filed", "9999") > fetched[:10]
                ):
                    continue
                result.append(
                    observation(
                        "sec",
                        "H2" if entity in HARDWARE_ISSUERS else "F1",
                        f"{entity}.{tag}",
                        fact["val"],
                        "USD",
                        fact.get("start", fact["end"]),
                        fact["end"],
                        digest,
                        fetched,
                        published=fact["filed"] + "T23:59:59Z",
                        url=url,
                        cohort=f"sec-{taxonomy}-v1",
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
                            "taxonomy": taxonomy,
                        },
                    )
                )
    return result


def parse_aa(payload, digest, fetched, basket):
    if not basket:
        raise SourceError("Fixed model membership must be explicitly configured")
    models = {m["slug"]: m for m in payload["data"]}
    if len(models) != len(payload["data"]):
        raise SourceError("Artificial Analysis model slugs must be unique")
    if not set(basket).issubset(models):
        raise SourceError("Required fixed-model basket member missing")
    selected = [models[slug] for slug in basket]
    model_ids = [model.get("id") for model in selected]
    if any(not isinstance(model_id, str) or not model_id for model_id in model_ids):
        raise SourceError("Artificial Analysis stable model identity missing")
    if len(model_ids) != len(set(model_ids)):
        raise SourceError("Artificial Analysis stable model identities must be unique")
    cohort_members = sorted(model_ids)
    cohort = hashlib.sha256(json.dumps(cohort_members).encode()).hexdigest()[:16]
    result = []
    for slug, model in zip(basket, selected):
        price = model["pricing"]
        inp, out = number(price["price_1m_input_tokens"]), number(price["price_1m_output_tokens"])
        model_id = model["id"]
        creator = model.get("model_creator") if isinstance(model.get("model_creator"), dict) else {}
        result.append(
            observation(
                "artificial-analysis",
                "C3",
                model_id,
                inp + out,
                "USD/task-bundle",
                fetched[:10],
                fetched[:10],
                digest,
                fetched,
                cohort=cohort,
                metadata={
                    "entity": slug,
                    "label": model.get("name") or slug,
                    "model_id": model_id,
                    "model_slug": slug,
                    "creator_id": creator.get("id"),
                    "input_price": inp,
                    "output_price": out,
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "required_members": cohort_members,
                    "cohort_members": cohort_members,
                    "coverage_numerator": len(basket),
                    "coverage_denominator": len(basket),
                    "definition": "Fixed 1M input + 1M output uncached list-price bundle",
                    "license": "Internal attribution; redistribution rights must be confirmed",
                },
                methodology_version="aa-frontier-id-v1",
            )
        )
    return result


def parse_eia(payload, digest, fetched):
    by_day = defaultdict(list)
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
        by_day[start.date().isoformat()].append(number(row["value"]))
    result = []
    for day, values in sorted(by_day.items()):
        common = {
            "timestamp_convention": "source hours ending UTC; grouped by interval start date",
            "methodology_source_url": "https://www.eia.gov/survey/form/eia_930/instructions.pdf",
            "parent": "PJM",
            "subba": "DOM",
            "source_observations": len(values),
            "definition": "Daily summary of observed one-hour energy; numerically equivalent to interval-average MW. Not AI load.",
            "weather_adjusted": False,
        }
        for series, value, label in (
            ("PJM.DOM.daily-average-load", sum(values) / len(values), "DOM daily average grid load"),
            ("PJM.DOM.daily-peak-load", max(values), "DOM daily peak grid load"),
        ):
            result.append(
                observation(
                    "eia",
                    "P1",
                    series,
                    value,
                    "MWh/hour",
                    day,
                    day,
                    digest,
                    fetched,
                    methodology_version="eia-daily-hour-ending-v3",
                    metadata={**common, "label": label},
                )
            )
    return result


def parse_noaa(payload, digest, fetched, start=None, end=None):
    """Parse fixed DOM-area daily station temperatures from NCEI."""
    if not isinstance(payload, list):
        raise SourceError("NOAA daily summary schema changed")
    seen, result = set(), []
    for row in payload:
        station, day = row.get("STATION"), row.get("DATE")
        if station not in NOAA_DOM_STATIONS or not isinstance(day, str):
            raise SourceError("NOAA station cohort changed")
        if (start and day < start) or (end and day > end):
            continue
        if (station, day) in seen:
            raise SourceError("Duplicate NOAA station day")
        seen.add((station, day))
        if row.get("TMAX") in (None, "") or row.get("TMIN") in (None, ""):
            continue
        maximum = temperature_celsius(row["TMAX"])
        minimum = temperature_celsius(row["TMIN"])
        mean = (maximum + minimum) / 2
        result.append(
            observation(
                "noaa",
                "P1",
                f"{station}.daily-mean-temperature",
                mean,
                "degC",
                day,
                day,
                digest,
                fetched,
                url=URLS["noaa"],
                cohort="pjm-dom-weather-control-v1",
                metadata={
                    "label": f"{row.get('NAME') or station} daily mean temperature",
                    "station": station,
                    "tmax_c": maximum,
                    "tmin_c": minimum,
                    "definition": "Mean of NOAA daily maximum and minimum air temperature; weather control only.",
                    "license": "U.S. government public data",
                },
                methodology_version="noaa-daily-summary-v1",
            )
        )
    if payload and not result:
        raise SourceError("NOAA temperature coverage is incomplete")
    return result


RAMP_SPEND_SERIES = (
    ("spend.median_pepm", "median_pepm", "Median firm AI spend per employee", "USD/employee-month"),
    (
        "spend.top_10_percent_median_pepm",
        "top_10_percent_median_pepm",
        "Top 10% firm median AI spend per employee",
        "USD/employee-month",
    ),
    (
        "spend.top_1_percent_median_pepm",
        "top_1_percent_median_pepm",
        "Top 1% firm median AI spend per employee",
        "USD/employee-month",
    ),
)


def _month_bounds(day):
    start = date.fromisoformat(day[:10])
    if start.month == 12:
        end = date(start.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def parse_ramp_curated(payload, digest, fetched):
    """Curated published Ramp AI Index tables; no Ramp Data API key required."""
    required = (
        "source_url",
        "methodology_version",
        "cohort_version",
        "spend_per_employee",
        "adoption_overall",
    )
    if any(field not in payload for field in required):
        raise SourceError("Ramp curated import missing required fields")
    if payload.get("raw_hash") and payload["raw_hash"] != digest:
        raise SourceError("Ramp curated import raw_hash does not match archived evidence")
    source_url = public_url(payload["source_url"])
    published = payload.get("published_at")
    if published:
        published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if published_at.tzinfo is None or published_at > datetime.fromisoformat(fetched.replace("Z", "+00:00")):
            raise SourceError("Ramp publication must be a known past UTC timestamp")
    notes = payload.get("methodology_notes") or []
    common = {
        "definition": "Paid Ramp card and bill-pay AI transactions divided by employee count; free tools excluded",
        "coverage_numerator": None,
        "coverage_denominator": None,
        "methodology_notes": notes,
        "curated_from": payload.get("curated_from") or [source_url],
        "license": "Public published research; attribution to Ramp Economics Lab",
        "measurement_limits": [
            "Undercounts free AI tools and personal employee accounts",
            "Top 1% cohort is small and revised as late transactions arrive",
        ],
    }
    result = []
    seen = set()
    for row in payload["spend_per_employee"]:
        month = row.get("date_month")
        if not isinstance(month, str):
            raise SourceError("Ramp spend month must be an ISO date")
        start, end = _month_bounds(month)
        for series_id, field, label, unit in RAMP_SPEND_SERIES:
            if field not in row or row[field] in (None, ""):
                continue
            value = number(row[field])
            key = (series_id, month)
            if key in seen:
                raise SourceError("Duplicate Ramp spend observation")
            seen.add(key)
            result.append(
                observation(
                    "ramp",
                    "D5",
                    series_id,
                    value,
                    unit,
                    start,
                    end,
                    digest,
                    fetched,
                    published=published,
                    url=source_url,
                    cohort=payload["cohort_version"],
                    methodology_version=payload["methodology_version"],
                    metadata={**common, "label": label, "entity": series_id.rsplit(".", 1)[-1], "month": month[:7]},
                )
            )
    for row in payload["adoption_overall"]:
        month = row.get("date_month")
        rate = row.get("adoption_rate_pct")
        if not isinstance(month, str) or rate in (None, ""):
            raise SourceError("Ramp adoption month and rate are required")
        start, end = _month_bounds(month)
        key = ("adoption.overall_rate_pct", month)
        if key in seen:
            raise SourceError("Duplicate Ramp adoption observation")
        seen.add(key)
        share = number(rate)
        if share > 100:
            raise SourceError("Ramp adoption share exceeds 100 percent")
        result.append(
            observation(
                "ramp",
                "D5",
                "adoption.overall_rate_pct",
                share,
                "%",
                start,
                end,
                digest,
                fetched,
                published=published,
                url=source_url,
                cohort=payload["cohort_version"],
                methodology_version=payload["methodology_version"],
                metadata={
                    **common,
                    "label": "Businesses with paid AI transaction",
                    "entity": "overall_adoption",
                    "month": month[:7],
                    "mom_change_pp": row.get("mom_change_pp"),
                    "yoy_change_pp": row.get("yoy_change_pp"),
                },
            )
        )
    if not result:
        raise SourceError("Ramp curated import produced no observations")
    return result


OPENDESI_UA = "RadonAICycle/1.0 (open-design-arena collector; +https://github.com/joemccann/radon)"
_OPENDESI_FAMILIES = {
    "quality ranking": "overall",
    "model quality vs cost": "overall",
    "web app ranking": "web",
    "web app model quality vs cost": "web",
    "mobile app ranking": "mobile",
    "mobile app model quality vs cost": "mobile",
    "desktop app ranking": "desktop",
    "desktop app model quality vs cost": "desktop",
    "dashboard ranking": "dashboard",
    "dashboard model quality vs cost": "dashboard",
    "landing page ranking": "landing",
    "landing page model quality vs cost": "landing",
}
_WEIGHT_ROW = re.compile(r'<li class="weight-row"[^>]*data-weight-row="([^"]+)"[^>]*>(.*?)</li>', re.S)
_WEIGHT_FIELDS = re.compile(
    r"Average score:\s*([0-9]+(?:\.[0-9]+)?)/100;.*?"
    r"Average cost:\s*\$([0-9]+(?:\.[0-9]+)?);.*?"
    r"Average time:\s*([0-9]+(?:\.[0-9]+)?)\s*min",
    re.S,
)
_HEADING_SPLIT = re.compile(r"<h3>\s*([^<]+?)\s*</h3>", re.I)
_RANK_ROW = re.compile(r"<div class=\"ranking-row\"([^>]*)>", re.I)
_COST_POINT = re.compile(r'<span class="cost-point(?:\s[^"]*)?"([^>]*)>', re.I)
_ATTR = re.compile(r'([A-Za-z0-9:_-]+)="([^"]*)"')


def _opendesi_slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _opendesi_family(title):
    key = re.sub(r"[\s·]+", " ", title.lower().replace("&middot;", " ")).strip()
    return _OPENDESI_FAMILIES.get(key)


def _opendesi_attrs(tag):
    return {name: value for name, value in _ATTR.findall(tag)}


def _http_published(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def parse_opendesi(html, digest, fetched, *, published=None, url=None):
    """Parse the public OpenDesign Arena leaderboard. LLM/model-quality only."""
    if not isinstance(html, str) or not html.strip():
        raise SourceError("OpenDesign Arena HTML structure broke; page was empty")
    overall = {}
    for model, body in _WEIGHT_ROW.findall(html):
        fields = _WEIGHT_FIELDS.search(body)
        if not fields:
            raise SourceError("OpenDesign Arena HTML structure broke; overall model fields missing")
        if model in overall:
            raise SourceError("OpenDesign Arena HTML structure broke; duplicate overall model row")
        overall[model] = {
            "avg_score": number(fields.group(1)),
            "usd_per_artifact": number(fields.group(2)),
            "avg_minutes": number(fields.group(3)),
        }
    if not overall:
        raise SourceError("OpenDesign Arena HTML structure broke; overall model rows were not found")

    family_scores, family_costs = {}, {}
    parts = _HEADING_SPLIT.split(html)
    for title, body in zip(parts[1::2], parts[2::2]):
        family = _opendesi_family(title)
        if not family:
            continue
        if "ranking" in title.lower():
            for tag in _RANK_ROW.findall(body):
                attrs = _opendesi_attrs(tag)
                model, score = attrs.get("data-ranking-row"), attrs.get("data-score")
                if not model or score is None:
                    raise SourceError("OpenDesign Arena HTML structure broke; ranking row missing model or score")
                key = (family, model)
                value = number(score)
                if key in family_scores and family_scores[key] != value:
                    raise SourceError("OpenDesign Arena HTML structure broke; conflicting family scores")
                family_scores[key] = value
        if "quality vs cost" in title.lower():
            for tag in _COST_POINT.findall(body):
                attrs = _opendesi_attrs(tag)
                model, cost = attrs.get("data-name"), attrs.get("data-cost")
                if not model or cost is None:
                    raise SourceError("OpenDesign Arena HTML structure broke; cost point missing model or cost")
                key = (family, model)
                value = number(cost)
                if key in family_costs and family_costs[key] != value:
                    raise SourceError("OpenDesign Arena HTML structure broke; conflicting family costs")
                family_costs[key] = value

    for model, fields in overall.items():
        published_score = family_scores.get(("overall", model))
        if published_score is not None and published_score != fields["avg_score"]:
            raise SourceError("OpenDesign Arena HTML structure broke; overall score conflict")

    asof = published or fetched
    day = fetched[:10]
    common = {
        "definition": (
            "OpenDesign Arena published design-task evaluation. LLM/model-quality only; "
            "not GPU scarcity or Silicon Data."
        ),
        "lane": "llm-model-quality",
        "asof": asof,
        "license": "Public leaderboard page; retain OpenDesign attribution",
    }
    result, seen = [], set()

    def emit(family, model, metric, value, unit):
        series = f"{family}.{_opendesi_slug(model)}.{metric}"
        key = (series, family, model)
        if key in seen:
            raise SourceError("Duplicate OpenDesign Arena observation")
        seen.add(key)
        result.append(
            observation(
                "open-design-arena",
                "D6",
                series,
                value,
                unit,
                day,
                day,
                digest,
                fetched,
                published=published,
                url=url or URLS["open-design-arena"],
                cohort="opendesi-arena-v1",
                methodology_version="opendesi-arena-html-v1",
                metadata={
                    **common,
                    "entity": model,
                    "label": f"{model} {family} {metric.replace('_', ' ')}",
                    "model": model,
                    "task_family": family,
                },
            )
        )

    for model, fields in overall.items():
        emit("overall", model, "avg_score", fields["avg_score"], "score")
        emit("overall", model, "usd_per_artifact", fields["usd_per_artifact"], "USD/artifact")
        emit("overall", model, "avg_minutes", fields["avg_minutes"], "minutes")
    for (family, model), score in family_scores.items():
        if family == "overall":
            continue
        emit(family, model, "avg_score", score, "score")
    for (family, model), cost in family_costs.items():
        if family == "overall":
            continue
        emit(family, model, "usd_per_artifact", cost, "USD/artifact")
    if not result:
        raise SourceError("OpenDesign Arena HTML structure broke; no observations extracted")
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
    if day >= fetched[:10]:
        raise SourceError("App history requires a completed UTC day")
    seen, parsed = set(), []
    for item in payload["data"]:
        entity = str(item["app_id"])
        if entity in seen:
            raise SourceError("Duplicate app identifier")
        seen.add(entity)
        if any(
            isinstance(item[field], bool) or not str(item[field]).isdigit()
            for field in ("total_tokens", "total_requests")
        ):
            raise SourceError("App count must be a non-negative integer")
        parsed.append((int(item["rank"]), entity, int(item["total_tokens"]), int(item["total_requests"])))
    total_tokens = sum(item[2] for item in parsed)
    total_requests = sum(item[3] for item in parsed)
    if total_tokens and not total_requests:
        raise SourceError("App token activity has no requests")
    cohort_members = sorted(item[1] for item in parsed)
    cohort = hashlib.sha256(json.dumps(cohort_members).encode()).hexdigest()[:16]
    top10 = sum(item[2] for item in sorted(parsed)[:10])
    values = (
        ("returned_requests", total_requests, "requests", "Returned public-app requests"),
        (
            "tokens_per_request",
            total_tokens / total_requests if total_requests else 0,
            "tokens/request",
            "Returned public-app tokens per request",
        ),
        (
            "top10_token_concentration",
            top10 / total_tokens if total_tokens else 0,
            "ratio",
            "Top-10 public-app token concentration",
        ),
    )
    return [
        observation(
            "openrouter",
            "D2",
            series,
            value,
            unit,
            day,
            day,
            digest,
            fetched,
            url="https://openrouter.ai/api/v1/datasets/app-rankings",
            cohort=cohort,
            metadata={
                "label": label,
                "truncated": True,
                "population": "Returned top-100 public app cohort only; hidden and private apps excluded",
                "publisher_as_of": payload.get("meta", {}).get("as_of"),
                "cohort_members": cohort_members,
                "coverage_numerator": len(parsed),
                "response_row_count": 3,
                "license": "CC BY 4.0",
                "citation": "Source: OpenRouter (openrouter.ai/apps)",
            },
            methodology_version="openrouter-app-aggregate-v2",
        )
        for series, value, unit, label in values
    ]


def collect_gpu_history(transport, start, end):
    listing, _digest, _fetched = transport.fetch(GPU_HISTORY_INDEX)
    if not isinstance(listing, list):
        raise SourceError("GPU Rental Prices history index schema changed")
    result, days = [], set()
    for item in listing:
        name, url = item.get("name"), item.get("download_url")
        if not isinstance(name, str) or not name.endswith(".json") or not isinstance(url, str):
            continue
        day = name.removesuffix(".json")
        if not start <= day <= end:
            continue
        payload, digest, fetched = transport.fetch(public_url(url))
        rows = parse_gpu(payload, digest, fetched)
        if payload.get("date") != day:
            raise SourceError("GPU Rental Prices snapshot date mismatch")
        result.extend(rows)
        days.add(day)
    latest, digest, fetched = transport.fetch(URLS["gpu-rental"])
    if start <= latest.get("date", "") <= end and latest["date"] not in days:
        result.extend(parse_gpu(latest, digest, fetched))
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
        return collect_gpu_history(transport, start, end)
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
    if source == "open-design-arena":
        html, digest, fetched, meta = transport.fetch_html(URLS[source], headers={"User-Agent": OPENDESI_UA})
        return parse_opendesi(html, digest, fetched, published=_http_published(meta.get("last_modified")))
    if source == "noaa":
        return parse_noaa(
            *transport.fetch(
                URLS[source],
                params={
                    "dataset": "daily-summaries",
                    "stations": ",".join(NOAA_DOM_STATIONS),
                    "startDate": start,
                    "endDate": end,
                    "format": "json",
                    "units": "metric",
                    "includeAttributes": "false",
                    "includeStationName": "true",
                },
            ),
            start,
            end,
        )
    raise SourceError(
        {
            "portkey": "Automated access and completed-day methodology unverified",
            "vast": "Authenticated offer schema and entitlement not verified",
            "lambda": "Direct HTML pricing semantics require a verified cohort import",
            "issuer-disclosures": "Requires reviewed issuer disclosure import; unattended release retrieval is not enabled",
            "ramp": "Requires curated published Ramp AI Index import; Ramp Data API is out of scope for v1",
        }.get(source, "Collector is not configured")
    )

# Flex sFTP setup

Routine cash flows, TWR NAV/flows, and journal recon come from **IBKR-hosted sFTP**, not the Flex Web Service. The VPS is an SSH **client**. Do not open an inbound port. Do not SendRequest from `/orders`, `/performance`, or weekday timers.

Same-day blotter remains IB Gateway `journal_sync`. Flex `1422766` is daily recon.

Cutover history: [`flex-sftp-cutover.md`](flex-sftp-cutover.md). Operator board: [`show-me-flex-sftp-ops.html`](show-me-flex-sftp-ops.html).

## Queries (exactly two)

| Query | Portal name | Sections | Cadence |
|---|---|---|---|
| `1442520` | Equity Summary in Base | NAV in Base, Cash Transactions, Transfers | SOD, Last Business Day, XML |
| `1422766` | Trade History | Trades | EOD, Last Business Day, XML |

Do not create a cash-only query. Do not set `IB_FLEX_FLOWS_QUERY_ID`. Do not tick EquitySummaryByReportDateInBase or extra Flex queries for delivery.

Portal: Performance & Reports → Flex Queries Delivery. **Delivery Method: FTP. Encryption: Yes.** That is IBKR sFTP + PGP.

## IBKR grant (one-time, per account)

sFTP is by request. Email `filedelivery@interactivebrokers.com`:

- Client Portal Flex Query delivery, IB-hosted sFTP, **we pull**
- Query ids `1442520` and `1422766`, XML, Last Business Day, PGP
- Not reporting-integration / EmployeeTrack / master-sub
- RSA public key + PGP public key attached
- Egress **IPv4 only**

They return host, username, directory (`outgoing`), port 22. Production host: `ftp2.interactivebrokers.com` (`64.190.196.110`). Pin the host key. Never `StrictHostKeyChecking accept-new`.

## VPS files

Keys and env live **outside** `/etc/radon/env` so `TWS_PASSWORD` is not in this unit.

```
/var/lib/radon/flex-secrets/          0700 radon
  ibkr_sftp                           0600  RSA identity
  ibkr_sftp.pub
  gnupg/                              0700  PGP homedir
  ssh_config                          0600  Host ibkr-flex, AddressFamily inet,
                                            IdentitiesOnly yes, StrictHostKeyChecking yes
  known_hosts                         0600  pinned IBKR host key
  env                                 0600  TURSO_* + IB_FLEX_NAV_QUERY_ID + IB_FLEX_QUERY_ID
                                            (no TWS_PASSWORD, no IB_FLEX_TOKEN required)
/var/lib/radon/flex-inbox/            0700  pulled files (IBKR names them <acct>.<Query_Name>.<from>.<to>.xml.pgp)
```

`radon-flex-pull.service` uses `EnvironmentFile=-/var/lib/radon/flex-secrets/env` and `InaccessiblePaths` on `/etc/radon/env`.

Generate keys on the VPS, not the laptop. Copy only public keys off-box. Encrypted backup of the PGP private key off-box. Not git.

## Timer

`radon-flex-pull.timer`: Tue–Sat 07:30 ET pull, 08:30 ET empty-dir retry. On `auto-sync-units.txt`. Heartbeat `flex-pull`.

- Empty `outgoing` **through 2026-08-31**: ok skip (IBKR first delivery).
- Empty **from 2026-09-01**: error. Do not SendRequest on miss.

Monday SOD sits until Tuesday 07:30. Same-day fills do not wait on this timer.

## Manual recovery

Portal **Run** (not SendRequest) → XML →

```bash
python3.13 -m scripts.flex_delivery_ingest --file /path/to.xml
# or
python3.13 -m scripts.cash_flow_sync --from-file /path/to.xml
python3.13 scripts/perf_twr_builder.py --from-file /path/to.xml
python3.13 scripts/journal_rehydrate.py --from-file /path/to.xml
```

Live Flex fetch is `--sendrequest` only, after `flex_embargo.is_blocked()` is false. Do not use it to "check" a miss.

## Root install

Units: `cloud/services/radon-flex-pull.{service,timer}`. Listed in `setup-vps.sh` and `installed-units.sha256`. New host: keygen, filedelivery email, pin host key, stripped env, then let deploy enable the timer.

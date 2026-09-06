# Documentation index

Durable facts have one owner. `docs/owners.json` maps path globs to that
owner. This file is the human index. Do not copy signal math or env-var
tables into the root README.

Archived session artifacts live in [`docs/archive/`](archive/).

## Start here

| Topic | Doc |
|-------|-----|
| Developer runbook, gates, calculations | [`CLAUDE.md`](../CLAUDE.md) |
| Authoring toolchain (agents, verification) | [`DEVELOPMENT.md`](../DEVELOPMENT.md) |
| Operator contributing rules | [`CONTRIBUTING.md`](../CONTRIBUTING.md) |
| Security reports | [`SECURITY.md`](../SECURITY.md) |
| Support (proprietary, unsupported clones) | [`SUPPORT.md`](../SUPPORT.md) |
| External services and signup (incl. Robinhood read-only MCP; host secret locations) | [`docs/external-services.md`](external-services.md) |
| CLI commands | [`docs/scripts-reference.md`](scripts-reference.md) |
| Python script map | [`scripts/README.md`](../scripts/README.md) |
| Repo layout plan | [`docs/monorepo-legibility-plan.md`](monorepo-legibility-plan.md) |

## Operations and cloud

| Topic | Doc |
|-------|-----|
| Background services, watchdogs, env | [`docs/operations.md`](operations.md) |
| Cloud architecture, Turso, two-mode deploy | [`docs/cloud-services.md`](cloud-services.md) |
| Production network topology (app / broker split) | [`docs/spof-host-split.md`](spof-host-split.md) · [`docs/radon-network-topology.png`](radon-network-topology.png) |
| Retired beta.radon.run (do not recreate) | [`docs/cloud-services.md`](cloud-services.md#retired-betaradonrun-2026-08-20) |
| Monorepo cutover and rollback | [`docs/monorepo-cloud-migration.md`](monorepo-cloud-migration.md) |
| Cloud operating contract | [`cloud/CLAUDE.md`](../cloud/CLAUDE.md) |
| Incident cases and `/incident` | [`docs/incident-runbook.md`](incident-runbook.md) |
| Grok P1 auto-fix | [`docs/grok-page-responder.md`](grok-page-responder.md) |
| Software factory (Foreman) | [`docs/factory.md`](factory.md) |

## Market data and strategies

| Topic | Doc |
|-------|-----|
| Regime and scanner indicator specs | [`docs/indicators/README.md`](indicators/README.md) |
| AI boom leading indicators | [`docs/ai-boom-leading-indicators.md`](ai-boom-leading-indicators.md) |
| Equibles market-structure API | [`docs/equibles-api.md`](equibles-api.md) |
| Strategy specs | [`docs/strategies.md`](strategies.md) |
| VCG-R research notes | [`docs/cross_asset_volatility_credit_gap_spec_(VCG).md`](cross_asset_volatility_credit_gap_spec_(VCG).md) |
| GARCH convergence | [`docs/strategy-garch-convergence.md`](strategy-garch-convergence.md) |
| Options structures | [`docs/options-structures.md`](options-structures.md) |
| Evaluation pipeline | [`docs/evaluation.md`](evaluation.md) |
| Unusual Whales API | [`docs/unusual_whales_api.md`](unusual_whales_api.md) |

## IB and performance

| Topic | Doc |
|-------|-----|
| IB Gateway Docker (laptop) | [`docs/ib-gateway-docker.md`](ib-gateway-docker.md) |
| IB connection troubleshooting | [`docs/ib-connection-troubleshooting.md`](ib-connection-troubleshooting.md) |
| IB Gateway recovery / 2FA | [`docs/ib-gateway-recovery.md`](ib-gateway-recovery.md) |
| IB Flex sFTP setup (install) | [`docs/flex-sftp-setup.md`](flex-sftp-setup.md) |
| IB Flex transfers / TWR | [`docs/ib-flex-transfers-runbook.md`](ib-flex-transfers-runbook.md) |
| Performance reconstruction | [`docs/performance-reconstruction.md`](performance-reconstruction.md) |
| Chart system | [`docs/chart-system.md`](chart-system.md) |
| Brand identity | [`docs/brand-identity.md`](brand-identity.md) |
| Public agent-design surface + eval scenarios | [`docs/design-evals.md`](design-evals.md) |
| Short locate / borrow playbook | [`docs/short-locate-borrow.md`](short-locate-borrow.md) |
| OAuth subscription auth | [`docs/oauth-subscription-auth.md`](oauth-subscription-auth.md) |

## Glossary

| Term | Definition |
|------|------------|
| **Convexity** | Asymmetric payoff where expected upside materially exceeds downside |
| **CRI** | Crash Risk Index, composite crash-risk and CTA deleveraging model |
| **CTA** | Commodity Trading Advisor, typically systematic trend-following funds |
| **Dark Pool** | Private off-exchange venue used for institutional trading |
| **Edge** | A specific reason the market is mispricing an outcome |
| **GEX** | Gamma exposure surface across the options chain |
| **Kelly Criterion** | Position-sizing framework that scales exposure to edge and odds |
| **VCG-R** | Volatility-Credit Gap, VIX>28 + VCG>2.5σ risk-off trigger |

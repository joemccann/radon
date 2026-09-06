# Clear production performance verification

Measured 2026-09-05 against the compiled, non-demo Next.js application at `http://localhost:3002`. This is a local diagnostic, not production field telemetry or a backend latency measurement.

## Method

- Production compile, fresh Chromium context, cold HTTP cache, service workers disabled.
- Desktop 1440×1000 and mobile 390×844; CDP 150 ms network latency, 1.6 Mbps download, 0.75 Mbps upload, 4× CPU slowdown.
- Browser-isolated representative API responses and WebSocket messages prevent broker/service mutations. API fixtures are fulfilled locally; their delivery does not represent real upstream latency.
- Native PerformanceObservers collect paint, layout shifts, long tasks and interaction events. CDP measures transferred resources and account-chart readiness. The fixture clock replaces normal resource-timing getters, so those getters are deliberately not used.
- Observation ends two seconds after the actual account chart and fonts are ready. Polling/realtime pages cannot use network-idle as their completion condition.
- One canonical-host baseline sample per viewport; two post-change samples per viewport. Small diagnostic samples, not percentile claims. Concurrent work completed other visual changes between production checkpoints, so not every byte difference is attributable to one optimization.
- Final samples use the compiled checkpoint containing the standalone Performance route split. Later shared-CSS minimum-font-size adjustments are excluded from this measured checkpoint; the final release build is verified separately.

## Measured changes

1. Inter remains preloaded. Plex Mono faces remain available, but are fetched only when rendered instead of preloading all five weights/styles.
2. The already-conditional assistant panel is dynamically imported on first opening. Its markdown, assistant and order-risk dependencies no longer compete with the cold account view. First-open focus, live quote propagation, order-risk rendering and Escape dismissal remain tested.
3. The mobile Performance presentation shares the parent data/polling owner instead of creating a second performance request and poller. Standalone mobile consumers retain their existing connected wrapper.
4. Performance now loads its own dynamic route component, following the existing isolated Portfolio route pattern. It no longer waits for unrelated scanner/order/workspace code. `portfolioLastSync` and `marketState` are forwarded unchanged and covered by the lazy-boundary regression.

## Results

| Metric | Desktop before | Desktop after | Mobile before | Mobile after |
| --- | ---: | ---: | ---: | ---: |
| FCP / native LCP | 1,672 ms | 1,412–1,456 ms | 1,612 ms | 1,380 ms |
| Actual account chart ready | 5,600 ms | 5,183–5,231 ms | 5,538 ms | 5,178–5,180 ms |
| Initial JS transfer | 530,031 B | 469,399–469,444 B | 530,040 B | 469,406–469,407 B |
| Initial CSS transfer | 68,721 B | 70,303 B | 68,721 B | 70,303 B |
| Initial font transfer | 259,424 B | 227,026 B | 259,424 B | 227,026 B |
| Initial request count | 69 | 68 | 64 | 63 |
| CLS | 0.00834 | 0.00861 | 0.00122 | 0.00122 |
| Long tasks / longest | 5 / 99 ms | 4 / 100–101 ms | 4 / 121 ms | 3–4 / 125–130 ms |
| Sum of long-task time above 50 ms | 181 ms | 125–130 ms | 198 ms | 136–173 ms |
| Period click to second animation frame | 21–50 ms | 21–55 ms | 22–50 ms | 26–49 ms |
| Observed interaction event maximum | 56 ms | 48–56 ms | 40 ms | 40 ms |
| First Performance navigation | 2,310 ms | 779–781 ms | 2,314 ms | 759–767 ms |
| Performance GETs across dashboard → performance → dashboard | 3 | 3 | 4 | 3 |
| WebSockets before / after navigation | 2 / 2 | 2 / 2 | 2 / 2 | 2 / 2 |

The native LCP candidate is the loading paragraph, not the loaded account. The complete chart remains approximately 5.2 seconds under the disclosed constrained-network profile. Calling this a 1.4-second loaded dashboard would be incorrect. Observed interaction timing is a lab interaction sample, not field INP. CLS, LCP and INP definitions/field thresholds: [Google Web Vitals](https://web.dev/articles/vitals).

Initial JavaScript fell approximately 11.4% and font transfer 12.5%. Chart readiness improved approximately 7%. An intermediate measurement caught a slower Performance navigation (3,315–3,334 ms) after deferring shared assistant dependencies. The isolated Performance route repaired that regression; final navigation is 759–781 ms, approximately 66% below the original baseline. The intermediate evidence remains in `/tmp/radon-clear-perf-after`.

## Verification and reproduction

- Performance request ownership regression: actual fetch count failed with `expected 1, received 2`; passes after sharing the owner, including desktop/mobile resize transitions.
- Performance component and startup/lazy-boundary suites: 52 assertions passed.
- Font and Clear overview suites: 26 assertions passed; overview line/function coverage 100%, branch coverage 96.6%.
- Focus, quote plumbing and request ownership: 9 assertions passed.
- Clear overview browser suite: 8 passed, including desktop/mobile first-open assistant focus/Escape and single Performance GET.
- Post-change production throttle suite: 4 passed. No live order-place/cancel/modify request was transmitted. Both existing sockets persist across route navigation; they serve different status/futures and portfolio-price responsibilities.

```sh
CLEAR_PERF=1 CLEAR_PERF_BASE_URL=http://localhost:3002 CLEAR_PERF_RUNS=2 \
PLAYWRIGHT_PORT=3002 PLAYWRIGHT_BASE_HOST=localhost \
RADON_AUTHLESS_TEST_TOKEN=<matching-local-server-token> \
npx playwright test e2e/clear-performance.spec.ts --workers=1 --output=/tmp/radon-clear-perf-final
```

The server must already be a compiled non-demo build with the matching token-bound test configuration. The harness does not change live authentication or upstream service behavior. Timing artifacts are attached to each test as `performance.json`; canonical baseline artifacts are in `/tmp/radon-clear-perf-before-font`, final artifacts in `/tmp/radon-clear-perf-final`.

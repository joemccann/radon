# UW Advanced execution and rollout

## Delivery

This change implements the dependency-linked October14roadmap in a new research domain. UI: existing ticker Flow report, including mobile; Profile credentials contains UW configuration and capability state. Operator-only API/MCP reads materialized research. Shared admission covers Python and Next provider access. IB quotes/portfolio/order risk remain unchanged.

## Account admission evidence (2026-10-21)

Bounded REST probes from the configured localUW credential returned HTTP200 for economic-calendar, AAPL companyprofile and futures/contracts. No quota/countheaders were supplied, so neither unlimitedallowance nor higherRPM is verified; conservative120RPM/40000allowance remains. A SPYcompanyprofile404was followed by AAPL200; an ETFresponsecannotestablishissuer-profiledenial. No socketwasopenedlocally: same-tokenmulti-hostconnectionsareunsupported.

VPS publicandtailnetSSH connections timedout. ActualVPSentitlementACKs, serviceactivation, lagmeasurement and stagedpromotionremainunverified. Do not labeltheimplementation asactivated.

## Dependency graph

- T1 account/config receipts depends_on: []
- T2 REST governor depends_on: [T1]
- T3 stream anddurablestore depends_on: [T1]
- T4 Advanced research depends_on: [T1]
- T5 privateAPI/UI/MCP depends_on: [T2,T3,T4]
- T6 measuredpromotion depends_on: [T5]

T1 -> {T2,T3,T4} -> T5 -> T6. Unknownaccountlimitsdonotblockcodedelivery; theyblockincreasingallowance andclaimingverifiedscope.

## Activation

After exact-headCIandproductiondeploy, use Profile -> Credentials -> UnusualWhales. Existing UW_TOKEN is theonlynewdomaincredential; no vendorsecretisreturnedto browser/MCP. Set selectedchannels and scheduledtickers. Enable onlyaftera sameVPSprobeconfirmschannelACK. Restartresearchcollector throughtheexistingadminservicecontroller; enablethehourlytimerthroughcontrolledbootstrap/ops. Inspectfeedcoverage panel+health,recordlagandboundedqueue metrics,shadowcompareagainstcurrentREST andpromoteonewatchlistthenportfolio. U8/U9optionalissuer/macro/futures are gatedbytheireffectiveaccess.

## Full tape capacity decision

Do not enable marketwideoption_tradesbydefault. Existingstreamcounters andboundedqueue/storagewatermarks providepilotmeasurements; use selectedtickerchannelsfirst. No livepayload-rate/compression/diskmeasurementwasobtainedduringthisimplementation, so U11doesnotauthorizebroadcapture. Kafkaandhistoricaltape purchases remainseparate.

## Verification

New focusedPythonfixtures coveradmission, cache/budget, streamingsemantics/storage, researchdatacontacts andAPIguarding. Vitestcoversproxyadmission, typedresearchrows andpanelprovenance. Playwrightcoversdesktop/mobile retaineddata, gaps, signedstrategydata andrefresh. All suitesrunonGitHubrunners; staticlocalAST/typeinspection only. Exact-headCIandbrowserartifactsarethereleaseevidence.

## Rollback

Set RADON_UW_STREAM_ENABLED=0 andstopnewresearchtimer; privateUIshowsretained/unavailabledata, existingRESTandIBpathsremain. Revertingthechange restorespre-upgradeNextdirectfetch; no brokerstorage/order migration is introduced. RetainrawSQLite/archivefilesforresearchprovenance; donotpromotepartialframesascompleteobservations.

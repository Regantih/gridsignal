# GridSignal Control Room — write-up

**Problem.** A distributed home-battery fleet earns most of its money in a handful of hours a
year, and that is exactly when a device stops answering. The operator has to notice the silence,
work out what it costs the commitment and coordinate engineers and field techs — across
dashboards, chat and spreadsheets, while the market clears at the cap.

**Who it helps.** Fleet operators and reliability engineers at a company like Base Power, the
field techs they dispatch, and the homeowner whose battery is the one that went dark and who only
wants to know whether their lights still have backup.

**Solution.** GridSignal Control Room is a simulation-only operator console for a fleet of
batteries run as a mesh of agents. Every battery, gateway and zone publishes an HMAC-signed
capability card; when capacity is lost, a coordinator calls for capacity, healthy agents bid a
price that reflects wear and the homeowner's backup reserve, and the cheapest covering set is
proposed. Jev, a fast decision model, judges root cause, agent trustworthiness and backup risk
with a confidence — but signatures, deterministic rules and a named human approval decide. Only
after approval is the device quarantined, its kW reassigned and the whole sequence written to an
append-only audit timeline. A Grid Signals view scores a day-ahead-anchored dispatch policy on
real ERCOT prices, and a Member App shows the same event as a homeowner sees it.

**Impact.** One approval turns $8,971 at risk into $8,683 recovered across 10,000 simulated
devices on a real ERCOT scarcity day, detected and reallocated in about 138 ms. The data says why
that minute matters: on the scarcity days bundled here only 47% of a battery's capturable value
was visible in the day-ahead curve.

# GridSignal Control Room — write-up

**Problem.** A distributed home-battery fleet earns most of its money in a handful of hours a
year, and that is exactly when a device stops answering. The operator has to notice the silence,
price it against the commitment and coordinate the fix — while the market clears at the cap.

**Who it helps.** Fleet operators and reliability engineers at a company like Base Power, the
field techs they dispatch, and the member whose battery went dark and only wants to know whether
their lights still have backup.

**Solution.** GridSignal Control Room is a simulation-only operator console for a fleet of
batteries run as a mesh of agents. Every battery, gateway and zone publishes an HMAC-signed
capability card; when capacity is lost, a coordinator calls for it, healthy agents bid a price
reflecting wear and the member's backup reserve, and the cheapest covering set is proposed. Jev, a fast decision model, judges root cause, trustworthiness and backup risk with a
confidence — but signatures, rules and a named human approval decide. Only then is the device
quarantined, its kW reassigned and the sequence written to an append-only audit log. The same
orchestration ships firmware in gated canary rings, rolling back a build that silently breaks
hot devices, and lets newly installed units join on probation. Grid Signals
scores a day-ahead-anchored policy on real ERCOT prices and maps congestion; a Member App shows
the homeowner's side.

**Impact.** One approval turns $8,971 at risk into $8,683 recovered across 10,000 simulated
devices on a real ERCOT scarcity day, in about 138 ms. Why that minute matters: on the bundled scarcity days only 47% of a battery's capturable value was
visible in the day-ahead curve, while the worst zone-hour's $39.82/MWh of congestion returns
only $0.13 per battery per day to re-timing.

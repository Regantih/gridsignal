# GridSignal Control Room — write-up

**Problem.** A distributed home-battery fleet earns most of its money in a handful of hours a
year, and that is exactly when a device stops answering. The operator has to notice the silence,
price it and coordinate the fix.

**Who it helps.** Fleet operators and reliability engineers at a company like Base, their field
techs, and members who only want their lights to stay on.

**Solution.** GridSignal Control Room is a simulation-only operator console for a fleet of
batteries run as a mesh of agents. Every battery, gateway and zone publishes an HMAC-signed
capability card; when capacity is lost, healthy agents bid a price reflecting wear and the
member's backup reserve, and the cheapest covering set is proposed. Jev, a fast decision model,
judges root cause, trust and backup risk with a confidence, but signatures, rules and a named
human decide. Only then is the device quarantined, its kW
reassigned and the sequence audited. Related alarms collapse into one incident timeline; any
award is overridable by hand with a logged reason. Dispatch is home-first: each battery serves
its simulated household load before exporting, a partner utility's units are a tenant the mesh
may never touch, and mutual aid never spends a giver's reserve. The same
orchestration ships firmware in gated canary rings; Grid Signals scores a day-ahead-anchored
policy on real ERCOT prices and prices the wear of each cycle, so a spread too thin to pay for
the pack is not taken.

**Impact.** One approval turns $4,812 at risk into $4,761 recovered across 10,000 simulated
devices on a real ERCOT scarcity day, in ~0.1 s of compute. Only 47% of capturable scarcity-day
value was visible day-ahead; the home eats export revenue; and wear-gating at a modelled
$100/MWh skips 5.55 of 7.55 held-out cycles on an older pack. Measured, not smoothed.

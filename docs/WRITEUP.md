# GridSignal Control Room — write-up

**Problem.** A distributed home-battery fleet earns most of its money in a handful of hours a
year, and that is exactly when a device stops answering. Someone must notice the silence, price
it and fix it.

**Who it helps.** Fleet operators at a company like Base, and members who want lights on.

**Solution.** GridSignal Control Room is a simulation-only console for a battery fleet run as a
mesh of agents. Every battery, gateway and zone publishes an HMAC-signed capability card; when
capacity is lost, healthy agents bid and the cheapest covering set wins. Jev, a fast decision
model, judges root cause, trust and backup risk against six pre-committed principles;
signatures, rules and a named human still decide. Only then is the device quarantined, its kW
reassigned and the sequence audited. Related alarms collapse into one timeline; any award is
overridable with a reason. Dispatch is home-first: the house runs off the grid while storage is
held for the day-ahead peak, the member's reserve is never sold, and a partner utility's units
are untouchable. It also ships firmware in canary rings, takes JSON-lines telemetry, and runs
coordinator and agents as separate processes over multiplexed loopback TCP. A what-if console
prices typed scenarios on the same engine at fleet scale, dispatching nothing.

**Impact.** One approval turns $4,812 at risk into $4,751 recovered across 10,000 simulated
devices on a real ERCOT scarcity day. Only 47% of a scarcity day's capturable value was visible
day-ahead; on seven held-out days the policy beats a naive schedule 7 of 7, median $1.94 per simulated battery
per day; scored blind, the rules beat Jev 21/24 to 17/24. The scarcity-day headline is uplift
over that naive clock schedule, not revenue: $127.17 of export revenue against $16.46 for the
naive schedule, $110.71 per battery of uplift.

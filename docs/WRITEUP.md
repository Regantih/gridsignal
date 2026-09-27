# GridSignal Control Room — write-up

**Problem.** A home-battery fleet earns most of its money in a handful of hours a year, and
that is exactly when a device stops answering. Someone must notice the silence, price it and
fix it.

**Who it helps.** Fleet operators like Base's, and members who want the lights on.

**Solution.** GridSignal Control Room is a simulation-only console for a battery fleet run as a
mesh of agents. Every battery, gateway and zone publishes an HMAC-signed capability card; when
capacity is lost, healthy agents bid and the cheapest covering set wins. Jev, a fast model, judges root cause, trust and backup risk against six pre-committed principles;
signatures, rules and a named human still decide. Only then is the device quarantined, its kW
reassigned and the sequence audited. Related alarms collapse into one timeline; any award is
overridable with a reason. Dispatch is home-first: the house runs off the grid while storage is
held for the day-ahead peak, the member's reserve is never sold, a partner utility's units are
untouchable. It ships firmware in canary rings, takes JSON-lines telemetry, runs
agents as separate processes over multiplexed loopback TCP, and prices typed what-if scenarios
on the same engine at fleet scale, dispatching nothing.

**Impact.** One approval turns $4,812 at risk into $4,751 recovered across 10,000 simulated
devices on a real ERCOT scarcity day. Only 47% of a scarcity day's capturable value was visible
day-ahead. On seven held-out days, with the naive clock schedule allowed the same evening-peak
hold, the policy wins 5 of 7 but averages -$0.79 per simulated battery per day, median +$0.13;
against a battery that does nothing, median $1.64. Scored blind, the rules beat Jev 21/24 to
17/24. The scarcity-day headline is uplift, not revenue: $127.17 of export revenue against
$63.28 for the naive schedule, $63.89 per battery of uplift.

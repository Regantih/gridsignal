# GridSignal Control Room — write-up

**Problem.** A distributed home-battery fleet earns most of its money in a handful of hours a
year, and that is exactly when a device stops answering. The operator has to notice the silence,
price it against the commitment and coordinate the fix.

**Who it helps.** Fleet operators and reliability engineers at a company like Base, the
field techs they dispatch, and the member who only wants to know their lights still have backup.

**Solution.** GridSignal Control Room is a simulation-only operator console for a fleet of
batteries run as a mesh of agents. Every battery, gateway and zone publishes an HMAC-signed
capability card; when capacity is lost, a coordinator calls for it, healthy agents bid a price
reflecting wear and the member's backup reserve, and the cheapest covering set is proposed. Jev,
a fast decision model, judges root cause, trustworthiness and backup risk with a confidence — but
signatures, rules and a named human approval decide. Only then is the device quarantined, its kW
reassigned and the sequence audited. Related alarms collapse into one incident on one timeline;
any award can be overridden by hand with a logged reason. Dispatch is home-first: each
battery serves its simulated household load before exporting the surplus, a partner utility's
units are a separate tenant the mesh may never touch, and a neighbour mutual-aid card can never
spend a giver's reserve. The same orchestration ships firmware in gated canary rings; Grid
Signals scores a day-ahead-anchored policy on real ERCOT prices.

**Impact.** One approval turns $4,812 at risk into $4,761 recovered across 10,000 simulated
devices on a real ERCOT scarcity day, in ~0.1 s of compute. Only 47% of a battery's capturable value
on the bundled scarcity days was visible day-ahead, and serving the home first costs export
revenue — reported as measured, not smoothed.

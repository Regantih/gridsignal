# GridSignal Control Room — write-up

**Problem.** A distributed home-battery fleet earns most of its money in a handful of hours a
year, and that is exactly when a device stops answering. Someone must notice the silence,
price it and coordinate the fix.

**Who it helps.** Fleet operators at a company like Base, and members who want their lights
to stay on.

**Solution.** GridSignal Control Room is a simulation-only operator console for a fleet of
batteries run as a mesh of agents. Every battery, gateway and zone publishes an HMAC-signed
capability card; when capacity is lost, healthy agents bid and the cheapest covering set is
proposed. Jev, a fast decision model, judges root cause, trust and backup risk, and weighs six
principles committed before they were scored — hard vetoes on member backup, market rules and
deliverability, plus three weighted ones — into act, act-and-notify or ask-a-human, naming the
principle that decided it; signatures, rules and a named human still decide. Only then is the
device quarantined, its kW reassigned and the sequence audited. Related alarms collapse into one
incident timeline and any award is overridable with a logged reason. Dispatch is
home-first: each battery serves
its household load before exporting, a partner utility's units are a tenant the mesh may never
touch, and mutual aid never spends a giver's reserve. The same orchestration ships firmware in
gated canary rings; Grid Signals scores a day-ahead policy on real ERCOT prices.

**Impact.** One approval turns $4,812 at risk into $4,751 recovered across 10,000 simulated
devices on a real ERCOT scarcity day, in ~0.1 s of compute. Only 47% of capturable scarcity-day
value was visible day-ahead; wear-gating at a modelled $100/MWh skips 5.55 of 7.55 held-out
cycles on an older pack; and scored blind, the rules beat Jev 21/24 to 17/24 on the safety
questions. Measured, not smoothed.

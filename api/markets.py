"""The markets GridSignal knows about. Only ERCOT is modelled; the engine and every
number in the app belong to it. The rest are listed honestly as not modelled yet so the
front end can show where Base Power operates without inventing homes, prices or dollars.
The web registry in web/src/lib/markets.ts mirrors this list."""

from __future__ import annotations

MARKETS: list[dict] = [
    {
        "id": "ercot",
        "name": "Texas",
        "iso": "ERCOT",
        "status": "live",
        "note": "Live. Prices are real ERCOT data; the fleet is simulated.",
        "bounds": [[-106.65, 25.84], [-93.51, 36.5]],
        "center": [-99.3, 31.2],
    },
    {
        "id": "comed",
        "name": "Illinois, ComEd area",
        "iso": "PJM",
        "status": "planned",
        "note": "Coming soon, not modelled yet. Needs PJM prices and a ComEd promise model.",
        "bounds": [[-91.51, 36.97], [-87.5, 42.51]],
        "center": [-88.6, 41.6],
    },
    {
        "id": "colorado",
        "name": "Colorado",
        "iso": "WECC",
        "status": "equipment",
        "note": "Equipment only. No grid programme is modelled.",
        "bounds": [[-109.06, 36.99], [-102.04, 41.0]],
        "center": [-105.55, 39.0],
    },
]

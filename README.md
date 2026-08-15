# FS25 Server Hub

A private Home Assistant app for a Farming Simulator 25 dedicated server.

## Included pages

- Live overview with HD interactive map, players, farm balance and activity
- Vehicle Fleet with search and maintenance filters
- Economy with a mission-aware transaction ledger, evidence, category breakdowns and active contracts
- Active Mods with author/version search
- Play History with sessions, leaderboard and daily activity
- Diagnostics with feed health, adaptive polling and database status

The app uses Home Assistant Ingress, so it is available through the normal Home Assistant interface and Nabu Casa remote connection.

## Version 0.5.2 — Review and optimisation

Unclassified balance changes now appear in a **Needs Review** queue. You can choose a proper category, add a custom title such as “Mushroom autosale”, and optionally remember a narrow amount-range rule for future entries. Saved rules only run after the normal contract, production, product, supply and fleet matching has failed.

The app now hashes savegame payloads and reuses unchanged parsed data, shares one FTP session between `missions.xml` and `placeables.xml`, and uses adaptive polling. Savegame and map checks slow down while the server is empty, then return to the configured interval as soon as a player joins or a contract remains active.

The Diagnostics page is read-only and shows source health, latency, payload sizes, changed/unchanged checks, current polling intervals and database housekeeping. No Home Assistant notifications, alarms, MQTT sensors or extra integrations are added. The existing tractor icon and logo are unchanged.

## Version 0.5.0 — Production autosale tracking

The Economy page now correlates `careerSavegame.xml`, `vehicles.xml`, `economy.xml`, `missions.xml` and `placeables.xml` between saves. This lets it distinguish contract payments, production autosales, product sales, fleet purchases, supply purchases and unclassified running costs. Every ledger row displays its confidence and can expose the evidence used.

Pallets, bales and big bags are now classified separately from farm machinery. For example, chicken feed is displayed as a supply purchase rather than a vehicle purchase.

The app can download `missions.xml` directly from GPORTAL FTP. Version 0.5.0 automatically reads `placeables.xml` from that same savegame folder, so production autosale tracking needs no additional FTP credentials or path. An optional `placeables_url` is available for HTTP-based setups.

The dashboard remains usable without a mission source, but contract income will be labelled as inferred.

## Interactive HD map

The map supports zoom, pan, player and vehicle tracking, field and owned-land layers, clickable marker details, player focus controls and full-screen mode. The app requests the configured GIANTS map endpoint at up to 2048 pixels and automatically falls back if necessary.


## Economy audit

Version 0.5.0 also reads `placeables.xml`, including each production point's `directSellFillType` settings. Positive balance changes with no stronger contract, product or fleet evidence can therefore be labelled as inferred production autosales and show the possible products and buildings. Rock-specific completion matching and earlier supply repairs remain included.

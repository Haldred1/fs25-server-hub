# FS25 Server Hub

A Home Assistant app for Farming Simulator 25 dedicated servers.

FS25 Server Hub turns the standard GIANTS dedicated-server feeds and savegame XML files into a live dashboard with an interactive map, fleet information, farm economy history, contracts, mods, play history and diagnostics.

## Included pages

- Live overview with HD interactive map, players, farm balance and activity
- Vehicle Fleet with search and maintenance filters
- Economy with a mission-aware transaction ledger, evidence, category breakdowns and active contracts
- Active Mods with author/version search
- Play History with sessions, leaderboard and daily activity
- Diagnostics with feed health, adaptive polling and database status

The app uses Home Assistant Ingress, so it is available through the normal Home Assistant interface and supported remote access such as Nabu Casa.

## Setup-friendly installation

A new installation no longer has to be fully configured before it can stay running. If one of the five required GIANTS feeds is missing, FS25 Server Hub opens a **Setup checker** instead of exiting.

The setup checker shows what is missing and can test:

- server statistics XML
- live map image
- `careerSavegame.xml`
- `vehicles.xml`
- `economy.xml`
- optional `missions.xml` and `placeables.xml` HTTP sources
- FTP/FTPS login, `missions.xml`, and the automatically derived `placeables.xml` path

An optional `setup_mode` switch makes the checker available later when changing server provider details or troubleshooting connectivity.

Home Assistant configuration translations give each option a friendly name and description, including reminders that hosted FTP servers can use non-standard ports.

## Compatibility

The app is tested with a Farming Simulator 25 dedicated server hosted by **GPORTAL**, using the standard GIANTS Server Manager Web API and direct FTP savegame access.

Other providers should work when they expose the same GIANTS Web API resources and/or standard FTP/FTPS access to the FS25 savegame files. Provider-specific ports, paths and access methods can differ, so compatibility with every host is not claimed until tested.

Nothing in the normal collector is hard-coded to one farm's URLs or credentials.

## Economy and savegame correlation

The Economy page correlates `careerSavegame.xml`, `vehicles.xml`, `economy.xml`, `missions.xml` and `placeables.xml` between saves. This lets it distinguish contract payments, production autosales, product sales, fleet purchases, supply purchases and unclassified running costs. Every ledger row displays its confidence and can expose the evidence used.

Pallets, bales and big bags are classified separately from farm machinery. The app can download `missions.xml` directly over FTP and automatically reads `placeables.xml` from the same savegame folder, so production autosale tracking needs no second set of FTP credentials.

The dashboard remains usable without a mission source, but contract income can be less confidently classified.

## Interactive HD map

The map supports zoom, pan, player and vehicle tracking, field and owned-land layers, clickable marker details, player focus controls and full-screen mode. The app requests the configured GIANTS map endpoint at up to 2048 pixels and automatically falls back if necessary.

## Review, optimisation and diagnostics

Unclassified balance changes appear in a **Needs Review** queue. Users can choose a category, add a custom title and optionally remember a narrow amount-range rule for future entries. Saved rules only run after normal contract, production, product, supply and fleet matching has failed.

The app hashes savegame payloads and reuses unchanged parsed data, shares one FTP session between `missions.xml` and `placeables.xml`, and uses adaptive polling. Savegame and map checks can slow down while the server is empty, then return to the configured interval as soon as activity resumes.

The Diagnostics page is read-only and shows source health, latency, payload sizes, changed/unchanged checks, current polling intervals and database housekeeping.

## Updates and persistence

The SQLite database and app state live under `/data` and are preserved across normal repository updates. Transaction history, play sessions, snapshots and saved classification rules therefore survive version upgrades.

Future releases are installed through Home Assistant's normal **Update** button. Updating FS25 Server Hub restarts this app only; Home Assistant Core does not need to be restarted.

See **DOCS.md** for full setup instructions, field descriptions, polling guidance and the GPORTAL FTP walkthrough.

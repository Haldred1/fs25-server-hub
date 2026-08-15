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

If one of the five required GIANTS feeds is missing, FS25 Server Hub opens a **Setup checker** instead of repeatedly stopping. The checker shows what is missing and can test the configured HTTP feeds plus FTP/FTPS access to `missions.xml` and `placeables.xml`.

Home Assistant configuration translations provide friendly names and descriptions for every option, including the savegame FTP host, custom port and remote path.

An optional `setup_mode` switch allows an existing installation to temporarily open the connection tester when changing provider details or troubleshooting.

## Compatibility

The app is tested with a Farming Simulator 25 dedicated server hosted by **GPORTAL**, using the standard GIANTS Server Manager Web API and direct FTP savegame access.

Other providers should work when they expose the same GIANTS Web API resources and/or standard FTP/FTPS access to the FS25 savegame files. Provider-specific ports, paths and access methods can differ, so compatibility with every host is not claimed until tested.

Nothing in the normal collector is hard-coded to one farm's URLs or credentials.

## Economy and savegame correlation

The Economy page correlates `careerSavegame.xml`, `vehicles.xml`, `economy.xml`, `missions.xml` and `placeables.xml` between saves. This lets it distinguish contract payments, production autosales, product sales, fleet purchases, supply purchases and unclassified running costs. Every ledger row displays its confidence and can expose the evidence used.

Pallets, bales and big bags are classified separately from farm machinery. The app can download `missions.xml` directly over FTP and automatically reads `placeables.xml` from the same savegame folder, so production autosale tracking needs no second set of FTP credentials.

## Interactive HD map

The map supports zoom, pan, player and vehicle tracking, field and owned-land layers, clickable marker details, player focus controls and full-screen mode. The app requests the configured GIANTS map endpoint at up to 2048 pixels and automatically falls back if necessary.

## Review, optimisation and diagnostics

Unclassified balance changes appear in a **Needs Review** queue. Users can choose a category, add a custom title and optionally remember a narrow amount-range rule for future entries. Saved rules only run after normal contract, production, product, supply and fleet matching has failed.

The app hashes savegame payloads and reuses unchanged parsed data, shares one FTP session between `missions.xml` and `placeables.xml`, and uses adaptive polling. Savegame and map checks can slow down while the server is empty, then return to the configured interval when activity resumes.

The Diagnostics page is read-only and shows source health, latency, payload sizes, changed/unchanged checks, current polling intervals and database housekeeping.

## Updates and persistence

The SQLite database and app state live under `/data` and are preserved across normal repository updates. Transaction history, play sessions, snapshots and saved classification rules survive version upgrades.

Future releases are installed through Home Assistant's normal **Update** button. Updating FS25 Server Hub restarts this app only; Home Assistant Core does not need to be restarted.

See `fs25_server_hub/DOCS.md` for full setup instructions, polling guidance and the GPORTAL FTP walkthrough.

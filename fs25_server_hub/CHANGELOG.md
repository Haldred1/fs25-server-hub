## 0.5.7
- Fixes first-start migration mode when upgrading from the old Local installation.
- The 0.5.6 version marker was published before its final startup files, allowing Home Assistant to cache an incomplete 0.5.6 build that launched the normal dashboard process and rejected blank feed URLs.
- Republishes the completed migration startup script, migration server and Docker image under a new version so Home Assistant is forced to rebuild the app.
- No farm history or existing repository-edition `/data` is changed by this update.

## 0.5.6
- Added a one-time **Migration mode** for safely moving `fs25.db` from the old Local installation into the repository edition.
- Migration mode pauses all normal FS25 polling and serves an Ingress-only database upload page instead.
- Uploaded databases are integrity-checked and must contain the expected FS25 history tables.
- If the repository edition already has a database, a timestamped safety copy is created before import.
- After import, turn Migration mode off and restart the app once to resume the normal dashboard.

## 0.5.5
- Converted FS25 Server Hub into a Home Assistant app-repository release.
- Future releases are detected from the repository when `config.yaml` is given a newer version, allowing normal Home Assistant **Update** installs.
- Updating the repository-installed app rebuilds/restarts FS25 Server Hub only; Home Assistant Core does not need to be restarted.
- Existing `/data` storage remains persistent for normal updates of the repository-installed app.
- Added repository and migration documentation for the one-time move from the old Local app.

## 0.5.4
- Added **Vehicle repairs** to the Economy Needs Review category dropdown.
- Reviewed repair costs now appear as their own spending category in the ledger and breakdowns.
- Added a matching repair icon and support for remembered classification rules.

## 0.5.3
- Fixed Economy review saves through Home Assistant Ingress when POST bodies are forwarded with HTTP chunked transfer encoding.
- Prevented unread POST bodies from corrupting keep-alive connections and causing follow-on 400, 401, 501 or 502 responses.
- Explicitly preserves same-origin Home Assistant credentials on dashboard API writes.
- Bumped frontend cache keys so browsers load the corrected review code immediately.

# Changelog

## 0.5.2

- Adds a **Needs Review** queue for unclassified income and spending with manual categories and optional custom titles.
- Adds user-approved classification rules that only match future unclassified movements with the same direction and a narrow amount range; stronger contract, production, product, supply and fleet evidence always wins.
- Adds read-only **Diagnostics** showing source health, latency, payload size, changed/unchanged counts, adaptive intervals and database size.
- Reuses one FTP login for `missions.xml` and `placeables.xml` and hashes savegame payloads so unchanged files are not parsed again.
- Adds adaptive polling: normal speed while players are online or contracts are active, quieter savegame and map checks while the server is empty, with immediate wake-up when activity resumes.
- Removes the unnecessary 30-second browser overview refresh while the live event stream is connected; a slower fallback remains for reconnection failures.
- Compresses balance samples older than the configured retention period into daily summaries while keeping transaction and play-session history indefinitely.
- Adds manual categories for production sales, crops/products, animals, land, buildings, loans, leases, supplies and operating costs.
- Does not add Home Assistant notifications, alarms, MQTT entities or sensors.
- Keeps the existing tractor icon and logo files unchanged.

## 0.5.1
- Added a custom Home Assistant add-on icon and logo using a green tractor graphic.
- Preserved the existing mdi:tractor sidebar panel icon.

## 0.5.0

- Reads `placeables.xml` and discovers owned production outputs configured for direct selling.
- Automatically reuses the existing GPORTAL FTP connection and derives `placeables.xml` from the same folder as `missions.xml`; no second password or FTP path is required.
- Classifies otherwise unexplained positive balance changes as inferred **Production autosale** income when direct selling is enabled.
- Shows the candidate products and production buildings in every autosale audit trail.
- Adds an Automatic production sales panel to Economy.
- Conservatively repairs repeated recent unclassified credits when they have no contract, product, fleet or inventory evidence and production direct selling is currently configured.
- Adds optional `placeables_url` support for servers that expose the file over HTTP.

## 0.4.1

- Fixes rock-breaking contract payments being left unclassified when `info.completion` remains low or the final `SUCCESS` snapshot is skipped.
- Uses `numRocksDestroyed` and other mission-specific counters as completion evidence instead of relying only on the generic completion field.
- Matches a removed accepted contract to an exact listed payout when the server saves before completion and again only after collection.
- Automatically repairs matching rock-contract income recorded by 0.4.0 as unclassified, when its nearby contract-removal audit record is available.

## 0.4.0

- Turns Economy into an audited farm finance centre with confirmed-by-value coverage, unclassified totals and contract performance statistics.
- Adds rich contract payout evidence: field, progress, mission state, listed reward, reimbursement, expected payout, captured balance change and variance.
- Adds detailed mission progress for tree transport, rock destruction and deadwood jobs, plus expiry and machinery information.
- Separates successful collection, near-complete disappearance, cancellation and failure so mission matches are more trustworthy.
- Expands supply classification for animal feed, crop inputs, fuel and utilities, with quantity, count and saved object value details.
- Automatically repairs older chicken-food and other unmistakable big-bag/pallet purchases that were stored as vehicle purchases.
- Adds richer expandable ledger evidence, source-file badges, available-contract previews and contract performance cards.
- Improves CSV exports with evidence sources and confidence reasons.
- Tunes fresh-install polling defaults to 60 seconds for live feeds and 120 seconds for savegame/FTP data.

## 0.3.1

- Added direct FTP and explicit FTPS download support for GPORTAL `missions.xml`.
- Added masked FTP password configuration plus host, port, username, path, TLS and passive-mode options.
- HTTP `missions_url` remains supported and takes priority when configured.
- Improved missing-source messages in the Economy page and app logs.

## 0.3.0

- Rebuilt Economy as a detailed farm ledger with 7/30/90/365-day ranges, category breakdowns, search and confidence filters.
- Added optional `missions_url` support and parses active, completed and available contracts from `missions.xml`.
- Matches positive balance changes to completed/collected missions and labels them as confirmed contract payments.
- Records contract accepted, completed and failed lifecycle activity.
- Separates real machinery from pallets, bales, big bags and other saved objects.
- Classifies chicken feed, seed, fertilizer, lime and similar items as supply purchases instead of vehicles.
- Adds evidence panels showing balance before/after, mission matches, inventory changes and added/removed objects.
- Adds active contract cards, listed rewards, completion state and recent contract activity.
- Adds income and spending breakdowns plus a confirmed-ledger percentage.
- Preserves compatibility with older v0.2 snapshots and avoids one-off false vehicle sales after upgrading.

## 0.2.1

- Automatically upgrades the configured GIANTS map feed request to 2048px at JPEG quality 95.
- Falls back through 1024px, 512px and the original configured URL if a host rejects the higher resolution.
- Detects and displays the actual map pixel dimensions returned by the server.
- Adds an HD/Sharp/Low-resolution source badge to the map header.
- Caps zoom intelligently against the real source dimensions to avoid enlarging the image far beyond its native resolution.
- Preserves coordinate-perfect live player, vehicle, field and land overlays while improving map clarity.
- Correctly serves either JPEG or PNG map responses.

## 0.2.0

- Replaced the static overview image with a fully interactive live map.
- Added a dedicated Live Map page.
- Added mouse-wheel and pinch zoom, drag panning, keyboard navigation, reset, player fitting and browser full-screen mode.
- Added live player and vehicle markers with clickable detail panels.
- Added layer controls for players, vehicles, owned fields, field numbers and owned land centres.
- Added clickable player chips that locate the player on the map.
- Added live map coordinates, source age and an optional north/south marker flip for mod-map compatibility.
- Preserves map zoom, centre, orientation and active layers when fresh server data arrives.
- Clarified that GIANTS exposes field and farmland centre coordinates rather than exact polygon boundaries.

## 0.1.2

- Added a Home Assistant app changelog.
- Repackaged the 0.1.1 event-driven refresh fix with a confirmed version bump so Supervisor detects the update.
- Dashboard polling remains frequent, but unchanged feed checks no longer redraw the full page.

## 0.1.1

- Changed dashboard refreshes to be event-driven.
- Unchanged feed checks no longer rebuild the page.
- Polling settings remain measured in seconds.

## 0.1.0

- Initial FS25 Server Hub release.
- Added Overview, Vehicle Fleet, Economy, Mods and Play History pages.
- Added GIANTS feed polling, live map, SQLite history and Home Assistant Ingress support.

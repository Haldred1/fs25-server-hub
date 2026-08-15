# FS25 Server Hub configuration

FS25 Server Hub is a Home Assistant app for Farming Simulator 25 dedicated servers. It reads the standard GIANTS Server Manager Web API feeds and can additionally read savegame files over FTP for contract and production tracking.

## Quick start

1. Install **FS25 Server Hub** from the Home Assistant app repository.
2. Open **Settings → Apps → FS25 Server Hub → Configuration**.
3. Enter the five required GIANTS Web API URLs: Statistics, Map, `careerSavegame.xml`, `vehicles.xml` and `economy.xml`.
4. Save and start the app.
5. If a required value is missing, the app opens the **Setup checker** instead of stopping with an error.
6. Use the test buttons to verify each configured HTTP feed.
7. For contract and production tracking, configure either the optional HTTP URLs or the FTP connection to `missions.xml`.
8. When the five required feeds are configured, restart the app with **Setup / connection-test mode** disabled to open the full dashboard.

The configuration screen includes friendly names and descriptions for every field. The setup checker never displays the FTP password.

## Finding the GIANTS Web API URLs

The standard feeds come from **GIANTS Server Manager → Miscellaneous → Web API (RESTful)**. Copy the URLs for the matching resources into Home Assistant.

Required feeds:

- `stats_url` — dedicated-server statistics XML; server state and connected players
- `map_url` — live map image
- `career_url` — `careerSavegame.xml`; balance, settings and career data
- `vehicles_url` — `vehicles.xml`; fleet and saved objects
- `economy_url` — `economy.xml`; stored products and economy information

Optional sources:

- `missions_url` — HTTP address for `missions.xml`
- `placeables_url` — HTTP address for `placeables.xml`

If the optional savegame files are not available over HTTP, use the FTP fields instead.

## Setup / connection-test mode

Set:

```yaml
setup_mode: true
```

and restart FS25 Server Hub to temporarily replace the normal dashboard with the setup checker. This is useful for testing an existing installation after changing provider details, ports, passwords or Web API URLs.

The checker can:

- show which of the five required feeds are configured or missing
- test every configured HTTP source and verify XML responses
- test the live map endpoint
- connect to the configured FTP/FTPS server
- download and validate `missions.xml`
- automatically derive and test the sibling `placeables.xml` path
- report useful latency, payload and error information without exposing the FTP password

After testing, turn `setup_mode` back to `false`, save and restart the app.

If one or more required feeds are blank, the app enters the setup checker automatically even when `setup_mode` is false. This prevents a fresh installation from repeatedly stopping before the user can diagnose the configuration.

## Connecting GPORTAL missions.xml directly

1. In GPORTAL, open **My Servers**, select the FS25 server, then open **Status**. Copy the FTP host, **port**, username and password shown under the access data.
2. Do not assume the FTP port is `21`; hosted servers can use a custom port.
3. In GPORTAL **File Manager** or FileZilla, browse to the active savegame and locate `missions.xml`. Copy the complete remote path shown there.
4. In Home Assistant, open **Settings → Apps → FS25 Server Hub → Configuration** and enter the FTP values. Leave `missions_url` empty when FTP is being used.
5. Optional: enable `setup_mode`, restart the app and press **Test FTP connection** before returning to normal mode.
6. In normal mode the log should report `mission source: ftp` and `production source: ftp-sibling`.

A typical path looks like:

```text
/profile/savegame1/missions.xml
```

The Hub derives `/profile/savegame1/placeables.xml` automatically from that path, so the second savegame file does not need its own FTP credential or path.

Do not paste the FTP password into chats, screenshots or support posts. Store it only in the masked Home Assistant configuration field.

## Provider compatibility

The app is tested against a Farming Simulator 25 dedicated server hosted by **GPORTAL**, using the standard GIANTS Web API plus direct FTP savegame access.

Other dedicated-server providers should work when they expose the same GIANTS Web API resources and/or standard FTP/FTPS access to the FS25 savegame files. Provider-specific file paths, FTP ports and access methods can differ, so compatibility with every provider is not claimed until tested.

The five required dashboard feeds use standard GIANTS data rather than values hard-coded for one server.

## Recommended polling settings

Recommended starting values:

- GIANTS Web API interval: `45` seconds (where supported by the server manager)
- Farming Simulator autosave: `5` minutes
- `stats_poll_seconds`: `60`
- `map_poll_seconds`: `60`
- `save_poll_seconds`: `120`
- `adaptive_polling`: `true`
- `empty_server_save_poll_seconds`: `300`
- `empty_server_map_poll_seconds`: `600`
- `balance_sample_retention_days`: `90`

The live player and map feed can only become as fresh as the GIANTS Web API snapshot. Economy and fleet changes appear after the savegame files update. A five-minute autosave with a two-minute savegame poll is a good balance between detail, responsiveness and unnecessary FTP requests.

When adaptive polling is enabled, normal intervals remain active while players are online. Savegame polling also remains fast while a contract is active. Empty-server intervals reduce unnecessary downloads and automatically return to normal when activity resumes.

## Economy classifications

The collector compares consecutive savegame snapshots. It uses the balance change as the exact amount, then looks for matching evidence:

- completed or collected mission → **Contract payment**
- owned production output set to direct selling plus an otherwise unexplained credit → **Production autosale** (inferred)
- removed product pallet/bale or falling stored quantity → **Product sale**
- new real machine → **Vehicle purchase**
- new seed, fertilizer, lime, feed or other consumable object → **Supplies purchased**
- unmatched amount → **Inferred income/expense**

A single save interval can combine several game transactions. `placeables.xml` proves which outputs are configured for direct selling, but it does not include a separate receipt allocating the exact balance increase to each product. Production autosales therefore remain marked **Inferred**, with the candidate products and buildings shown in the audit trail.

Rock-breaking contracts use saved rock counters as well as the generic completion field. The ledger shows the balance before and after each transaction, source files, confidence reasoning, matched mission details, saved-object quantities and values, and inventory changes.

## Needs Review and Diagnostics

The **Needs Review** section on the Economy page lets the user classify otherwise unexplained balance changes. Optional remembered rules only apply to future unclassified movements with the same income/spending direction and a narrow amount range. They never override stronger contract, product, production or fleet evidence.

The **Diagnostics** page shows live source health, latency, payload size, changed/unchanged checks, current polling intervals and database housekeeping.

No Home Assistant notifications, alarms, MQTT entities or sensors are created.

## Persistence and updates

The SQLite ledger, snapshots and cached map are stored in the app's `/data` directory and are included in Home Assistant app backups. Normal repository updates preserve `/data`, so transaction history, play history, snapshots and classification rules survive version upgrades.

Repository releases are installed using Home Assistant's normal **Update** button. Updating FS25 Server Hub rebuilds/restarts this app only; Home Assistant Core does not need to be restarted.

## Legacy Local-app migration

`migration_mode` exists only for moving `fs25.db` from an older Local installation into the repository edition. Leave it disabled for normal installations. Once migration is complete and the repository edition has been verified, normal future updates preserve its `/data` automatically.

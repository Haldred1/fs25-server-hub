# FS25 Server Hub configuration

Paste the server feed URLs into the Home Assistant app configuration. The standard feeds come from **GIANTS Server Manager → Miscellaneous → Web API (RESTful)**. The mission feed can use either an HTTP URL or a direct GPORTAL FTP connection to the active savegame's `missions.xml` file. Production autosale tracking uses `placeables.xml`; with FTP configured it is read automatically from the same savegame folder.

## Required feed options

- `stats_url` — dedicated server statistics XML
- `map_url` — live map image
- `career_url` — `careerSavegame.xml`
- `vehicles_url` — `vehicles.xml`
- `economy_url` — `economy.xml`
- `placeables_url` — optional HTTP address for `placeables.xml`; leave blank when using the GPORTAL FTP connection below
- `missions_url` — optional HTTP address for `missions.xml`
- `missions_ftp_host` — GPORTAL FTP hostname or IP
- `missions_ftp_port` — normally `21`
- `missions_ftp_username` — GPORTAL FTP username
- `missions_ftp_password` — GPORTAL FTP password (masked in the UI)
- `missions_ftp_path` — full remote path ending in `missions.xml`
- `missions_ftp_tls` — enable only when GPORTAL identifies the connection as explicit FTPS
- `missions_ftp_passive` — leave enabled unless support tells you otherwise

Recommended settings:

- GIANTS Web API interval: `45` seconds (the minimum allowed)
- Farming Simulator autosave: `5` minutes
- `stats_poll_seconds`: `60`
- `map_poll_seconds`: `60`
- `save_poll_seconds`: `120`

The live player and map feed can only become as fresh as the GIANTS Web API snapshot. Economy and fleet changes appear after the savegame files update. A five-minute autosave with a two-minute savegame poll is a good balance between detail, responsiveness and unnecessary FTP requests.

## Economy classifications

The collector compares consecutive savegame snapshots. It uses the balance change as the exact amount, then looks for matching evidence:

- completed or collected mission → **Contract payment**
- owned production output set to direct selling plus an otherwise unexplained credit → **Production autosale** (inferred)
- removed product pallet/bale or falling stored quantity → **Product sale**
- new real machine → **Vehicle purchase**
- new seed, fertilizer, lime, feed or other consumable object → **Supplies purchased**
- unmatched amount → **Inferred income/expense**

A single save interval can combine several game transactions. `placeables.xml` proves which outputs are configured for direct selling, but it does not include a separate receipt allocating the exact balance increase to each product. Production autosales therefore remain marked **Inferred**, with the candidate products and buildings shown in the audit trail.

## Persistence

The SQLite ledger, snapshots and cached map image are stored in the app's `/data` directory and are included in Home Assistant backups. Existing history is preserved when updating the app.


## Connecting GPORTAL missions.xml directly

1. In GPORTAL, open **My Servers**, select the FS25 server, then open **Status**. Copy the FTP host, port, username and password shown under the access data.
2. In GPORTAL **File Manager** or FileZilla, browse to the active savegame and locate `missions.xml`. Copy the complete remote path shown there.
3. In Home Assistant, open **Settings → Apps → FS25 Server Hub → Configuration** and enter the five FTP values. Leave `missions_url` empty.
4. Start or restart the app. The log should report `mission source: ftp`.
5. Open Economy. The yellow connection warning should disappear after the next successful savegame poll.

Do not paste the FTP password into chats or screenshots. Store it only in the masked Home Assistant configuration field.


## Economy evidence in 0.5.0

Rock-breaking contracts use the saved rock counters as well as the generic completion field. Production buildings are read from `placeables.xml`; `directSellFillType` entries identify products set to Selling. The app automatically derives `/profile/savegame1/placeables.xml` from the configured `/profile/savegame1/missions.xml` path when FTP is used.

The ledger shows the balance before and after each transaction, the source files used, confidence reasoning, matched mission details, saved object quantities and values, and inventory changes. Contract payout matching is strongest when the collector sees an accepted mission, a successful completion state, and the later collection save.
## Review and optimisation settings

Version 0.5.2 adds these optional settings:

```yaml
adaptive_polling: true
empty_server_save_poll_seconds: 300
empty_server_map_poll_seconds: 600
balance_sample_retention_days: 90
```

When adaptive polling is enabled, configured `save_poll_seconds` and `map_poll_seconds` remain the active intervals while players are online. Savegame polling also remains fast while a contract is active. Empty-server intervals reduce unnecessary downloads and immediately return to normal when activity resumes.

The **Needs Review** section is available on the Economy page. Manual classification rules only apply to future unclassified movements with the same income/spending direction and a narrow amount range. They never override stronger XML evidence.

The **Diagnostics** page is read-only. Transaction and play-session history are retained indefinitely; only high-frequency balance samples older than `balance_sample_retention_days` are compressed into daily summaries.

No Home Assistant notifications, alarms, MQTT entities or sensors are created.


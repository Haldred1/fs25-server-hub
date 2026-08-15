# One-time migration from Local FS25 Server Hub

The existing Local installation is identified by Supervisor as `local_fs25_server_hub`. An app installed from a custom GitHub repository receives a repository-specific prefix, so Home Assistant treats the repository copy as a separate app on the first migration.

## Safe migration order

1. **Do not uninstall the Local app yet.** Its `/data` directory contains `fs25.db`, snapshots and cached data.
2. Create a Home Assistant backup that includes **FS25 Server Hub** before changing anything.
3. Add the GitHub repository to the Home Assistant App Store.
4. Install the repository edition of FS25 Server Hub.
5. Copy the existing configuration values from the Local app into the repository edition. Keep FTP passwords only inside Home Assistant's masked app configuration.
6. Migrate `/data/fs25.db` from the Local app to the repository edition before relying on the new install if historical Economy/Play History data must be retained.
7. Start the repository edition and verify Overview, Economy, Fleet, Mods, Play History and Diagnostics.
8. Only after the data/history and configuration are verified should the old Local app be removed.

The data-copy step depends on how the Home Assistant host is being accessed. Do not guess the Supervisor data-volume path. Use a supported backup/export route or an inspected host path during the migration.

Once the repository edition is in place, future updates do **not** repeat this migration: bump the app version in GitHub and use Home Assistant's normal Update button.

## Recommended database transfer (v0.5.6)

1. Stop the old Local FS25 Server Hub so its database is no longer changing.
2. Create a manual Home Assistant backup containing the old Local FS25 Server Hub and download it.
3. Extract and consolidate `fs25.db`; retain any `fs25.db-wal` alongside it during consolidation so committed WAL transactions are included.
4. Install the repository edition but keep the Local installation.
5. In the repository edition Configuration, set `migration_mode: true`, save, and start/restart the app.
6. Open the app. Migration mode shows a database upload page instead of the dashboard. Upload the consolidated `fs25.db`.
7. When import succeeds, set `migration_mode: false`, save, and restart the repository edition.
8. Verify Economy and Play History before removing the Local installation.

The migration service is only reachable through Home Assistant Ingress. It rejects non-SQLite files, runs `PRAGMA integrity_check`, verifies the core FS25 tables, and creates `/data/fs25-before-migration-<timestamp>.db` before replacing an existing repository-edition database.

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

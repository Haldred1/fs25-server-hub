# FS25 Server Hub - Home Assistant App Repository

This repository publishes **FS25 Server Hub** as a Home Assistant app so new versions can be installed using Home Assistant's normal **Update** button.

## Add the repository

In Home Assistant open **Settings → Apps → App store → ⋮ → Repositories**, add:

`https://github.com/Haldred1/fs25-server-hub`

Then refresh/check the App Store and open **FS25 Server Hub**.

## Release workflow

For every future release:

1. Make the FS25 Server Hub code changes in `fs25_server_hub/`.
2. Increase `version` in `fs25_server_hub/config.yaml`.
3. Add the release notes to `fs25_server_hub/CHANGELOG.md`.
4. Commit/push to the repository.
5. In Home Assistant, use **Check for updates** if the new version is not detected immediately, then press **Update** on FS25 Server Hub.

Home Assistant Core does not need to be restarted. Updating the app may restart the FS25 Server Hub container itself.

## Persistence

FS25 Server Hub stores its SQLite ledger, snapshots and cached data in `/data`. Home Assistant preserves `/data` across normal updates to the same installed app and includes it in app backups.

## One-time migration from the old Local app

The old Local app and this repository app are separate Supervisor app identities. Keep the Local app installed until its `/data/fs25.db` history and configuration have been migrated and verified. See `MIGRATION.md` before removing the old Local installation.

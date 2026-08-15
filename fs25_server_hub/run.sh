#!/usr/bin/with-contenv bashio
set -euo pipefail

export FS25_STATS_URL="$(bashio::config 'stats_url')"
export FS25_MAP_URL="$(bashio::config 'map_url')"
export FS25_CAREER_URL="$(bashio::config 'career_url')"
export FS25_VEHICLES_URL="$(bashio::config 'vehicles_url')"
export FS25_ECONOMY_URL="$(bashio::config 'economy_url')"
export FS25_PLACEABLES_URL="$(bashio::config 'placeables_url')"
export FS25_MISSIONS_URL="$(bashio::config 'missions_url')"
export FS25_MISSIONS_FTP_HOST="$(bashio::config 'missions_ftp_host')"
export FS25_MISSIONS_FTP_PORT="$(bashio::config 'missions_ftp_port')"
export FS25_MISSIONS_FTP_USERNAME="$(bashio::config 'missions_ftp_username')"
export FS25_MISSIONS_FTP_PASSWORD="$(bashio::config 'missions_ftp_password')"
export FS25_MISSIONS_FTP_PATH="$(bashio::config 'missions_ftp_path')"
export FS25_MISSIONS_FTP_TLS="$(bashio::config 'missions_ftp_tls')"
export FS25_MISSIONS_FTP_PASSIVE="$(bashio::config 'missions_ftp_passive')"
export STATS_POLL_SECONDS="$(bashio::config 'stats_poll_seconds')"
export MAP_POLL_SECONDS="$(bashio::config 'map_poll_seconds')"
export SAVE_POLL_SECONDS="$(bashio::config 'save_poll_seconds')"
export REQUEST_TIMEOUT_SECONDS="$(bashio::config 'request_timeout_seconds')"
export ADAPTIVE_POLLING="$(bashio::config 'adaptive_polling')"
export EMPTY_SERVER_SAVE_POLL_SECONDS="$(bashio::config 'empty_server_save_poll_seconds')"
export EMPTY_SERVER_MAP_POLL_SECONDS="$(bashio::config 'empty_server_map_poll_seconds')"
export BALANCE_SAMPLE_RETENTION_DAYS="$(bashio::config 'balance_sample_retention_days')"
MIGRATION_MODE="$(bashio::config 'migration_mode')"
export CURRENCY_SYMBOL="$(bashio::config 'currency_symbol')"
export SITE_TITLE="$(bashio::config 'site_title')"
export PORT="8099"
export DATA_DIR="/data"
export ALLOW_DIRECT="false"

bashio::log.info "Starting FS25 Server Hub"
if [[ "${MIGRATION_MODE}" == "true" ]]; then
  bashio::log.warning "Database migration mode is enabled; normal FS25 polling is paused"
  exec python3 /app/migration.py
fi
MISSION_SOURCE="$([[ -n "${FS25_MISSIONS_URL}" ]] && echo http || ([[ -n "${FS25_MISSIONS_FTP_HOST}" ]] && echo ftp || echo not-configured))"
PRODUCTION_SOURCE="$([[ -n "${FS25_PLACEABLES_URL}" ]] && echo http || ([[ -n "${FS25_MISSIONS_FTP_HOST}" && -n "${FS25_MISSIONS_FTP_PATH}" ]] && echo ftp-sibling || echo not-configured))"
bashio::log.info "Stats poll: ${STATS_POLL_SECONDS}s; map poll: ${MAP_POLL_SECONDS}s; savegame poll: ${SAVE_POLL_SECONDS}s; adaptive polling: ${ADAPTIVE_POLLING}; mission source: ${MISSION_SOURCE}; production source: ${PRODUCTION_SOURCE}"
exec python3 /app/app.py

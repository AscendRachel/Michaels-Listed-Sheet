#!/bin/sh
# Copy the app's daily backups out of the container to ./backups-offsite on the server, keeping the newest 14.
# Run it from cron (for example every night), then sync that folder somewhere else (another machine or a bucket),
# so a lost server doesn't take the only copy of your catalog with it.
#
#   one app:        ./deploy/backup.sh
#   several apps:   COMPOSE="docker compose --env-file deploy/multi/app/my-app.env -f deploy/multi/app/docker-compose.yml" ./deploy/backup.sh
set -eu
cd "$(dirname "$0")/.."
COMPOSE="${COMPOSE:-docker compose}"
mkdir -p backups-offsite
$COMPOSE cp app:/data/backups/. backups-offsite/
ls -1t backups-offsite | tail -n +15 | while read -r f; do rm -f "backups-offsite/$f"; done
echo "Backups copied to $(pwd)/backups-offsite"

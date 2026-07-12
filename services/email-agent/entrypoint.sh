#!/bin/sh
set -e

# The API and poller both import src.graph at module load time, which reads
# instance config from Postgres when AGENT_STORAGE_BACKEND=postgres — before
# either process's own startup code would otherwise get a chance to run
# migrations. Bring the schema to head here, first, so it always exists by
# the time any process-specific code imports src.graph.
python -c "from src.migrate import upgrade_to_head; upgrade_to_head()"

exec "$@"

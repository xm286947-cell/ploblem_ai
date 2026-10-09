#!/bin/bash
set -euo pipefail

: "${OPENSEARCH_HOME:?Set OPENSEARCH_HOME to the extracted OpenSearch directory.}"

SEARCH_PORT="${HARDWARE_SEARCH_PORT:-9200}"
DATA_ROOT="${HARDWARE_SEARCH_DATA_DIR:-$HOME/.hardware-knowledge/search-w0}"
mkdir -p "$DATA_ROOT/data" "$DATA_ROOT/logs"

if [[ -z "${OPENSEARCH_JAVA_HOME:-}" ]]; then
  if [[ -n "${JAVA_HOME:-}" ]]; then
    export OPENSEARCH_JAVA_HOME="$JAVA_HOME"
  elif [[ -x /usr/libexec/java_home ]]; then
    export OPENSEARCH_JAVA_HOME="$(/usr/libexec/java_home -v 21)"
  else
    echo "SEARCH_JAVA_HOME_REQUIRED: set OPENSEARCH_JAVA_HOME or JAVA_HOME to Java 21+." >&2
    exit 2
  fi
fi

if [[ ! -x "$OPENSEARCH_HOME/bin/opensearch" ]]; then
  echo "SEARCH_ENGINE_BINARY_NOT_FOUND: $OPENSEARCH_HOME/bin/opensearch" >&2
  exit 3
fi

export OPENSEARCH_JAVA_OPTS="${OPENSEARCH_JAVA_OPTS:--Xms512m -Xmx512m}"

echo "Starting OpenSearch W0 on http://127.0.0.1:$SEARCH_PORT"
echo "Data: $DATA_ROOT/data"
echo "Logs: $DATA_ROOT/logs"

exec "$OPENSEARCH_HOME/bin/opensearch"   -Ecluster.name=hardware-search-w0   -Enode.name=hardware-search-w0   -Ediscovery.type=single-node   -Enetwork.host=127.0.0.1   -Ehttp.port="$SEARCH_PORT"   -Eplugins.security.disabled=true   -Epath.data="$DATA_ROOT/data"   -Epath.logs="$DATA_ROOT/logs"

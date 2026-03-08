#!/bin/bash
# Invoke the builder_progenitor agent via A2A JSON-RPC protocol.
#
# Usage:
#   ./scripts/invoke_builder.sh "Create a LICENSE.md with MIT license"
#   ./scripts/invoke_builder.sh "Create a LICENSE.md with MIT license" "http://builder-progenitor:8108"

set -e

REQUEST="${1:-}"
ENDPOINT="${2:-http://localhost:8108}"

if [ -z "$REQUEST" ]; then
  echo "Usage: $0 <request> [<endpoint>]"
  echo ""
  echo "Example:"
  echo '  ./scripts/invoke_builder.sh "Create a LICENSE.md with MIT license"'
  exit 1
fi

MESSAGE_ID=$(uuidgen | tr '[:upper:]' '[:lower:]' || echo "msg-$(date +%s)")
REQUEST_ID=$(uuidgen | tr '[:upper:]' '[:lower:]' || echo "req-$(date +%s)")

# Create A2A JSON-RPC payload
PAYLOAD=$(cat <<EOF
{
  "jsonrpc": "2.0",
  "id": "$REQUEST_ID",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "message_id": "$MESSAGE_ID",
      "role": "user",
      "parts": [{"kind": "text", "text": "$REQUEST"}]
    }
  }
}
EOF
)

echo "🚀 Invoking builder_progenitor at $ENDPOINT"
echo "📝 Request: $REQUEST"
echo ""

# Send request
RESPONSE=$(curl -s -X POST "$ENDPOINT" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

# Check for errors
if echo "$RESPONSE" | grep -q '"error"'; then
  echo "❌ A2A Error:"
  echo "$RESPONSE" | jq '.error' 2>/dev/null || echo "$RESPONSE"
  exit 1
fi

# Success
echo "✅ Builder agent invoked successfully"
echo "📤 Response:"
echo "$RESPONSE" | jq '.result' 2>/dev/null | head -50 || echo "$RESPONSE" | head -50

echo ""
echo "💡 Full response available at:"
echo "   GET $ENDPOINT/.well-known/agent-card.json (agent capabilities)"

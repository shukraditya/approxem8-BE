#!/bin/bash
# Test materialized samples

LOGFILE="logs/test_materialized_$(date +%Y%m%d_%H%M%S).log"
SERVER="http://localhost:8000"

echo "=== Materialized Samples Test Suite ===" | tee "$LOGFILE"
echo "Date: $(date)" | tee -a "$LOGFILE"

# Wait for server
sleep 1

echo -e "\n=== Test 1: GROUP BY region (materialized stratified) ===" | tee -a "$LOGFILE"
curl -s -X POST "$SERVER/query" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT region, COUNT(*) as cnt FROM sales GROUP BY region", "accuracy": 0.95}' \
  | tee -a "$LOGFILE"

echo -e "\n\n=== Test 2: Simple COUNT (materialized uniform) ===" | tee -a "$LOGFILE"
curl -s -X POST "$SERVER/query" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT COUNT(*) as cnt FROM sales", "accuracy": 0.95}' \
  | tee -a "$LOGFILE"

echo -e "\n\n=== Test 3: COUNT DISTINCT (should NOT use materialized) ===" | tee -a "$LOGFILE"
curl -s -X POST "$SERVER/query" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT COUNT(DISTINCT user_id) as distinct_users FROM sales", "accuracy": 0.95}' \
  | tee -a "$LOGFILE"

echo -e "\n\n=== Tests Complete ===" | tee -a "$LOGFILE"
echo "Log saved to: $LOGFILE"

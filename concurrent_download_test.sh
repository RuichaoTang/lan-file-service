#!/bin/bash
set -e  # fail on any error

SERVER_HOST=127.0.0.1
SERVER_PORT=5001
FILENAME=bigfile.bin
BASE_DIR=./downloads

mkdir -p "$BASE_DIR/s1" "$BASE_DIR/s2" "$BASE_DIR/s3"

echo "=== Concurrent Download Test ==="
START_TIME=$(date +%s)
echo "Start time: $(date)"

# Start three download tasks in parallel
python3 client.py --host "$SERVER_HOST" --port "$SERVER_PORT" download "$FILENAME" --output "$BASE_DIR/s1/$FILENAME" &
PID1=$!

python3 client.py --host "$SERVER_HOST" --port "$SERVER_PORT" download "$FILENAME" --output "$BASE_DIR/s2/$FILENAME" &
PID2=$!

python3 client.py --host "$SERVER_HOST" --port "$SERVER_PORT" download "$FILENAME" --output "$BASE_DIR/s3/$FILENAME" &
PID3=$!

# wait for all downloads to complete
wait $PID1
wait $PID2
wait $PID3

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo "End time: $(date)"
echo "Total duration: ${DURATION}s"

echo "=== Verifying Results ==="

FILE1="$BASE_DIR/s1/$FILENAME"
FILE2="$BASE_DIR/s2/$FILENAME"
FILE3="$BASE_DIR/s3/$FILENAME"

# Check if files exist
if [[ ! -f "$FILE1" || ! -f "$FILE2" || ! -f "$FILE3" ]]; then
  echo "❌ ERROR: One or more files missing"
  exit 1
fi

# Check file sizes
SIZE1=$(stat -f%z "$FILE1")
SIZE2=$(stat -f%z "$FILE2")
SIZE3=$(stat -f%z "$FILE3")

echo "Sizes:"
echo "s1: $SIZE1"
echo "s2: $SIZE2"
echo "s3: $SIZE3"

if [[ "$SIZE1" != "$SIZE2" || "$SIZE1" != "$SIZE3" ]]; then
  echo "❌ ERROR: File sizes do not match"
  exit 1
fi

# Check file hashes
HASH1=$(shasum "$FILE1" | awk '{print $1}')
HASH2=$(shasum "$FILE2" | awk '{print $1}')
HASH3=$(shasum "$FILE3" | awk '{print $1}')

echo "Hashes:"
echo "s1: $HASH1"
echo "s2: $HASH2"
echo "s3: $HASH3"

if [[ "$HASH1" != "$HASH2" || "$HASH1" != "$HASH3" ]]; then
  echo "❌ ERROR: File contents differ"
  exit 1
fi

echo "✅ All files match (size + hash)"


if [[ $DURATION -le 10 ]]; then
  echo "✅ Likely concurrent execution (finished quickly)"
else
  echo "⚠️ Possibly sequential or slow network"
fi

echo "=== Test Passed ==="
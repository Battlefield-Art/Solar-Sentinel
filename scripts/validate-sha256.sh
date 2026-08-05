#!/bin/sh
set -e

# Usage: validate-sha256.sh <file_path> <expected_sha256>

FILE_PATH=$1
EXPECTED_SHA256=$2

if [ -z "$FILE_PATH" ] || [ -z "$EXPECTED_SHA256" ]; then
    echo "Usage: $0 <file_path> <expected_sha256>"
    exit 1
fi

if [ ! -f "$FILE_PATH" ]; then
    echo "Error: File $FILE_PATH does not exist."
    exit 1
fi

ACTUAL_SHA256=$(sha256sum "$FILE_PATH" | awk '{print $1}')

if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
    echo "Error: SHA256 mismatch for $FILE_PATH"
    echo "Expected: $EXPECTED_SHA256"
    echo "Actual:   $ACTUAL_SHA256"
    exit 1
fi

echo "SHA256 verified for $FILE_PATH"
exit 0

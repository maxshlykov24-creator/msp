#!/bin/sh
# Only the isolated stage database. stdout is binary; redirect to protected off-host storage.
set -eu
exec ssh -o BatchMode=yes dkacademy-vps docker exec kroy-app-stage-db pg_dump -U kroy -d kroy_stage -n public -Fc

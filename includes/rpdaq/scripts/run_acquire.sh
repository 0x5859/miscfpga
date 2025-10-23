#!/usr/bin/env bash
# Single-command Red Pitaya STEMlab 125-14 dual-channel ADC capture launcher.
#
# Architecture (post-refactor):
#   * The board's official ARM C++ tool ``rpsa_client`` does the streaming
#     receive + write — no Python in the hot path.
#   * One single ``waveform.bin`` per run is written under /tmp on the board,
#     scp'd to the PC, and parsed locally by rp_rin_stream.* converters.
#   * /tmp on the board is wiped at start and end so reboot/relaunch always
#     starts clean.
#
# Per invocation:
#   1. Sync clock from PC to RP (no battery RTC on the board).
#   2. Wipe /tmp/rp_rin_stream and /tmp/rpsa_pylib, redeploy rpsa_client.
#   3. Load FPGA stream_app overlay; ensure streaming-server is up on :18901.
#   4. sendConfig the requested decimation / input range / channels via
#      rpsa_client -c -i KEY=VALUE.
#   5. rpsa_client -s -f bin -l <samples-per-channel> writes one .bin + 2 logs.
#   6. Verify the rpsa_client logs report zero loss; abort with non-zero exit
#      if any sample was lost (user requirement: continuous, no missing data).
#   7. scp the .bin + logs to ./runs/<UTC>_<Sa/s>Sa_s/.
#   8. Run rp_rin_stream.metadata to write config.json + summary.json on the PC.
#   9. Wipe board /tmp again.

set -euo pipefail

HOST="${RP_HOST:-rp-f0d653.local}"
PASSWORD="${RP_PASSWORD:-root}"
DECIMATION=128
DURATION=""
OUTPUT_DIR="runs"
INPUT_RANGE="LV"
ALLOW_LOSS=0

usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  -H, --host HOST           Red Pitaya hostname or IP (default: ${HOST})
  -d, --decimation N        ADC decimation, e.g. 128 = 976.5625 kSa/s/ch (default: ${DECIMATION})
  -t, --duration SECONDS    Finite duration. Omit to capture until Ctrl+C
                            (rpsa_client supports up to ~2.1G samples/ch).
  -o, --out DIR             Local output dir under repo root (default: ${OUTPUT_DIR})
  -r, --input-range LV|HV   ADC input range (default: ${INPUT_RANGE})
      --password PASS       Override RP root password
      --allow-loss          Don't abort if rpsa_client reports any sample loss
                            (default: any loss is treated as an error).
  -h, --help                Show this help

Examples:
  $0                                 # decimation=128, run until Ctrl+C
  $0 -d 128 -t 60                    # 60 s @ ~976 kSa/s/ch (will hit ARM bottleneck)
  $0 -d 1024 -t 600                  # 10 min @ ~122 kSa/s/ch (well below bottleneck)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -H|--host) HOST="$2"; shift 2 ;;
    -d|--decimation) DECIMATION="$2"; shift 2 ;;
    -t|--duration) DURATION="$2"; shift 2 ;;
    -o|--out) OUTPUT_DIR="$2"; shift 2 ;;
    -r|--input-range) INPUT_RANGE="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --allow-loss) ALLOW_LOSS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)  echo "Unexpected positional arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! command -v sshpass >/dev/null 2>&1; then
  cat >&2 <<EOF
ERROR: sshpass not found. Install:
  macOS:  brew install hudochenkov/sshpass/sshpass
  Debian: sudo apt install sshpass
EOF
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found. Install via: brew install uv" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RP_WORK=/tmp/rp_rin_stream
RP_PYLIB=/tmp/rpsa_pylib

# Multiplex one authenticated SSH session across all ssh/scp calls.
# macOS Unix-domain socket paths are capped at ~104 chars; keep it short.
CTRL_PATH="/tmp/rprin-cm-$$-%r@%h:%p"
SSH_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o LogLevel=ERROR
  -o ConnectTimeout=10
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=4
  -o ControlMaster=auto
  -o ControlPath="${CTRL_PATH}"
  -o ControlPersist=120
)

cleanup() {
  ssh -o ControlPath="${CTRL_PATH}" -O exit "root@${HOST}" 2>/dev/null || true
  rm -f /tmp/rprin-cm-$$-* 2>/dev/null || true
}
trap cleanup EXIT

export SSHPASS="$PASSWORD"
ssh_rp() { sshpass -e ssh "${SSH_OPTS[@]}" "root@${HOST}" "$@"; }
scp_rp() { sshpass -e scp "${SSH_OPTS[@]}" "$@"; }

ssh_rp true   # open + authenticate the master connection once

echo "[1/8] Sync clock to Red Pitaya from PC ($(date -u +'%Y-%m-%dT%H:%M:%SZ'))"
ssh_rp "date -u -s '@$(date -u +%s)' >/dev/null"

echo "[2/8] Wipe ${RP_WORK} and ${RP_PYLIB}; extract rpsa_client"
ssh_rp '
  set -e
  rm -rf '"'${RP_WORK}'"' '"'${RP_PYLIB}'"'
  mkdir -p '"'${RP_WORK}'"' '"'${RP_PYLIB}'"'
  ZIP=$(ls /opt/redpitaya/streaming/rpsa_client-*-rp.zip 2>/dev/null | head -1)
  if [ -z "$ZIP" ]; then
    echo "ERROR: no /opt/redpitaya/streaming/rpsa_client-*-rp.zip on board" >&2
    exit 1
  fi
  unzip -q -o "$ZIP" -d '"'${RP_PYLIB}'"'
  test -x '"'${RP_PYLIB}'"'/rpsa_client
'

echo "[3/8] Load FPGA stream_app overlay; ensure streaming-server is on :18901"
# /proc/comm truncates the executable name to 15 chars (`streaming-serve`),
# so detect it by listening port instead of by process name.
ssh_rp '
  set -e
  export PATH=$PATH:/opt/redpitaya/bin:/opt/redpitaya/sbin
  overlay.sh stream_app >/dev/null
  if ! ss -tnl 2>/dev/null | awk "{print \$4}" | grep -qE "[:.]18901$"; then
    nohup streaming-server >/tmp/streaming-server.log 2>&1 &
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      sleep 0.5
      if ss -tnl 2>/dev/null | awk "{print \$4}" | grep -qE "[:.]18901$"; then
        break
      fi
    done
  fi
  if ! ss -tnl 2>/dev/null | awk "{print \$4}" | grep -qE "[:.]18901$"; then
    echo "ERROR: streaming-server did not start; tail of /tmp/streaming-server.log:" >&2
    tail -20 /tmp/streaming-server.log >&2 || true
    exit 1
  fi
'

INPUT_RANGE_UPPER=$(echo "$INPUT_RANGE" | tr '[:lower:]' '[:upper:]')
ATTENUATOR="A_1_1"
[[ "${INPUT_RANGE_UPPER}" == "HV" ]] && ATTENUATOR="A_1_20"

echo "[4/8] Configure streaming-server: decimation=${DECIMATION}, channels=ON/ON, ${INPUT_RANGE} (${ATTENUATOR}), BIT_16, NET, raw"
# Each sendConfig key takes a separate rpsa_client -c -i invocation.
# Quoting the value to keep it intact across the shell hop.
SERVER_CFG_KEYS=(
  "adc_decimation=${DECIMATION}"
  "adc_pass_mode=NET"
  "channel_state_1=ON"
  "channel_state_2=ON"
  "channel_state_3=OFF"
  "channel_state_4=OFF"
  "resolution=BIT_16"
  "channel_attenuator_1=${ATTENUATOR}"
  "channel_attenuator_2=${ATTENUATOR}"
  "channel_ac_dc_1=DC"
  "channel_ac_dc_2=DC"
  "use_calib=OFF"
  "data_type_sd=RAW"
  "format_sd=BIN"
)
SERVER_CFG_JSON="{"
SEP=""
for kv in "${SERVER_CFG_KEYS[@]}"; do
  k="${kv%%=*}"; v="${kv#*=}"
  ssh_rp "${RP_PYLIB}/rpsa_client -c -h 127.0.0.1 -i ${k}=${v} -w >/dev/null"
  SERVER_CFG_JSON+="${SEP}\"${k}\":\"${v}\""
  SEP=","
done
SERVER_CFG_JSON+="}"

# Compute Fs and the integer sample limit from the requested duration.
FS_HZ=$(uv run python -c "print(125_000_000 / ${DECIMATION})")
LIMIT_FLAG=""
SAMPLE_LIMIT_REPORT="unlimited"
if [[ -n "$DURATION" ]]; then
  SAMPLE_LIMIT=$(uv run python -c "print(int(round(${DURATION} * ${FS_HZ})))")
  LIMIT_FLAG="-l ${SAMPLE_LIMIT}"
  SAMPLE_LIMIT_REPORT="${SAMPLE_LIMIT}"
fi

# Run dir on the PC. Match the existing UTC_Sa_s naming.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FS_INT=$(uv run python -c "print(int(round(${FS_HZ})))")
RUN_NAME_BASE="${STAMP}_${FS_INT}Sa_s"
RUN_DIR_LOCAL="${REPO_ROOT}/${OUTPUT_DIR}/${RUN_NAME_BASE}"
i=2
while [[ -e "${RUN_DIR_LOCAL}" ]]; do
  RUN_DIR_LOCAL="${REPO_ROOT}/${OUTPUT_DIR}/${RUN_NAME_BASE}_${i}"
  i=$((i+1))
done
mkdir -p "${RUN_DIR_LOCAL}"
RP_RUN_DIR="${RP_WORK}/cap"
ssh_rp "rm -rf '${RP_RUN_DIR}' && mkdir -p '${RP_RUN_DIR}'"

RPSA_ARGV=("-s" "-h" "127.0.0.1" "-f" "bin" "-d" "${RP_RUN_DIR}" "-m" "raw")
[[ -n "$LIMIT_FLAG" ]] && RPSA_ARGV+=("-l" "${SAMPLE_LIMIT}")
RPSA_ARGV_CSV=$(IFS=,; echo "${RPSA_ARGV[*]}")

echo "[5/8] Capture (decimation=${DECIMATION}, Fs=${FS_HZ} Sa/s/ch, limit=${SAMPLE_LIMIT_REPORT})"
# ssh -tt for SIGINT forwarding to rpsa_client.  sshpass -e (env var) is
# required when -t/-tt is used.
INTERRUPTED=0
sshpass -e ssh -tt "${SSH_OPTS[@]}" "root@${HOST}" "${RP_PYLIB}/rpsa_client ${RPSA_ARGV[*]}" \
  || INTERRUPTED=1

# rpsa_client writes data_file_<ip>_<utc>.bin and two log files. We assert
# exactly one .bin in the dir to avoid silently picking the wrong file when
# something unexpected leaves stale data behind.
BIN_LIST=$(ssh_rp "ls -1 '${RP_RUN_DIR}'/data_file_*.bin 2>/dev/null" | tr -d '\r')
BIN_COUNT=$(printf '%s\n' "$BIN_LIST" | grep -c . || true)
if [[ "$BIN_COUNT" -eq 0 ]]; then
  echo "ERROR: rpsa_client produced no .bin file in ${RP_RUN_DIR}" >&2
  ssh_rp "ls -la '${RP_RUN_DIR}' >&2"
  exit 1
fi
if [[ "$BIN_COUNT" -gt 1 ]]; then
  echo "ERROR: rpsa_client left ${BIN_COUNT} .bin files in ${RP_RUN_DIR} (expected exactly 1):" >&2
  printf '  %s\n' $BIN_LIST >&2
  exit 1
fi
ORIG_BIN=$(printf '%s\n' "$BIN_LIST" | head -1)
ssh_rp "mv '${ORIG_BIN}' '${RP_RUN_DIR}/waveform.bin' && \
        mv '${ORIG_BIN}.log.txt' '${RP_RUN_DIR}/waveform.bin.log.txt' && \
        mv '${ORIG_BIN}.log.lost.txt' '${RP_RUN_DIR}/waveform.bin.log.lost.txt'"

echo "[6/8] Pull capture to ${RUN_DIR_LOCAL}"
scp_rp "root@${HOST}:${RP_RUN_DIR}/waveform.bin"              "${RUN_DIR_LOCAL}/" >/dev/null
# The two log files are required for the loss verification in step 7. Do
# NOT swallow scp errors here — a missing log means we cannot prove the
# capture was lossless, and that must surface as a failure not as a
# silent "no loss" verdict.
scp_rp "root@${HOST}:${RP_RUN_DIR}/waveform.bin.log.txt"      "${RUN_DIR_LOCAL}/" >/dev/null
scp_rp "root@${HOST}:${RP_RUN_DIR}/waveform.bin.log.lost.txt" "${RUN_DIR_LOCAL}/" >/dev/null

echo "[7/8] Generate config.json + summary.json (and verify zero loss)"
META_ARGS=(
  "${RUN_DIR_LOCAL}"
  --host "${HOST}"
  --decimation "${DECIMATION}"
  --input-range "${INPUT_RANGE_UPPER}"
  # use --key=value so argparse doesn't mis-parse leading-dash values
  "--rpsa-client-argv=${RPSA_ARGV_CSV}"
  "--streaming-server-config=${SERVER_CFG_JSON}"
)
[[ -n "$DURATION" ]] && META_ARGS+=(--duration "${DURATION}")
[[ "$INTERRUPTED" -eq 1 ]] && META_ARGS+=(--interrupted)
( cd "${REPO_ROOT}" && uv run --with-editable . python -m rp_rin_stream.metadata "${META_ARGS[@]}" )

# Loss check (after summary.json exists so user can inspect it either way)
LOSS_STATE=$(uv run python -c "
import json
s = json.load(open('${RUN_DIR_LOCAL}/summary.json'))
print(s['loss']['has_any_loss'], s['samples_received_per_channel'], s['state'], sep='|')
")
HAS_LOSS="${LOSS_STATE%%|*}"
REST="${LOSS_STATE#*|}"
SAMPLES="${REST%%|*}"
STATE="${REST#*|}"

echo "[8/8] Wipe board /tmp"
ssh_rp "rm -rf '${RP_WORK}' '${RP_PYLIB}'" || true

echo ""
echo "================================================================"
echo "Run dir: ${OUTPUT_DIR}/$(basename ${RUN_DIR_LOCAL})"
echo "Samples per channel:  ${SAMPLES}"
echo "Duration recorded:    $(uv run python -c "print(f'{${SAMPLES}/${FS_HZ}:.3f} s')")"
echo "State:                ${STATE}"
echo "Any sample loss:      ${HAS_LOSS}"
echo "================================================================"

if [[ "${HAS_LOSS}" == "True" ]]; then
  if [[ "${ALLOW_LOSS}" -eq 1 ]]; then
    echo "WARNING: sample loss detected, but --allow-loss was set; exiting OK." >&2
    exit 0
  fi
  echo "ERROR: rpsa_client reported sample loss; data is NOT continuous." >&2
  echo "       See ${RUN_DIR_LOCAL}/summary.json and waveform.bin.log.txt for details." >&2
  echo "       Lower the sample rate (e.g. -d 1024) or pass --allow-loss to suppress this." >&2
  exit 3
fi

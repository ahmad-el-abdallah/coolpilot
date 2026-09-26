#!/usr/bin/env bash
# pcie-watch — live error counter for the CPU <-> RTX 4060 PCIe link (press test).
#
#   ./pcie-watch.sh            keep the GPU busy (glmark2) and watch the link
#   ./pcie-watch.sh --no-load  watch only (GPU may sleep -> link off -> no errors)
#
# The link's 8 lanes run through CPU solder balls, board traces and GPU balls.
# Every corrupted packet the hardware had to resend shows up as "+N" below.
# Press an area of the laptop, hit its number key, and watch whether errors jump.
# Log: ~/crashdiag/logs/<time>-pcie.log  (fsync'd, survives a crash)

# the NVIDIA GPU and the CPU root port it hangs off (addresses differ per laptop)
GPU=
for d in /sys/bus/pci/devices/*; do
  [[ $(cat "$d/vendor") == 0x10de && $(cat "$d/class") == 0x03* ]] && { GPU=$d; break; }
done
[[ -n $GPU ]] || { echo "No NVIDIA GPU found on the PCIe bus"; exit 1; }
ROOT=$(dirname "$(readlink -f "$GPU")")
LOGS="$HOME/crashdiag/logs"; mkdir -p "$LOGS"
LOG="$LOGS/$(date +%Y%m%d-%H%M%S)-pcie.log"
[[ -r $GPU/aer_dev_correctable ]] || { echo "GPU PCIe error counters not readable"; exit 1; }

errs() { awk '/TOTAL_ERR_COR/{print $2}' "$GPU/aer_dev_correctable"; }
rperr() { cat "$ROOT/aer_rootport_total_err_cor" 2>/dev/null || echo 0; }
link() { echo "$(cut -d' ' -f1 "$GPU/current_link_speed")GT/s x$(cat "$GPU/current_link_width")"; }

declare -A AREA=([1]=top-left [2]=top-middle [3]=top-right [4]=palm-left [5]=touchpad
  [6]=palm-right [7]=hinge-left [8]=hinge-right [9]=lift/tilt [0]=not-touching)

LOAD=
if [[ ${1:-} != --no-load ]]; then
  for exe in glmark2 glmark2-wayland glmark2-es2 glmark2-es2-wayland ""; do command -v "$exe" >/dev/null && break; done
  if [[ -n $exe ]]; then
    __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia \
      "$exe" --off-screen --run-forever -s 800x600 >/dev/null 2>&1 &
    LOAD=$!
  else
    echo "glmark2 not installed - watching without GPU load (the GPU may sleep)"
  fi
fi
trap '[[ -n $LOAD ]] && kill $LOAD 2>/dev/null; echo; echo "log: $LOG"; exit' INT TERM

cat <<EOF
== CPU <-> GPU PCIe link watch.  Max link: $(cut -d' ' -f1 "$GPU/max_link_speed")GT/s x$(cat "$GPU/max_link_width")
== Keys: 1 top-left 2 top-middle 3 top-right 4 palm-left 5 touchpad 6 palm-right
==       7 hinge-left 8 hinge-right 9 lift/tilt 0 not touching      Ctrl+C = stop
== Hold still 30 s first (baseline), then press one area at a time for ~15 s.
EOF
echo "time        link          total   +new  gpu-rx-errors" | tee "$LOG"

prev=$(errs); prev_rp=$(rperr); area="-"
while :; do
  read -rsn1 -t 1 k; rc=$?
  if (( rc == 0 )) && [[ -n ${AREA[$k]} ]]; then
    area=${AREA[$k]}
    echo "$(date +%H:%M:%S) >>> now pressing: $area" | tee -a "$LOG"
  elif (( rc > 0 && rc < 128 )); then
    sleep 1  # no keyboard on stdin (EOF): still sample once a second
  fi
  now=$(errs); rp=$(rperr)
  d=$(( now - prev )); drp=$(( rp - prev_rp ))
  line=$(printf "%s  %-12s %7s  %5s  area=%s" "$(date +%H:%M:%S)" "$(link)" "$now" "+$d" "$area")
  (( drp > d )) && line+="  (root port +$drp)"
  echo "$line" >> "$LOG"; sync "$LOG"
  if (( d > 0 )); then printf '\e[1;31m%s  <-- ERRORS\e[0m\n' "$line"; else echo "$line"; fi
  [[ $(cat "$GPU/current_link_width") != "$(cat "$GPU/max_link_width")" ]] &&
    printf '\e[1;33m!!! link width dropped to x%s (lanes lost)\e[0m\n' "$(cat "$GPU/current_link_width")"
  prev=$now; prev_rp=$rp
done

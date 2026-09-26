#!/usr/bin/env bash
# crashdiag — find which component makes the laptop freeze/reset when moved.
#
#   ./crashdiag.sh test <idle|cpu|ram|gpu|disk|all> [minutes]
#       starts the logger + a load on ONE component. While it runs, lift/tilt/
#       move the laptop the way that normally crashes it.
#   ./crashdiag.sh report
#       run after the crash+reboot: shows what was happening just before.
#
# Every log line is fsync'd to disk immediately, so the last line in the
# log is (at most ~1s before) the moment the machine died.

DIR="$HOME/crashdiag"
LOGS="$DIR/logs"
mkdir -p "$LOGS"

hw() { for d in /sys/class/hwmon/hwmon*; do [[ $(<"$d/name") == "$1" ]] && { echo "$d"; return; }; done; }
rd() { [[ -r $1 ]] && cat "$1" 2>/dev/null || echo 0; }
milli() { echo $(( $(rd "$1") / 1000 )); }
# stop a process and everything it started (only our own processes - never match by name)
killtree() { local c; for c in $(pgrep -P "$1"); do killtree "$c"; done; kill "$1" 2>/dev/null; }
# first power supply of a type (Battery / Mains) - names differ per model (BAT0/BAT1, AC0/ACAD/ADP1)
psu() { for d in /sys/class/power_supply/*; do
  [[ $(cat "$d/type" 2>/dev/null) == "$1" && $(cat "$d/scope" 2>/dev/null) != Device ]] && { echo "$d"; return; }
done; }
# glmark2 on the discrete GPU (NVIDIA PRIME offload or DRI_PRIME), whichever build the distro ships
gpu_load() {
  local exe
  for exe in glmark2 glmark2-wayland glmark2-es2 glmark2-es2-wayland ""; do command -v "$exe" >/dev/null && break; done
  [[ -z $exe ]] && { echo "glmark2 not installed - no GPU load"; return; }
  if [[ -e /proc/driver/nvidia/version ]] || command -v nvidia-smi >/dev/null; then
    __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia "$exe" "$@" >/dev/null 2>&1 &
  else
    DRI_PRIME=1 "$exe" "$@" >/dev/null 2>&1 &
  fi
}

monitor() {
  local log="$1" kl="$2"
  local CPU NV ASUS BAT AC
  BAT=$(psu Battery); AC=$(psu Mains)
  CPU=$(hw k10temp); [[ -z $CPU ]] && CPU=$(hw zenpower); [[ -z $CPU ]] && CPU=$(hw coretemp)  # AMD / Intel
  NV=$(hw nvme); ASUS=$(hw asus)
  mapfile -t SPD < <(for d in /sys/class/hwmon/hwmon*; do [[ $(<"$d/name") == spd5118 ]] && echo "$d"; done)

  # kernel messages (PCIe/AER errors, GPU falling off the bus, NVMe resets, MCE...)
  ( journalctl -kf -n 0 -o short-precise 2>&1 | while IFS= read -r l; do
      echo "$l" >> "$kl"; sync "$kl"; done ) &

  echo "time        test  cpuT  cpuMHz load  ramT1 ramT2 ssdT  fan1 fan2  AC batV   batW  gpuT gpuW  gpuMHz" >> "$log"
  while :; do
    local mhz gpu p
    mhz=$(awk '/^cpu MHz/{s+=$4;n++} END{printf "%d", s/n}' /proc/cpuinfo)
    gpu=$(timeout 2 nvidia-smi --query-gpu=temperature.gpu,power.draw,clocks.gr \
            --format=csv,noheader,nounits 2>/dev/null | tr -d ' ' | tr ',' ' ')
    [[ -z $gpu ]] && gpu="ERR ERR ERR"
    p=$(rd $BAT/power_now); [[ $p == 0 ]] && p=$(( $(rd $BAT/current_now) * $(rd $BAT/voltage_now) / 1000000 ))
    printf "%s %-5s %4s  %5s  %-5s %4s  %4s  %4s  %4s %4s  %s  %5.2f  %5.1f %s\n" \
      "$(date +%H:%M:%S.%1N)" "$TEST" \
      "$(milli $CPU/temp1_input)" "$mhz" "$(cut -d' ' -f1 /proc/loadavg)" \
      "$(milli ${SPD[0]}/temp1_input)" "$(milli ${SPD[1]:-/x}/temp1_input)" "$(milli $NV/temp1_input)" \
      "$(rd $ASUS/fan1_input)" "$(rd $ASUS/fan2_input)" "$(rd $AC/online)" \
      "$(awk "BEGIN{print $(rd $BAT/voltage_now)/1e6}")" "$(awk "BEGIN{print $p/1e6}")" \
      "$(awk '{printf "%-4s %-5s %s",$1,$2,$3}' <<<"$gpu")" >> "$log"
    sync "$log"
    sleep 0.5
  done
}

start_load() {
  local t=$1
  case $t in
    idle) ;;
    cpu)  stress-ng --cpu 0 --cpu-method matrixprod -q & ;;
    ram)  stress-ng --vm 4 --vm-bytes 75% --vm-method all --verify -q & ;;
    disk) mkdir -p "$DIR/diskload"
          stress-ng --hdd 2 --hdd-bytes 2G --temp-path "$DIR/diskload" -q & ;;
    gpu)  gpu_load --run-forever -s 1920x1080 ;;
    all)  start_load cpu; start_load gpu; start_load disk ;;
    *) echo "unknown test: $t"; exit 1 ;;
  esac
}

cmd_test() {
  TEST=${1:?usage: crashdiag.sh test <idle|cpu|ram|gpu|disk|all> [minutes]}
  local mins=${2:-15} stamp; stamp=$(date +%Y%m%d-%H%M%S)
  local log="$LOGS/$stamp-$TEST.log" kl="$LOGS/$stamp-$TEST.kernel.log"
  echo "$TEST" > "$DIR/last-test"
  echo "== test: $TEST for $mins min — log: $log"
  echo "== Let it warm up ~2 min, then move/lift/tilt the laptop like when it crashes."
  echo "== Ctrl+C to stop early."
  monitor "$log" "$kl" & local MON=$!
  start_load "$TEST"
  trap 'for p in $(pgrep -P $$); do killtree "$p"; done; rm -rf "$DIR/diskload"; echo; echo "== stopped, survived. log: $log"; echo "survived $TEST $(date)" >> "$DIR/results.txt"; exit' INT TERM
  cat <<'EOF'
== While pressing an area, hit its key (logged with time):
   1 top-left (above keyboard)   2 top-middle   3 top-right
   4 palm rest left              5 touchpad     6 palm rest right
   7 hinge left                  8 hinge right  9 lifting/tilting whole laptop
   0 not touching (stand)
EOF
  local end=$(( SECONDS + mins*60 )) k
  declare -A AREA=([1]=top-left [2]=top-middle [3]=top-right [4]=palm-left [5]=touchpad
    [6]=palm-right [7]=hinge-left [8]=hinge-right [9]=lift/tilt [0]=not-touching)
  while (( SECONDS < end )); do
    if read -rsn1 -t 5 k && [[ -n ${AREA[$k]} ]]; then
      echo "$(date +%H:%M:%S.%1N) >>> MARK $k ${AREA[$k]}" | tee -a "$log"; sync "$log"
    else
      tail -n1 "$log"
    fi
  done
  kill -INT $$
}

cmd_report() {
  local log; log=$(ls -t "$LOGS"/*[a-z].log 2>/dev/null | grep -v kernel | head -1)
  [[ -z $log ]] && { echo "no logs yet"; exit; }
  echo "== last test log: $log"
  head -1 "$log"; tail -n 15 "$log"
  echo; echo "== area marks in this test (last one = where you were pressing at the crash):"
  grep MARK "$log" | tail -n 10 || echo "(none)"
  echo; echo "== kernel messages captured during test:"
  tail -n 30 "${log%.log}.kernel.log" 2>/dev/null
  echo; echo "== end of previous boot's kernel log:"
  journalctl -b -1 -k --no-pager -o short-precise | tail -n 15
  echo; echo "== hardware errors reported at this boot (MCE from the crash show up here):"
  journalctl -b 0 -k --no-pager | grep -iE "hardware error|machine check|mce:|aer|pcie bus error|nvme.*(reset|timeout)|Xid|fallen off" || echo "(none)"
  echo; echo "== previous results:"; cat "$DIR/results.txt" 2>/dev/null
}

case $1 in
  test) shift; cmd_test "$@" ;;
  report) cmd_report ;;
  *) sed -n '2,12p' "$0" ;;
esac

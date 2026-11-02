#!/bin/sh
# PID 1 for the OpenRC test container: bring up the same 3 runlevels a real
# boot would, then stay alive so ci/distro-test.sh can exec into it. Run with
# `docker run --init` so zombies get reaped (there's no systemd here to do it).
openrc sysinit
openrc boot
openrc default
exec tail -f /dev/null

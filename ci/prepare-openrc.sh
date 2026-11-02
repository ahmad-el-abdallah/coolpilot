#!/bin/sh
# Install OpenRC plus what install.sh expects, on an Alpine (or other apk) base
# image. Plain POSIX sh: bash isn't installed yet (this script installs it).
set -eu
apk add --no-cache openrc python3 py3-pip bash util-linux procps eudev shadow curl sqlite
useradd -m tester

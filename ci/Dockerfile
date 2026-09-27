# A distro image that boots systemd, for testing install.sh / uninstall.sh (see ci/distro-test.sh).
ARG IMAGE=debian:stable
FROM ${IMAGE}
COPY prepare.sh /prepare.sh
RUN bash /prepare.sh
STOPSIGNAL SIGRTMIN+3
ENTRYPOINT ["/usr/lib/systemd/systemd"]

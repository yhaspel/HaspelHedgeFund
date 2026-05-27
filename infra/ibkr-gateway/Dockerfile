# IBKR Client Portal Gateway — local Dockerfile wrapping IBKR's `.zip`.
#
# Why this Dockerfile exists (ADR 0011 §1):
#   - IBKR distributes the gateway only as a downloadable `.zip`. There is
#     no official Docker image.
#   - We deliberately COPY the `.zip` from the build context instead of
#     `curl`-ing it: IBKR's distribution URLs have changed historically,
#     no official SHA256 is published, and having the user perform the
#     download keeps IBKR's licensing acceptance with the user (they
#     clicked the EULA, not this Dockerfile).
#
# Before building this image, drop the `.zip` at:
#     infra/ibkr-gateway/dist/clientportal.gw.zip
# The `dist/` directory is gitignored. See `guides/ibkr-gateway.md` for
# the download walkthrough.

FROM eclipse-temurin:17-jre

WORKDIR /opt/clientportal

# The `.zip` MUST be at dist/clientportal.gw.zip in the build context.
# If it's missing, the COPY fails fast with: "COPY failed: file not found".
COPY dist/clientportal.gw.zip /tmp/cpg.zip

# Install unzip, extract the gateway, then remove unzip again to keep the
# image lean. IBKR's distribution unpacks to bin/, dist/, root/ under WORKDIR.
RUN apt-get update \
 && apt-get install -y --no-install-recommends unzip \
 && unzip -q /tmp/cpg.zip -d /opt/clientportal \
 && rm /tmp/cpg.zip \
 && apt-get purge -y --auto-remove unzip \
 && rm -rf /var/lib/apt/lists/* \
 && chmod +x bin/run.sh

# Optional: ship our own minimal conf.yaml that pins the listening port
# and SSL settings to match the docker-compose port mapping. Comment this
# out if you'd rather use the conf.yaml bundled with IBKR's distribution.
COPY conf.yaml root/conf.yaml

EXPOSE 5000

# `run.sh` expects the conf.yaml path relative to WORKDIR.
CMD ["bin/run.sh", "root/conf.yaml"]

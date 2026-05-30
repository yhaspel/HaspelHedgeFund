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
#
# P3a-2 is deferred: the `.zip` is usually absent. This build is therefore
# tolerant — a missing `.zip` logs a warning and produces a placeholder image
# (the IBKR flow stays blocked) instead of failing the build. The service is
# also gated behind the `ibkr` compose profile, so a plain `docker compose up`
# never builds it at all.

FROM eclipse-temurin:17-jre

WORKDIR /opt/clientportal

# Copy the whole dist/ dir (always present — it carries a tracked .gitignore)
# rather than the `.zip` directly, so the COPY can't fail when the BYO `.zip`
# is absent.
COPY dist/ /tmp/cpg/

# Install unzip, then extract the gateway IF the `.zip` was provided. When it's
# missing, warn loudly and skip — the build still succeeds. IBKR's distribution
# unpacks to bin/, dist/, root/ under WORKDIR.
RUN apt-get update \
 && apt-get install -y --no-install-recommends unzip \
 && if [ -f /tmp/cpg/clientportal.gw.zip ]; then \
        echo "ibkr-gateway: extracting clientportal.gw.zip" \
     && unzip -q /tmp/cpg/clientportal.gw.zip -d /opt/clientportal \
     && chmod +x bin/run.sh ; \
    else \
        echo "WARNING: infra/ibkr-gateway/dist/clientportal.gw.zip not found — building a placeholder gateway (IBKR flow stays disabled until you add the .zip; see guides/ibkr-gateway.md)." ; \
    fi \
 && rm -rf /tmp/cpg \
 && apt-get purge -y --auto-remove unzip \
 && rm -rf /var/lib/apt/lists/*

# Optional: ship our own minimal conf.yaml that pins the listening port
# and SSL settings to match the docker-compose port mapping. Comment this
# out if you'd rather use the conf.yaml bundled with IBKR's distribution.
COPY conf.yaml root/conf.yaml

EXPOSE 5000

# If the gateway was extracted, run it; otherwise log the deferred state and
# idle (so the container neither crash-loops under `restart: unless-stopped`
# nor pretends to serve). `run.sh` expects conf.yaml relative to WORKDIR.
CMD ["sh", "-c", "if [ -x bin/run.sh ]; then exec bin/run.sh root/conf.yaml; else echo 'ibkr-gateway unavailable: clientportal.gw.zip was not provided at build time (P3a-2 deferred / IBKR flow blocked).'; exec sleep infinity; fi"]

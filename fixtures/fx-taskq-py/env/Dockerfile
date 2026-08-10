# Prepared image for fx-taskq-py.
#
# Pinned by digest, not by tag: a tag can be repointed, and a benchmark result
# taken against a moving base is not reproducible. Resolve a new digest with
#   docker buildx imagetools inspect python:3.12-slim-bookworm
# and record it in fixture.yaml alongside the rebuild.
#
# The fixture has no third-party runtime dependency, so nothing is resolved from
# a package index at build time and the image contents do not depend on when it
# was built.
ARG BASE_DIGEST
FROM python@${BASE_DIGEST}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    TZ=UTC \
    LC_ALL=C.UTF-8

# Non-root, matching the uid the measure profile runs as.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin agent \
 && mkdir -p /workspace /workspace/scratch \
 && chown -R agent:agent /workspace

# pytest is the only build-time dependency, and only so the clean and witness
# contracts can run. It is pinned so the image does not change under us.
RUN pip install --no-cache-dir --disable-pip-version-check "pytest==8.3.4"

WORKDIR /workspace
COPY --chown=agent:agent tree/ /workspace/
USER agent

CMD ["python3", "-c", "print('fx-taskq-py prepared image')"]

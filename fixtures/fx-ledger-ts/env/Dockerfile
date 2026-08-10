# Prepared image for fx-ledger-ts.
#
# Pinned by digest, not by tag: a tag can be repointed, and a benchmark result
# taken against a moving base is not reproducible. Resolve a new digest with
#   docker buildx imagetools inspect node:22-bookworm-slim
# and record it in fixture.yaml alongside the rebuild.
#
# Unlike fx-taskq-py, this fixture does consult a package registry at build
# time: TypeScript has to compile before the tests can run. `npm ci` is used
# rather than `npm install` because it installs exactly what package-lock.json
# resolved and fails if the lock and the manifest disagree, instead of quietly
# re-resolving. The dependencies are build-time only — nothing in src/ imports
# anything outside the standard library, so the running service still has no
# third-party code in it.
ARG BASE_DIGEST
FROM node@${BASE_DIGEST}

ENV NODE_ENV=development \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    TZ=UTC \
    LC_ALL=C.UTF-8

# Non-root. The node images already ship a user at uid 1000, which is what the
# measure profile pins — it passes `--user 1000:1000`, a numeric id, so the
# account's name is not part of the contract and renaming it to match
# fx-taskq-py would be cosmetic.
RUN mkdir -p /workspace /workspace/scratch \
 && chown -R 1000:1000 /workspace

WORKDIR /workspace

# Dependencies first, so a change to src/ does not re-resolve them.
COPY --chown=1000:1000 tree/package.json tree/package-lock.json /workspace/
USER 1000:1000
RUN npm ci

COPY --chown=1000:1000 tree/ /workspace/

CMD ["node", "-e", "console.log('fx-ledger-ts prepared image')"]

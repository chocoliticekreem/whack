# OverWatch as one free Hugging Face Space (Docker SDK): the backend and the website in a single container.
# This file and README.md are the whole Space; the build clones the code from GitHub, so the Space needs no copy
# of the repo. Rebuild the Space ("Factory rebuild") to pick up new commits on main.
FROM python:3.13-slim-bookworm

ARG REPO=https://github.com/chocoliticekreem/whack
ARG REF=main

# Node 22 for the Next.js site; git and curl for the clone and the launcher.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# Spaces run as uid 1000 and need a writable home: the recorder and the ledger write under data/.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PORT=7860 NEXT_TELEMETRY_DISABLED=1 PYTHONUNBUFFERED=1
WORKDIR /home/user

RUN git clone --depth 1 --branch "$REF" "$REPO" app
WORKDIR /home/user/app/apps/stream
RUN npm ci && npm run build

WORKDIR /home/user/app
EXPOSE 7860
CMD ["bash", "scripts/run-all.sh"]

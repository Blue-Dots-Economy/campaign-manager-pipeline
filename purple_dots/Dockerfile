# Purple Dots loader: Raya calls -> Supabase.
#
#   docker build -t purple-dots .
#   docker run --rm --env-file .env purple-dots --dry-run --batch 3031
#   docker run --rm --env-file .env purple-dots --batch 3031
#
# A batch job, not a service. It runs, writes to Supabase, and exits.
#
# It writes NO files by default. --csv asks for the rows on disk, and only
# then is a mount worth having:
#   docker run --rm --env-file .env -v "$PWD/out:/app/data" purple-dots \
#       --dry-run --batch 3031 --csv
#
# NOTHING SECRET IS BAKED IN
# The three credentials come from the environment at run time. load_purple.py
# calls a bare load_dotenv(), which finds no .env inside the container and
# falls through to the real environment, so --env-file and -e both work:
#   SUPABASE_URL  SUPABASE_SECRET_KEY  RAYA_API_KEY
# .dockerignore keeps .env and data/ out of the build context entirely - a
# secret copied in and deleted later still lives in the layer that added it.

FROM python:3.13-slim

# Fail fast and log straight through: this runs unattended, and buffered
# output means a container that dies tells you nothing about where.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies before source, so editing a transform does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the pipeline. The allow-list lives in .dockerignore.
COPY load_purple.py raya_client.py transform_purple.py ./
COPY sql/ ./sql/

# Only --csv writes here, and nothing does by default. The directory exists
# so that flag works without a mount; unmounted, whatever it writes dies with
# the container - which is the right default for call data that already has a
# home in Supabase.
RUN mkdir -p /app/data

# Not root. The loader needs no privileges - it makes HTTPS requests and
# writes one CSV.
RUN useradd --create-home --uid 10001 purple \
    && chown -R purple:purple /app
USER purple

# Refuses to start without credentials rather than failing mid-run, and
# refuses to point at the Blue Dots project - that guard is in load_purple.py
# and is the reason this image is scoped to one programme.
HEALTHCHECK NONE

ENTRYPOINT ["python", "load_purple.py"]

# A bare `docker run` does nothing destructive. Scoping is deliberate: an
# unscoped load puts back ~950 deleted test rows, so the default is the safe
# one and --batch has to be asked for.
CMD ["--dry-run"]

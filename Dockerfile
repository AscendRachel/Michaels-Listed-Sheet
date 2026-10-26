# Greentek Catalog, production image.
# Same Python as the version it was tested on, packages pinned in constraints.txt, runs as a normal user (not root).
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata: the app stamps times with the server's clock, so the container must know the local time zone (set TZ).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --create-home app \
 && mkdir /data && chown app:app /data

WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt

COPY app ./app
COPY web ./web
COPY run.py VERSION ./

# GT_PORT is the port INSIDE the container (any value works; the proxy is told the same number).
ENV GT_DATA=/data GT_HOST=0.0.0.0 GT_PORT=8000 TZ=America/New_York
USER app
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/ping' % os.environ.get('GT_PORT','8000'), timeout=4)"

# One process on purpose: the database is a single SQLite file, so never run several copies against the same /data.
CMD ["python", "run.py"]

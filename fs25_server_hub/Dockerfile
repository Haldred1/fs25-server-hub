FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
  io.hass.name="FS25 Server Hub" \
  io.hass.description="Live Farming Simulator 25 server dashboard" \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3

WORKDIR /app
COPY app.py /app/app.py
COPY migration.py /app/migration.py
COPY setup.py /app/setup.py
COPY web /app/web
COPY run.sh /run.sh
RUN chmod a+x /run.sh /app/app.py /app/migration.py /app/setup.py

CMD ["/run.sh"]

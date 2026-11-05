# Caddy with the built app. Build from the repo root (infra/docker-compose.staging.yml does).
FROM node:26.13.0-alpine AS build

WORKDIR /app/app
COPY app/package.json app/package-lock.json ./
RUN npm ci
COPY app/ ./
# tsc also checks the tests, which compare the mocks to these files.
COPY config/clients/ /app/config/clients/
# VITE_DEMO_MODE from infra/.env.staging. Vite hands VITE_* variables to the app, which reads
# this one from T2-17 on; until then it changes nothing.
ARG VITE_DEMO_MODE=false
ENV VITE_DEMO_MODE=$VITE_DEMO_MODE
RUN npm run build

FROM caddy:2.10.2-alpine

COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/app/dist /srv

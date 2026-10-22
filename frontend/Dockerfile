# UI image for the live server: the static export (ADR 017) inside Caddy, which also
# terminates TLS and proxies /api to the backend (infra/deploy/Caddyfile, mounted by
# compose). NEXT_PUBLIC_API_URL is left unset so the bundle calls /api on its own origin.

FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build


FROM caddy:2-alpine
COPY --from=build /app/out /srv

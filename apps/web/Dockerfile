# Build-Kontext ist das Repo-Root (siehe docker-compose.yml/.dev.yml), damit
# die pnpm-Workspace-Pakete packages/design-tokens und packages/contracts
# eingebunden werden koennen.

FROM node:20-alpine AS base
RUN corepack enable
WORKDIR /app

FROM base AS deps
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml tsconfig.base.json ./
COPY apps ./apps
COPY packages ./packages
RUN pnpm install --frozen-lockfile --filter @finanzplaner/web...

# Dev: services/api-artig per docker-compose.dev.yml ueber Volumes
# gemountet, hier nur die Dependencies vorinstalliert. Command/Ports werden
# von docker-compose.dev.yml gesetzt.
FROM deps AS dev

FROM deps AS build
ARG VITE_API_BASE_URL=/
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_SUPABASE_URL=$VITE_SUPABASE_URL \
    VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY
RUN pnpm --filter @finanzplaner/web build

# Runtime: statische Auslieferung ueber Caddy (schlanker als ein separater
# Node/nginx-Prozess, konsistent mit infra/caddy/Caddyfile).
FROM caddy:2-alpine AS runtime
COPY apps/web/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/apps/web/dist /usr/share/caddy
EXPOSE 80

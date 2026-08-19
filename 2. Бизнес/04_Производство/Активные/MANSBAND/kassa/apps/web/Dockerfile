# Сборка фронта в монорепо (контекст = корень kassa/, нужен packages/shared),
# затем раздача статики nginx + проксирование /api и /ws на сервис api.
FROM node:20-slim AS build
WORKDIR /app
RUN corepack enable

COPY package.json pnpm-workspace.yaml tsconfig.base.json ./
COPY packages/shared/package.json packages/shared/
COPY apps/web/package.json apps/web/
RUN pnpm install --no-frozen-lockfile

COPY packages/shared packages/shared
COPY apps/web apps/web

ARG VITE_API_BASE=/api
ENV VITE_API_BASE=${VITE_API_BASE}
# VITE_WS_BASE не задаём — в коде дефолт wss://host/ws (пустая строка ломала WS)
RUN pnpm --filter @kassa/shared build \
  && pnpm --filter @kassa/web build

FROM nginx:1.27-alpine AS runtime
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
EXPOSE 80

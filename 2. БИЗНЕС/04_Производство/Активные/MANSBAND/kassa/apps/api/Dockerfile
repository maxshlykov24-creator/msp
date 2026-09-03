# Сборка API в монорепо. Контекст сборки = корень kassa/ (нужен packages/shared).
FROM node:20-slim AS build
WORKDIR /app
RUN corepack enable

# Манифесты (для кэша установки зависимостей)
COPY package.json pnpm-workspace.yaml tsconfig.base.json ./
COPY packages/shared/package.json packages/shared/
COPY apps/api/package.json apps/api/
RUN pnpm install --no-frozen-lockfile

# Исходники
COPY packages/shared packages/shared
COPY apps/api apps/api

# Собираем shared, генерируем миграции из схемы, собираем api
RUN pnpm --filter @kassa/shared build \
  && pnpm --filter @kassa/api db:generate \
  && pnpm --filter @kassa/api build

# Прод-зависимости отдельным слоем
RUN pnpm --filter @kassa/api --prod deploy /app/deploy-api

FROM node:20-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
# Готовый набор прод-зависимостей + собранный код
COPY --from=build /app/deploy-api/node_modules ./node_modules
COPY --from=build /app/apps/api/dist ./dist
COPY --from=build /app/apps/api/drizzle ./drizzle
COPY --from=build /app/apps/api/package.json ./package.json
COPY --from=build /app/packages/shared/dist ./node_modules/@kassa/shared/dist
COPY --from=build /app/packages/shared/package.json ./node_modules/@kassa/shared/package.json

EXPOSE 3001
CMD ["node", "dist/index.js"]

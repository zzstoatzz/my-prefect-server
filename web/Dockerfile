FROM docker.io/oven/bun:1-slim
WORKDIR /app
COPY package.json bun.lockb ./
RUN bun install --frozen-lockfile
COPY . .
RUN bun run build
EXPOSE 3000
ENV NODE_ENV=production
ENV DUCKDB_PATH=/analytics/hub.duckdb
CMD ["bun", "build/index.js"]

FROM docker.io/oven/bun:1-slim
WORKDIR /repo/web
COPY web/package.json web/bun.lockb ./
RUN bun install --frozen-lockfile
COPY web/ .
COPY packages/mps/src/mps/projects.json /repo/packages/mps/src/mps/projects.json
RUN bun run build
EXPOSE 3000
ENV NODE_ENV=production
ENV DUCKDB_PATH=/analytics/hub.duckdb
CMD ["bun", "build/index.js"]

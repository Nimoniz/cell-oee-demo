# One image, runnable against any backend: API_URL is read at container start (see
# src/lib/runtimeConfig.ts), never baked in at build time.

FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
# No backend is reachable at build time, so types.gen.ts is committed and used as-is.
RUN npm run build

FROM node:22-slim AS run
WORKDIR /app
# The standalone server otherwise binds only to the container's own network interface, not
# 127.0.0.1 — the healthcheck below (and anything else probing localhost inside the container)
# would fail even though the published port works fine from the host.
ENV NODE_ENV=production HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://localhost:3000/live').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]

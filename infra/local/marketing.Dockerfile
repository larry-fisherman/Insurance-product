FROM node:26-bookworm-slim AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
COPY apps/marketing/package.json ./apps/marketing/package.json
RUN npm ci --no-audit --no-fund --loglevel=http

FROM dependencies AS build
ARG APPLICATION_ORIGIN=http://localhost:3000
ARG MARKETING_ORIGIN=http://127.0.0.1:3001
ENV APPLICATION_ORIGIN=$APPLICATION_ORIGIN MARKETING_ORIGIN=$MARKETING_ORIGIN NEXT_TELEMETRY_DISABLED=1
COPY apps/marketing ./apps/marketing
COPY packages/web-config ./packages/web-config
RUN npm run build --workspace @cgl/marketing

FROM node:26-bookworm-slim AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3001
WORKDIR /app
COPY --from=build --chown=node:node /app/apps/marketing/.next/standalone ./
COPY --from=build --chown=node:node /app/apps/marketing/.next/static ./apps/marketing/.next/static
USER node
EXPOSE 3001
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 \
 CMD node -e "require('node:http').get('http://127.0.0.1:3001',{headers:{host:new URL(process.env.MARKETING_ORIGIN || 'http://127.0.0.1:3001').host}},r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"
CMD ["node", "apps/marketing/server.js"]

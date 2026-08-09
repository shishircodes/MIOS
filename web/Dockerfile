# TanStack Start SSR server for the MIOS dashboard.
#
# Build from the web/ directory:
#   docker build --build-arg VITE_API_BASE=https://api.example.com -t mios-web web/
#
# IMPORTANT: VITE_API_BASE is a *build-time* value. Vite inlines import.meta.env
# into the bundle, so it cannot be changed by setting an environment variable on
# the running container — every deployment target needs its own build. That is
# why the preview workflow rebuilds this image per PR rather than reusing one.

FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Baked into the client bundle at this point, not at runtime.
ARG VITE_API_BASE=http://localhost:8787
ENV VITE_API_BASE=$VITE_API_BASE

RUN npm run build


FROM node:22-alpine AS runtime

WORKDIR /app
ENV NODE_ENV=production

# `vite build` emits dist/{client,server}; dist/server/server.js is a Web-Fetch
# handler with no listener, so server.js binds it to a Node HTTP server. That
# needs srvx at runtime, hence the production-only install.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=build /app/dist ./dist
COPY server.js ./

RUN addgroup -g 10001 mios && adduser -u 10001 -G mios -D mios
USER mios

EXPOSE 3000
ENV PORT=3000 HOST=0.0.0.0

# `/` 307s to /monitor/digest, so treat any non-5xx as healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -qS --spider http://127.0.0.1:3000/signin 2>&1 | grep -q "HTTP/1.1 200" || exit 1

CMD ["node", "server.js"]

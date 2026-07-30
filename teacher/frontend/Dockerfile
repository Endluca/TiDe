# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS build

ARG VITE_API_BASE_URL
ARG VITE_PUBLIC_ASSET_BASE_URL

ENV PNPM_HOME=/pnpm
ENV PATH=${PNPM_HOME}:${PATH}
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_PUBLIC_ASSET_BASE_URL=${VITE_PUBLIC_ASSET_BASE_URL}

RUN corepack enable \
  && corepack prepare pnpm@11.13.1 --activate

WORKDIR /app

COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=tide-frontend-pnpm,target=/pnpm/store \
  pnpm install --frozen-lockfile

COPY index.html ./
COPY public ./public
COPY scripts ./scripts
COPY src ./src

RUN pnpm run build:nginx

FROM nginx:1.27-alpine AS runtime

RUN rm -f /etc/nginx/conf.d/default.conf

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=nginx:nginx /app/dist /usr/share/nginx/html

USER nginx

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["wget", "-q", "-T", "3", "-O", "-", "http://127.0.0.1:8080/healthz"]

STOPSIGNAL SIGQUIT

CMD ["nginx", "-g", "daemon off;"]

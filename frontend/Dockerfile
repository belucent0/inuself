FROM node:20-alpine AS base

# 의존성 설치 단계
FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --package-lock-only && npm ci

# 빌드 단계
FROM base AS builder
WORKDIR /app

# 빌드 타임 환경변수 (VITE_*는 빌드 시 번들에 포함됨)
ARG VITE_API_URL=/api
ARG VITE_GRAFANA_DASHBOARD_URL
ARG VITE_GRAFANA_LOKI_URL

ENV VITE_API_URL=${VITE_API_URL}
ENV VITE_GRAFANA_DASHBOARD_URL=${VITE_GRAFANA_DASHBOARD_URL}
ENV VITE_GRAFANA_LOKI_URL=${VITE_GRAFANA_LOKI_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Vite 빌드
RUN npm run build

# 프로덕션 단계 - nginx로 정적 파일 서빙
FROM nginx:1.25-alpine AS runner

# nginx 설정
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 빌드된 정적 파일 복사
COPY --from=builder /app/dist /usr/share/nginx/html

# 비 root 사용자로 실행
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nextjs -u 1001 && \
    chown -R nextjs:nodejs /usr/share/nginx/html && \
    chown -R nextjs:nodejs /var/cache/nginx && \
    chown -R nextjs:nodejs /var/log/nginx && \
    touch /var/run/nginx.pid && \
    chown -R nextjs:nodejs /var/run/nginx.pid

USER nextjs

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]

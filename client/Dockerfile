FROM node:20-alpine AS base

# 의존성 설치 단계
FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# package-lock.json이 package.json과 불일치할 수 있으므로 npm install 사용
# (npm ci는 정확한 일치 필요, npm install은 자동 동기화)
RUN npm install --package-lock-only && npm ci

# 빌드 단계
FROM base AS builder
WORKDIR /app

# 빌드 타임 환경변수 (NEXT_PUBLIC_*는 빌드 시 번들에 포함됨)
ARG NEXT_PUBLIC_ADMIN_USERNAME=admin
ARG NEXT_PUBLIC_ADMIN_PASSWORD=admin123
ARG NEXT_PUBLIC_API_BASE_URL=/api

ENV NEXT_PUBLIC_ADMIN_USERNAME=${NEXT_PUBLIC_ADMIN_USERNAME}
ENV NEXT_PUBLIC_ADMIN_PASSWORD=${NEXT_PUBLIC_ADMIN_PASSWORD}
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# public 디렉토리가 없으면 빈 디렉토리 생성 (Next.js 빌드에 필요)
RUN mkdir -p public

# Next.js 빌드
RUN npm run build

# 프로덕션 단계
FROM base AS runner
WORKDIR /app

ENV NODE_ENV production

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# public 디렉토리 복사 (빌드 단계에서 생성됨)
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

ENV PORT 3000
ENV HOSTNAME "0.0.0.0"

CMD ["node", "server.js"]


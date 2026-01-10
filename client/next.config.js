// 루트 .env 파일 로드
require('dotenv').config({ path: require('path').resolve(__dirname, '../.env') })

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // 루트 .env의 환경변수를 Next.js에 주입
  env: {
    NEXT_PUBLIC_GRAFANA_DASHBOARD_URL: process.env.NEXT_PUBLIC_GRAFANA_DASHBOARD_URL,
  },
  // 개발 모드에서 외부 도메인 접근 허용
  allowedDevOrigins: process.env.ALLOWED_DEV_ORIGINS
    ? process.env.ALLOWED_DEV_ORIGINS.split(',')
    : ['asr.timblo.io'],
  turbopack: {}, // Webpack 설정 사용
  webpack: (config, { isServer }) => {
    // react-pdf 설정
    if (!isServer) {
      config.resolve.alias = {
        ...config.resolve.alias,
        canvas: false,
      }
    }
    return config
  },
  async rewrites() {
    // 프로덕션은 nginx가 처리하므로 불필요
    if (process.env.NODE_ENV === 'production') {
      return []
    }

    // 개발 환경 API 프록시
    const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
    return [
      {
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
      {
        source: '/ws/:path*',
        destination: `${backendUrl}/ws/:path*`,
      },
      {
        source: '/media/:path*',
        destination: 'http://localhost:9000/asr-media/:path*',
      },
      {
        source: '/grafana/:path*',
        destination: 'http://localhost:3002/:path*',
      },
      {
        source: '/public/:path*',
        destination: 'http://localhost:3002/public/:path*',
      },
    ]
  },
}

module.exports = nextConfig



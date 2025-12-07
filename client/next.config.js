/** @type {import('next').NextConfig} */
const nextConfig = {
  // Server Actions are available by default in Next.js 14+
  // experimental.serverActions 옵션은 더 이상 필요하지 않습니다
  output: 'standalone',
  // 개발 모드에서 외부 도메인에서 접근할 때 허용할 오리진 설정
  allowedDevOrigins: process.env.ALLOWED_DEV_ORIGINS 
    ? process.env.ALLOWED_DEV_ORIGINS.split(',')
    : ['asr.timblo.io'],
  // Next.js 16에서 Turbopack이 기본이지만, webpack 설정이 있으므로 webpack 사용 명시
  // 빈 turbopack 설정을 추가하여 webpack 사용을 명시적으로 지정
  turbopack: {},
  webpack: (config, { isServer }) => {
    // react-pdf를 위한 설정 (canvas는 서버 사이드에서만 false로 설정)
    if (!isServer) {
      config.resolve.alias = {
        ...config.resolve.alias,
        canvas: false,
      }
    }
    return config
  },
}

module.exports = nextConfig



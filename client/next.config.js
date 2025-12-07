/** @type {import('next').NextConfig} */
const nextConfig = {
  // Server Actions are available by default in Next.js 14+
  // experimental.serverActions 옵션은 더 이상 필요하지 않습니다
  output: 'standalone',
  // 개발 모드에서 외부 도메인에서 접근할 때 허용할 오리진 설정
  allowedDevOrigins: process.env.ALLOWED_DEV_ORIGINS 
    ? process.env.ALLOWED_DEV_ORIGINS.split(',')
    : ['asr.timblo.io'],
}

module.exports = nextConfig



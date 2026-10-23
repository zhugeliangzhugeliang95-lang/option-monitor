import type { NextConfig } from 'next';
const config: NextConfig = {async rewrites(){return [{source:'/api/:path*',destination:`http://127.0.0.1:${process.env.BACKEND_PORT||'8000'}/api/:path*`}]}};
export default config;

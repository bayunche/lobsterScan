/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination:
          (process.env.NEXT_PUBLIC_WEB_API_BASE || "http://localhost:8000") +
          "/api/:path*",
      },
    ];
  },
  webpack: (config, { dev }) => {
    // WSL2 + /mnt/c/ 挂载目录 chokidar 经常收不到 fs notify,改用 polling 触发热重载
    if (dev) {
      config.watchOptions = {
        poll: 800,
        aggregateTimeout: 200,
        ignored: ["**/node_modules", "**/.next"],
      };
    }
    return config;
  },
};
module.exports = nextConfig;

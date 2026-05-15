/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/admin/api/:path*",
        destination:
          (process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100") +
          "/admin/api/:path*",
      },
    ];
  },
};
module.exports = nextConfig;

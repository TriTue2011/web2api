# Dockerfile cho Dịch vụ Zalo cá nhân (NV172) — Node + zca-js trong container phụ.
# Tệp MỚI trong dich-vu/zalo-ca-nhan/ — KHÔNG sửa Dockerfile/docker-compose.yml gốc.
# Chưa dựng thử trong môi trường hộp cát (không có Docker daemon).
#
# Ảnh nền GHIM DIGEST thật (lấy từ registry-1.docker.io ngày 20/10/2026 cho tag
# node:22-alpine): sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402
# Phụ thuộc npm: `npm ci` theo package-lock.json đã ghim (zca-js 2.1.2 +
# integrity sha512 trong lock); KHÔNG chạy script postinstall của gói lạ nào
# ngoài cây phụ thuộc đã khoá — kiểm package-lock.json trước khi dựng.

FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402

ENV NODE_ENV=production \
    ZALO_PORT=8703 \
    ZALO_HOST=127.0.0.1 \
    ZALO_DATA=/data

WORKDIR /app

# Người dùng không root dùng sẵn trong ảnh node (uid 1000 «node»).
RUN mkdir -p /data /app && chown node:node /data /app && chmod 0700 /data

COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

COPY --chown=node:node server.js zalo-client.js ./

USER node

EXPOSE 8703

# ZALO_KHOA truyền lúc chạy (docker run -e ZALO_KHOA=...), KHÔNG ghi vào ảnh.
# Dịch vụ chỉ nghe loopback trong container; cổng không publish ra ngoài —
# module web2api gọi qua mạng nội bộ Docker hoặc cùng máy.
CMD ["node", "server.js"]

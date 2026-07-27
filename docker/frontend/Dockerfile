FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY certs/ /etc/nginx/certs/

EXPOSE 443

# https-only: certs/ ships a self-signed cert so there's no insecure http
# fallback to forget to remove before a real deployment. busybox wget
# supports --no-check-certificate for the self-signed cert.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD wget -q --no-check-certificate -O /dev/null https://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]

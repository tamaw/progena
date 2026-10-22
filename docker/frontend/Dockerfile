FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD ["sh", "-c", "wget -q -O /dev/null http://127.0.0.1/ || exit 1"]

CMD ["nginx", "-g", "daemon off;"]

FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
RUN npm ci
COPY apps/web ./apps/web
ENV NEXT_TELEMETRY_DISABLED=1
ENV API_URL=http://api:8000
RUN npm run build
EXPOSE 3000
CMD ["npm", "run", "start", "--workspace", "apps/web"]
